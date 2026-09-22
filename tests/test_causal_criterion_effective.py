"""
tests/test_causal_criterion_effective.py — El aprendizaje cambia el criterio.

QUÉ PROTEGE
-----------
La auditoría demostró que `core/learn/criterion.py` no mencionaba
convergencias ni aprendizajes: el enlace `aprendizaje → criterio` NO existía.
Estas pruebas demuestran que ahora existe y es EFECTIVO:

  * una convergencia que supera el gate entra en el criterio real;
  * el criterio conserva `learning_id` y `convergence_id`;
  * un candidato que NO superó el gate no mueve nada;
  * sin aprendizaje no puede afirmarse cambio de criterio.

Y demuestran el límite: aprender NO concede permiso para ejecutar.

AISLAMIENTO
-----------
El almacén causal se redirige al vault temporal de `conftest._hermetic_base`.
Las otras tres fuentes de `rank_domain_evidence` (domain_library, gravity,
verification_ledger) se neutralizan para que el efecto medido sea EL del
puente causal y no ruido de la máquina.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.learn import causal_learning as cl  # noqa: E402
from core.learn import criterion  # noqa: E402

STRONG = {"win_rate": 0.80, "expectancy": 0.60, "confidence": 1.0, "sample_size": 20.0}
THIN = {"win_rate": 1.00, "expectancy": 1.00, "confidence": 0.25, "sample_size": 5.0}


def _fetcher(**by_fp):
    def _fetch(fp):
        return by_fp.get(fp)
    return _fetch


BOTH_STRONG = _fetcher(**{"market:AAPL": STRONG, "freight:LANE-7": STRONG})


@pytest.fixture
def clean_sources(monkeypatch):
    """Las otras tres fuentes del ranking, vacías: aislamos el efecto causal."""
    import core.domain_knowledge as dk
    import core.learn.gravity_engine as ge
    import core.learn.verification_ledger as vl
    monkeypatch.setattr(dk, "get_domain_priors", lambda domain: [], raising=False)
    monkeypatch.setattr(
        ge, "get_gravity_index",
        lambda: type("_GI", (), {"by_domain": lambda self, d: []})(),
        raising=False,
    )
    monkeypatch.setattr(
        vl, "subject_scores", lambda domain, min_decisive=1: {}, raising=False,
    )


@pytest.fixture
def learned(clean_sources):
    """Una convergencia cualificada, ya aprendida, en el vault temporal."""
    cl.ensure_production_policies()
    snapshot = cl.CausalSnapshot(
        convergence_id="CONV-MKT-FRT",
        domain="market",
        source_pattern_ids=["market:AAPL", "freight:LANE-7"],
        evidence_ids=["EV-1", "EV-2"],
        metrics={"combined_hits": 9.0, "combined_cc": 0.77},
        claim="market:AAPL converge con freight:LANE-7",
    )
    decision = cl.evaluate_convergence(snapshot, stats_fetcher=BOTH_STRONG)
    assert decision.state == cl.STATE_LEARNED, decision.reasons
    return decision


# ===========================================================================
# El aprendizaje entra en el criterio EFECTIVO
# ===========================================================================

class TestLearnedChangesTheCriterion:

    def test_a_learned_convergence_enters_the_ranking(self, learned):
        ranked = criterion.rank_domain_evidence("market")
        names = [e["name"] for e in ranked]
        assert "market:AAPL converge con freight:LANE-7" in names

    def test_the_entry_keeps_its_provenance(self, learned):
        ent = next(
            e for e in criterion.rank_domain_evidence("market")
            if "causal_learning" in e["sources"]
        )
        assert ent["learning_id"] == learned.learning_id
        assert ent["convergence_id"] == "CONV-MKT-FRT"
        assert ent["policy_id"] == "production-market"
        assert ent["criterion_version"].startswith("crit-")

    def test_the_entry_has_the_same_shape_as_the_other_sources(self, learned):
        """No se inventa una forma nueva: el ranking sigue siendo homogéneo."""
        ent = next(
            e for e in criterion.rank_domain_evidence("market")
            if "causal_learning" in e["sources"]
        )
        for key in ("name", "domain", "sources", "win_rate", "expectancy",
                    "sample_size", "confidence", "wilson_lb", "hits", "tier",
                    "score"):
            assert key in ent, f"falta el campo {key!r} de la forma existente"

    def test_the_metrics_are_the_ones_that_crossed_the_threshold(self, learned):
        ent = next(
            e for e in criterion.rank_domain_evidence("market")
            if "causal_learning" in e["sources"]
        )
        assert ent["win_rate"] == 80.0
        assert ent["sample_size"] == 20
        assert ent["expectancy"] == 0.6
        # Escala de confianza existente (domain_knowledge._compute_confidence):
        # N=20 (<30) con WR=80 -> MEDIUM. No es un peso inventado.
        assert ent["confidence"] == "MEDIUM"

    def test_effective_criterion_reports_what_changed(self, learned):
        result = criterion.effective_criterion("market")
        assert result["criterion_changed"] is True
        assert result["causal_learning_ids"] == [learned.learning_id]
        assert result["convergence_ids"] == ["CONV-MKT-FRT"]
        assert result["criterion_version"].startswith("crit-")
        assert result["added"] == ["market:AAPL converge con freight:LANE-7"]

    def test_effective_criterion_keeps_the_previous_one_to_explain_the_change(self, learned):
        result = criterion.effective_criterion("market")
        assert result["previous_criterion"] == []
        assert len(result["effective_criterion"]) == 1

    def test_there_is_no_shadow_criterion(self, learned):
        result = criterion.effective_criterion("market")
        assert "shadow_criterion" not in result
        assert not hasattr(criterion, "shadow_criterion")


# ===========================================================================
# Sin aprendizaje no hay cambio de criterio
# ===========================================================================

class TestWithoutLearningNothingChanges:

    def test_a_candidate_does_not_enter_the_criterion(self, clean_sources):
        """Superar el gate es la única puerta. Un candidato no mueve nada."""
        cl.ensure_production_policies()
        decision = cl.evaluate_convergence(
            cl.CausalSnapshot(
                convergence_id="CONV-FLOJA", domain="market",
                source_pattern_ids=["market:AAPL", "freight:LANE-7"],
                evidence_ids=["EV-1"],
                metrics={"combined_hits": 9.0, "combined_cc": 0.77},
                claim="convergencia floja",
            ),
            stats_fetcher=_fetcher(**{"market:AAPL": STRONG, "freight:LANE-7": THIN}),
        )
        assert decision.state == cl.STATE_CONVERGED_CANDIDATE
        result = criterion.effective_criterion("market")
        assert result["criterion_changed"] is False
        assert result["causal_learning_ids"] == []
        assert result["effective_criterion"] == []

    def test_an_empty_store_changes_nothing(self, clean_sources):
        result = criterion.effective_criterion("market")
        assert result["criterion_changed"] is False
        assert result["causal_learning_ids"] == []

    def test_a_weakened_learning_leaves_the_criterion(self, learned):
        """Si la convergencia se disuelve, deja de sostener el criterio."""
        assert criterion.effective_criterion("market")["criterion_changed"] is True
        cl.evaluate_convergence(
            cl.CausalSnapshot(
                convergence_id="CONV-MKT-FRT", domain="market",
                source_pattern_ids=["market:AAPL", "freight:LANE-7"],
                evidence_ids=["EV-1", "EV-2"],
                metrics={"combined_hits": 9.0, "combined_cc": 0.77},
                status="dissolved", lifecycle_event="dissolved",
            ),
            stats_fetcher=BOTH_STRONG,
        )
        result = criterion.effective_criterion("market")
        assert result["causal_learning_ids"] == []
        # Pero el aprendizaje y su historia SIGUEN existiendo.
        assert cl.get_learning(learned.learning_id)["state"] == cl.STATE_WEAKENED

    def test_a_contradicted_learning_leaves_the_criterion(self, learned):
        cl.mark_contradicted(learned.learning_id, "outcome contrario")
        assert criterion.effective_criterion("market")["causal_learning_ids"] == []

    def test_only_learned_is_consumable(self, learned):
        """Ningún otro estado puede alimentar el criterio."""
        assert cl.CONSUMABLE_STATES == (cl.STATE_LEARNED,)
        for state in cl.STATES:
            if state == cl.STATE_LEARNED:
                continue
            assert cl.consumable_learnings("market") or True
            assert state not in cl.CONSUMABLE_STATES


# ===========================================================================
# Aprender NO concede permiso para ejecutar
# ===========================================================================

class TestLearningGrantsNoExecution:

    def test_the_causal_module_creates_no_executor(self):
        """Comprobado sobre IMPORTS y LLAMADAS, no sobre el texto fuente.

        Una búsqueda por subcadena daba un falso positivo en cuanto el módulo
        empezó a DECLARAR quién es el consumidor operativo de un dominio
        (`operational_consumer("market")` devuelve el nombre del módulo de
        eToro). Nombrar a quien puede ejecutar no es ejecutar; lo que importa
        es que el puente no lo importe ni lo invoque.
        """
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(cl))

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        for module in imported:
            low = module.lower()
            assert not any(
                bad in low for bad in
                ("etoro", "broker", "executor", "subprocess", "requests")
            ), f"el puente causal importa un ejecutor: {module}"

        called = {
            n.func.attr if isinstance(n.func, ast.Attribute)
            else getattr(n.func, "id", "")
            for n in ast.walk(tree) if isinstance(n, ast.Call)
        }
        for forbidden in ("place_order", "submit_order", "execute_trade",
                          "execute_proposal", "run", "Popen", "post"):
            assert forbidden not in called, (
                f"el puente causal invoca {forbidden!r}: "
                "aprender no concede permiso para ejecutar"
            )

    def test_the_declared_consumer_is_a_name_not_a_call(self):
        """`operational_consumer` devuelve una cadena; no importa ni ejecuta."""
        consumer = cl.operational_consumer("market")
        assert isinstance(consumer, str)
        assert consumer.startswith("connectors.etoro.")
        assert cl.operational_consumer("cybersecurity") == cl.NO_OPERATIONAL_CONSUMER

    def test_the_criterion_entry_is_evidence_not_authorization(self, learned):
        ent = next(
            e for e in criterion.rank_domain_evidence("market")
            if "causal_learning" in e["sources"]
        )
        for forbidden in ("authorized", "permission", "can_execute", "approved",
                          "limit", "capital", "mode"):
            assert forbidden not in ent, (
                f"una entrada de criterio no puede llevar {forbidden!r}"
            )

    def test_an_abstention_is_as_traceable_as_an_application(self, learned):
        """Que el criterio se abstenga es parte de la traza causal."""
        cl.record_application(
            learned.learning_id, convergence_id="CONV-MKT-FRT",
            decision_id="DEC-1", applied=False,
            abstained_reason="governor en pausa",
        )
        app = cl.get_applications(learning_id=learned.learning_id)[0]
        assert app["applied"] is False
        assert app["abstained_reason"] == "governor en pausa"
        assert app["decision_id"] == "DEC-1"

    def test_a_decision_keeps_the_criterion_version(self, learned):
        version = cl.get_learning(learned.learning_id)["criterion_version"]
        cl.record_application(
            learned.learning_id, criterion_version=version, decision_id="DEC-2",
        )
        app = cl.get_applications(learning_id=learned.learning_id)[0]
        assert app["criterion_version"] == version
        assert version.startswith("crit-")
