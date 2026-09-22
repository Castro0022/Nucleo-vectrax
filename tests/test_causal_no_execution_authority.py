"""
tests/test_causal_no_execution_authority.py — Aprender no concede ejecutar.

EL LÍMITE
---------
El puente causal produce EVIDENCIA para el criterio. Eso es todo. No puede:

  * saltarse autorización, governor, controles de riesgo o pausas;
  * aumentar límites o capital;
  * cambiar paper a real;
  * eliminar ninguna de las condiciones del validador de entradas;
  * crear ejecutores nuevos.

Es el límite más fácil de erosionar sin querer: basta con que alguien deje que
un `learning_id` participe en un booleano de decisión. Estas pruebas lo fijan
por contrato y sobre el código real.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connectors.etoro import entry_validator as ev  # noqa: E402
from core.learn import causal_learning as cl  # noqa: E402

_VALIDATOR = _ROOT / "connectors" / "etoro" / "entry_validator.py"


# ===========================================================================
# Las condiciones del validador siguen todas ahí
# ===========================================================================

class TestTheValidatorKeepsAllItsConditions:

    def test_every_condition_is_still_numbered_and_present(self):
        """Las condiciones numeradas 0..9 de `validate_entry`, intactas."""
        text = _VALIDATOR.read_text(encoding="utf-8")
        for marker in (
            "# 0. HALT check",
            "# 1. Convergencia fuerte",
            "# 2. Patrón registrado y usable",
            "# 3. Señal repetida",
            "# 4. Confianza mínima",
            "# 5. Mercado activo",
            "# 6. Sin alerta crítica",
            "# 7. Símbolo no operado hoy",
            "# 8. Símbolo aprobado",
            "# 9. Evidencia registrada",
        ):
            assert marker in text, f"desapareció la condición: {marker}"

    def test_the_halt_check_still_short_circuits_first(self):
        """Ningún aprendizaje puede colarse por delante del HALT."""
        tree = ast.parse(_VALIDATOR.read_text(encoding="utf-8"))
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "validate_entry"
        )
        returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
        # El primer `return` de la función sigue siendo el del HALT.
        first = min(returns, key=lambda n: n.lineno)
        assert "False" in ast.dump(first)

    def test_the_causal_bridge_does_not_import_the_executor(self):
        tree = ast.parse((_ROOT / "core" / "learn" / "causal_learning.py")
                         .read_text(encoding="utf-8"))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
            elif isinstance(node, ast.Import):
                modules.update(a.name for a in node.names)
        forbidden = [
            m for m in modules
            if "auto_executor" in m or "etoro" in m or "broker" in m
        ]
        assert not forbidden, (
            f"el puente causal importa un ejecutor: {forbidden}"
        )


# ===========================================================================
# Un aprendizaje no cambia ninguna decisión de entrada
# ===========================================================================

class TestALearningNeverFlipsTheDecision:

    @pytest.fixture
    def no_convergence(self, monkeypatch):
        """Ni convergencia canónica ni masa suficiente: `matched` debe ser False."""
        import core.learn.convergence_registry as reg
        import core.learn.gravity_engine as ge
        monkeypatch.setattr(
            reg, "get_canonical_convergences",
            lambda status=None, **kw: [], raising=False,
        )
        monkeypatch.setattr(
            ge, "get_gravity_index",
            lambda: type("_GI", (), {"get": lambda self, fp: None})(), raising=False,
        )

    def test_without_convergence_a_learning_cannot_make_it_match(
        self, no_convergence,
    ):
        """Aunque existan aprendizajes, sin convergencia sigue sin pasar."""
        cl.ensure_production_policies()
        cl.evaluate_convergence(
            cl.CausalSnapshot(
                convergence_id="CONV-X", domain="market",
                source_pattern_ids=["market:AAPL", "freight:L"],
                evidence_ids=["E"], metrics={"combined_hits": 9, "combined_cc": 0.9},
                claim="c",
            ),
            stats_fetcher=lambda fp: {
                "win_rate": 0.9, "expectancy": 0.8,
                "confidence": 1.0, "sample_size": 30.0,
            },
        )
        assert cl.list_learnings(state=cl.STATE_LEARNED), "debía haber aprendizaje"

        evidence = ev._convergence_evidence("AAPL")
        assert evidence["matched"] is False, (
            "un aprendizaje hizo pasar un símbolo que la regla rechazaba"
        )
        assert evidence["learning_ids"] == []
        assert ev._check_convergence("AAPL") is False

    def test_the_boolean_is_unchanged_when_a_convergence_exists(self, monkeypatch):
        import core.learn.convergence_registry as reg
        monkeypatch.setattr(
            reg, "get_canonical_convergences",
            lambda status=None, **kw: [{
                "convergence_id": "CONV-1", "domain_a": "market",
                "domain_b": "freight_logistics",
                "entity_a_id": "market:AAPL", "entity_b_id": "freight:L",
            }], raising=False,
        )
        evidence = ev._convergence_evidence("AAPL")
        assert evidence["matched"] is True
        assert evidence["convergence_ids"] == ["CONV-1"]
        assert ev._check_convergence("AAPL") is True

    def test_the_ids_are_evidence_only_and_never_gate_the_decision(self):
        """`learning_ids` se calcula DESPUÉS de fijar `matched`."""
        source = ast.parse(_VALIDATOR.read_text(encoding="utf-8"))
        fn = next(
            n for n in ast.walk(source)
            if isinstance(n, ast.FunctionDef) and n.name == "_convergence_evidence"
        )
        text = ast.get_source_segment(
            _VALIDATOR.read_text(encoding="utf-8"), fn,
        )
        matched_at = text.rindex('result["matched"] = True')
        learning_at = text.index('consumable_learnings')
        assert matched_at < learning_at, (
            "los learning_ids se consultan antes de fijar `matched`: "
            "eso los convierte en parte de la decisión"
        )

    def test_a_learning_id_never_appears_in_a_boolean_expression(self):
        """Ningún `if` del validador depende de un aprendizaje."""
        tree = ast.parse(_VALIDATOR.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.If, ast.While)):
                continue
            test_src = ast.dump(node.test)
            assert "learning" not in test_src.lower(), (
                f"una condición del validador depende de un aprendizaje "
                f"(línea {node.lineno})"
            )


# ===========================================================================
# Límites, capital y paper/real: intactos
# ===========================================================================

class TestTradingLimitsAreUntouched:

    def test_this_pr_does_not_touch_the_executor_or_its_config(self):
        """El alcance es comprobable: estos archivos no están en el diff.

        Se comprueba sobre el árbol, no sobre git: los módulos que deciden
        límites, capital y paper/real no pueden haber cambiado, y si alguien
        los toca en un PR futuro, esta prueba obliga a justificarlo.
        """
        executor = _ROOT / "connectors" / "etoro" / "auto_executor.py"
        assert executor.is_file()
        text = executor.read_text(encoding="utf-8")
        assert "causal_learning" not in text, (
            "el ejecutor empezó a leer el puente causal: aprender no concede "
            "permiso para ejecutar"
        )
        assert "learning_id" not in text

    def test_the_causal_module_names_no_money_or_mode_concept(self):
        text = (_ROOT / "core" / "learn" / "causal_learning.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(text)
        # Solo identificadores reales, no la prosa de los docstrings (que sí
        # explica que estos límites NO se tocan).
        names = {
            n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
        } | {
            n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
        }
        for forbidden in ("capital", "max_ops", "approved_symbols", "paper",
                          "live_mode", "position_size", "leverage"):
            assert forbidden not in names, (
                f"el puente causal manipula {forbidden!r}"
            )

    def test_the_bridge_defines_no_execution_function(self):
        tree = ast.parse((_ROOT / "core" / "learn" / "causal_learning.py")
                         .read_text(encoding="utf-8"))
        functions = [
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for name in functions:
            low = name.lower()
            assert not any(
                verb in low for verb in ("execute", "order", "buy", "sell", "trade")
            ), f"el puente causal define un ejecutor: {name}"
