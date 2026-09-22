"""
tests/test_causal_real_callers.py — La traza está conectada al recorrido REAL.

POR QUÉ HACE FALTA
------------------
`record_application()` y `record_outcome()` existían, tenían pruebas y no las
llamaba nadie en producción. Una traza causal que solo se escribe desde el
banco de pruebas no demuestra nada: el informe podía afirmar "el ciclo está
conectado" sin que ninguna operación real hubiese escrito jamás una fila.

Estas pruebas comprueban DOS cosas distintas, y las dos hacen falta:

1. Que existen llamadores REALES en código de producción, verificado por AST
   sobre el árbol — no por lectura ni por confianza.
2. Que el recorrido completo funciona de extremo a extremo:
   `convergence_id -> learning_id -> criterion_version -> decision_id ->
   application_id -> outcome_id`.

Y una tercera que es un límite, no una función: aprender NO concede ejecutar.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.learn import causal_learning as cl  # noqa: E402

_LEARNING_ENGINE = _ROOT / "connectors" / "etoro" / "learning_engine.py"
_POSITION_MANAGER = _ROOT / "connectors" / "etoro" / "position_manager.py"
_VALIDATOR = _ROOT / "connectors" / "etoro" / "entry_validator.py"


def _calls_in(path: Path) -> set:
    """Nombres de función invocados en un archivo, por AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            names.add(fn.attr if isinstance(fn, ast.Attribute)
                      else getattr(fn, "id", ""))
    return names


def _imports_in(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


# ===========================================================================
# 1. Los llamadores existen en producción
# ===========================================================================

class TestProductionCallersExist:

    def test_the_executor_records_abstentions_and_applications(self):
        assert "core.learn.causal_learning.record_decision" in _imports_in(
            _LEARNING_ENGINE
        ), "ningún código de producción registra aplicaciones causales"
        assert "record_decision" in _calls_in(_LEARNING_ENGINE)

    def test_the_position_closer_resolves_the_outcome(self):
        assert "core.learn.causal_learning.resolve_decision_outcome" in _imports_in(
            _POSITION_MANAGER
        ), "ningún código de producción cierra el resultado causal"
        assert "resolve_decision_outcome" in _calls_in(_POSITION_MANAGER)

    def test_both_branches_of_the_validation_are_traced(self):
        """Bloqueada -> abstención; ejecutada -> aplicación."""
        text = _LEARNING_ENGINE.read_text(encoding="utf-8")
        assert "_record_causal_decision(\n                    _causal, p, mode, applied=False," in text
        assert "_record_causal_decision(_causal, p, mode, applied=True)" in text

    def test_the_decision_id_is_the_same_on_both_ends(self):
        """`proposal_id` al aplicar y al resolver: sin vínculo inventado."""
        assert "decision_id=proposal.proposal_id" in _LEARNING_ENGINE.read_text(
            encoding="utf-8"
        )
        assert "resolve_decision_outcome(\n                trade.proposal_id," in (
            _POSITION_MANAGER.read_text(encoding="utf-8")
        )


# ===========================================================================
# 2. El recorrido completo, de extremo a extremo
# ===========================================================================

class TestTheWholeChain:

    @pytest.fixture
    def learned(self, tmp_path, monkeypatch):
        db = str(tmp_path / "causal.db")
        monkeypatch.setattr(cl, "default_db_path", lambda: db)
        cl.ensure_production_policies(db_path=db)
        decision = cl.evaluate_convergence(
            cl.CausalSnapshot(
                convergence_id="CONV-AAPL", domain="market",
                source_pattern_ids=["market:AAPL", "freight:L7"],
                evidence_ids=["EV-1"],
                metrics={"combined_cc": 0.9, "combined_hits": 9},
                claim="market:AAPL converge con freight:L7",
            ),
            stats_fetcher=lambda fp: {
                "win_rate": 0.8, "expectancy": 0.6,
                "confidence": 1.0, "sample_size": 20.0,
            },
            db_path=db,
        )
        assert decision.state == cl.STATE_LEARNED
        return db, decision

    def test_execution_records_a_single_application(self, learned):
        db, decision = learned
        ids = cl.record_decision(
            [decision.learning_id], decision_id="PROP-1", applied=True,
            action_type="trade_execution", execution_scope="paper", db_path=db,
        )
        assert len(ids) == 1
        app = cl.get_applications(learning_id=decision.learning_id, db_path=db)[0]
        assert app["applied"] is True
        assert app["decision_id"] == "PROP-1"
        assert app["execution_scope"] == "paper"
        assert app["criterion_version"].startswith("crit-")

    def test_retrying_the_cycle_does_not_duplicate(self, learned):
        db, decision = learned
        for _ in range(5):
            cl.record_decision(
                [decision.learning_id], decision_id="PROP-1", applied=True,
                db_path=db,
            )
        assert len(cl.get_applications(
            learning_id=decision.learning_id, db_path=db)) == 1

    def test_a_blocked_validation_records_an_abstention(self, learned):
        db, decision = learned
        cl.record_decision(
            [decision.learning_id], decision_id="PROP-2", applied=False,
            action_type="trade_blocked", execution_scope="paper",
            abstained_reason="Sin patrón usable para AAPL/buy.", db_path=db,
        )
        app = cl.get_applications(learning_id=decision.learning_id, db_path=db)[0]
        assert app["applied"] is False
        assert "Sin patrón usable" in app["abstained_reason"]

    def test_paper_and_live_are_distinguished(self, learned):
        db, decision = learned
        cl.record_decision([decision.learning_id], decision_id="P-PAPER",
                           applied=True, execution_scope="paper", db_path=db)
        cl.record_decision([decision.learning_id], decision_id="P-LIVE",
                           applied=True, execution_scope="live", db_path=db)
        scopes = {
            a["decision_id"]: a["execution_scope"]
            for a in cl.get_applications(learning_id=decision.learning_id, db_path=db)
        }
        assert scopes == {"P-PAPER": "paper", "P-LIVE": "live"}

    def test_the_closing_cycle_updates_that_same_application(self, learned):
        db, decision = learned
        cl.record_decision([decision.learning_id], decision_id="PROP-3",
                           applied=True, execution_scope="paper", db_path=db)
        outs = cl.resolve_decision_outcome(
            "PROP-3", outcome_status="closed_win", outcome_value=12.5, db_path=db,
        )
        assert len(outs) == 1
        app = cl.get_applications(learning_id=decision.learning_id, db_path=db)[0]
        assert app["outcome_id"] == outs[0]
        assert app["outcome_status"] == cl.OUTCOME_WIN
        assert app["outcome_value"] == 12.5

    def test_a_losing_close_weakens_the_learning_immediately(self, learned):
        db, decision = learned
        cl.record_decision([decision.learning_id], decision_id="PROP-4",
                           applied=True, execution_scope="paper", db_path=db)
        cl.resolve_decision_outcome(
            "PROP-4", outcome_status="closed_loss", outcome_value=-8.0, db_path=db,
        )
        assert cl.get_learning(decision.learning_id, db_path=db)["state"] == (
            cl.STATE_WEAKENED
        )
        assert cl.consumable_learnings("market", db_path=db) == []

    def test_an_abstention_is_never_given_an_outcome(self, learned):
        db, decision = learned
        cl.record_decision([decision.learning_id], decision_id="PROP-5",
                           applied=False, abstained_reason="halt", db_path=db)
        assert cl.resolve_decision_outcome(
            "PROP-5", outcome_status="win", db_path=db,
        ) == []

    def test_the_chain_is_reconstructible_from_the_outcome_backwards(self, learned):
        db, decision = learned
        cl.record_decision([decision.learning_id], decision_id="PROP-6",
                           applied=True, execution_scope="paper", db_path=db)
        cl.resolve_decision_outcome("PROP-6", outcome_status="win", db_path=db)

        app = cl.get_applications(learning_id=decision.learning_id, db_path=db)[0]
        learning = cl.get_learning(app["learning_id"], db_path=db)
        assert app["outcome_id"]
        assert app["decision_id"] == "PROP-6"
        assert app["criterion_version"] == learning["criterion_version"]
        assert learning["source_convergence_id"] == "CONV-AAPL"
        assert learning["source_pattern_ids"] == ["market:AAPL", "freight:L7"]


# ===========================================================================
# 3. Sin aprendizaje no se inventa nada
# ===========================================================================

class TestNothingIsInvented:

    def test_no_learning_means_no_application(self, tmp_path):
        db = str(tmp_path / "c.db")
        assert cl.record_decision([], decision_id="PROP-X", applied=True,
                                  db_path=db) == []
        assert cl.get_applications(db_path=db) == []

    def test_domains_without_an_executor_declare_it(self):
        assert cl.operational_consumer("market") != cl.NO_OPERATIONAL_CONSUMER
        for domain in ("freight_logistics", "florida_real_estate", "cybersecurity"):
            assert cl.operational_consumer(domain) == cl.NO_OPERATIONAL_CONSUMER, (
                f"{domain} declara un ejecutor que no existe"
            )

    def test_the_market_consumer_is_the_real_module(self):
        consumer = cl.operational_consumer("market")
        assert consumer == (
            "connectors.etoro.learning_engine._auto_execute_proposals"
        )
        tree = ast.parse(_LEARNING_ENGINE.read_text(encoding="utf-8"))
        names = {
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert "_auto_execute_proposals" in names, (
            "el consumidor declarado no existe en el módulo que se nombra"
        )


# ===========================================================================
# 4. El límite: aprender no concede ejecutar
# ===========================================================================

class TestLearningStillGrantsNoExecution:

    def test_the_trace_runs_after_the_decision_not_before(self):
        """Si se registrara antes, podría influir en `allowed`."""
        text = _LEARNING_ENGINE.read_text(encoding="utf-8")
        validate_at = text.index("allowed, reasons = validate_entry(")
        causal_at = text.index("_causal = convergence_evidence(")
        assert validate_at < causal_at, (
            "la evidencia causal se lee antes de validar: eso la mete en la decisión"
        )

    def test_the_tracer_touches_no_control(self):
        tree = ast.parse(_LEARNING_ENGINE.read_text(encoding="utf-8"))
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_record_causal_decision"
        )
        # Solo IDENTIFICADORES, no el volcado completo: el docstring explica en
        # prosa que estos controles NO se tocan, y buscarlo ahí daría un falso
        # positivo sobre su propia documentación.
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
        for forbidden in ("update_config", "_set_mode", "unhalt", "approve_symbol",
                          "activate_live", "max_ops", "capital", "halt"):
            assert forbidden not in names, (
                f"el trazador causal toca un control: {forbidden}"
            )

    def test_the_tracer_never_propagates_a_failure(self):
        """Una traza rota no puede impedir ni provocar una operación."""
        tree = ast.parse(_LEARNING_ENGINE.read_text(encoding="utf-8"))
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_record_causal_decision"
        )
        assert any(isinstance(n, ast.Try) for n in ast.walk(fn))

    def test_the_outcome_hook_never_propagates_a_failure(self):
        tree = ast.parse(_POSITION_MANAGER.read_text(encoding="utf-8"))
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_close_paper_trade"
        )
        calls = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", "") == "resolve_decision_outcome"
        ]
        assert calls, "el cierre no resuelve el resultado causal"
        tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
        assert any(
            any(c in ast.walk(t) for c in calls) for t in tries
        ), "la resolución causal no está aislada: un fallo suyo rompería el cierre"
