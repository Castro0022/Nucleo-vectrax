"""
tests/test_constitutional_enforcement.py — El control constitucional gobierna.

EL DEFECTO QUE CIERRA
---------------------
`shadow_check()` evaluaba los 7 principios, escribía en el ledger y devolvía un
veredicto que su propio contrato prohibía usar para decidir. Dos de sus tres
llamadores —`core/idea_store.py` y
`core/learning_cycle/learning_integrator.py`— ni siquiera asignaban el valor de
retorno.

El resultado: un control que aparentaba existir en tres puntos del pipeline y
no gobernaba ninguno. Y no era cuestión de encender un interruptor: la bandera
global `enforce` no los alcanzaba, porque esos dos llamadores descartaban el
veredicto en cualquier modo.

LO QUE SE EXIGE AQUÍ
--------------------
1. Los tres choke points obedecen el MISMO veredicto.
2. Ningún código llama a una comprobación y descarta el resultado.
3. PASS continúa.
4. BLOCK impide la acción y explica la causa.
5. CAUTION queda visible y auditado según su semántica existente.
6. Un error interno produce bloqueo o pausa, nunca fallback silencioso.
7. El mecanismo de emergencia es pausa o bloqueo explícito.
8. Cada decisión guarda correlation_id, acción, veredicto, reglas, actor,
   marca de tiempo y punto de aplicación.
9. No quedan nombres ni estados de sombra.
10. El control constitucional no se mezcla con `causal_learning`.
11. Trading PAPER no se toca.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.operator import constitutional_mode  # noqa: E402
from core.operator.constitutional_filter import (  # noqa: E402
    ActionProposal, PrincipleVerdict,
)
from core.operator.constitutional_guard import GateDecision, enforce_check  # noqa: E402

_GUARD = _ROOT / "core" / "operator" / "constitutional_guard.py"
_CHOKE_POINTS = {
    "external_gateway": _ROOT / "core" / "operator" / "external_gateway.py",
    "idea_store": _ROOT / "core" / "idea_store.py",
    "learning_integrator": (
        _ROOT / "core" / "learning_cycle" / "learning_integrator.py"
    ),
}


@pytest.fixture(autouse=True)
def _isolated_mode(tmp_path, monkeypatch):
    """El modo se persiste en `~/.vectrax`. No se toca el HOME real."""
    monkeypatch.setattr(
        constitutional_mode, "_MODE_PATH",
        str(tmp_path / "constitutional_mode.json"), raising=False,
    )
    constitutional_mode._cache["mode"] = None
    yield
    constitutional_mode._cache["mode"] = None


def _proposal(**kw):
    base = dict(action="respond", correlation_id="CID-1", classification="market")
    base.update(kw)
    return ActionProposal(**base)


# ===========================================================================
# 1 y 2. Los tres obedecen, y nadie descarta el resultado
# ===========================================================================

class TestNobodyDiscardsTheVerdict:

    @pytest.mark.parametrize("name", sorted(_CHOKE_POINTS))
    def test_the_choke_point_uses_the_result(self, name):
        """El valor de retorno se ASIGNA y se consulta.

        Es la comprobación que faltaba: antes la llamada era una sentencia
        suelta (`shadow_check(...)`) cuyo resultado se tiraba.
        """
        path = _CHOKE_POINTS[name]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bare_calls, assigned = [], []
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                fn = node.value.func
                fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if fname == "enforce_check":
                    bare_calls.append(node.lineno)
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                fn = node.value.func
                fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if fname == "enforce_check":
                    assigned.append(node.lineno)
        assert not bare_calls, (
            f"{name} llama a enforce_check y descarta el resultado "
            f"(líneas {bare_calls})"
        )
        assert assigned, f"{name} no llama a enforce_check"

    @pytest.mark.parametrize("name", sorted(_CHOKE_POINTS))
    def test_the_choke_point_branches_on_allowed(self, name):
        text = _CHOKE_POINTS[name].read_text(encoding="utf-8")
        assert ".allowed" in text, (
            f"{name} no ramifica sobre el veredicto: lo consulta y sigue igual"
        )

    @pytest.mark.parametrize("name", sorted(_CHOKE_POINTS))
    def test_the_choke_point_declares_where_it_applies(self, name):
        text = _CHOKE_POINTS[name].read_text(encoding="utf-8")
        assert "application_point=" in text, (
            f"{name} no declara su punto de aplicación"
        )

    def test_no_live_code_calls_the_retired_functions(self):
        skip = {".git", ".venv", "venv", "__pycache__", "node_modules",
                "build", "dist", ".pytest_cache", "tests"}
        offenders = []
        for path in _ROOT.rglob("*.py"):
            if any(p in skip for p in path.relative_to(_ROOT).parts):
                continue
            if not path.is_file():
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and (
                    "constitutional" in node.module
                ):
                    for alias in node.names:
                        if alias.name in ("shadow_check", "gate_check"):
                            offenders.append(
                                f"{path.relative_to(_ROOT)}:{node.lineno}:{alias.name}"
                            )
        assert not offenders, f"sobrevive la API retirada: {offenders}"


# ===========================================================================
# 3, 4 y 5. PASS continúa; BLOCK impide y explica; CAUTION auditado
# ===========================================================================

class TestTheThreeVerdicts:

    def test_pass_allows(self):
        gate = enforce_check(_proposal(), application_point="test")
        assert isinstance(gate, GateDecision)
        assert gate.allowed is True

    def test_block_prevents_and_explains(self):
        import core.operator.constitutional_filter as _cf
        from core.operator.constitutional_filter import PrincipleResult
        from unittest.mock import MagicMock

        forced = MagicMock(return_value=PrincipleResult(
            number=7, name="Generación", verdict=PrincipleVerdict.BLOCK,
            reason="motivo verificable del bloqueo",
        ))
        broken = list(_cf._EVALUATORS)
        broken[6] = forced
        with patch.object(_cf, "_EVALUATORS", tuple(broken)):
            gate = enforce_check(_proposal(), application_point="test")
        assert gate.allowed is False
        assert gate.overall == "block"
        assert "motivo verificable del bloqueo" in gate.reason, (
            "el bloqueo no explica la causa"
        )
        assert "L7=block" in gate.rules

    def test_caution_is_resolved_by_decision_authority_and_audited(self):
        """CAUTION conserva su semántica: la resuelve la autoridad real."""
        from core.operator.decision_authority import Authority, DecisionResult

        with patch(
            "core.operator.decision_authority.check_authority",
            return_value=DecisionResult(
                action="respond", authority=Authority.AUTHORIZED,
                auto_approved=False, reason="denegado en la prueba",
            ),
        ):
            gate = enforce_check(
                _proposal(classification=""), application_point="test",
            )
        assert gate.overall == "caution"
        assert gate.allowed is False
        assert "denegado en la prueba" in gate.reason
        assert gate.decision is not None

    def test_caution_auto_approved_continues(self):
        gate = enforce_check(_proposal(classification=""), application_point="test")
        assert gate.overall == "caution"
        assert gate.allowed is True, (
            "'respond' es AUTO_ACTION: la autoridad debe auto-aprobarlo"
        )


# ===========================================================================
# 6 y 7. Falla cerrado; la emergencia es pausa explícita
# ===========================================================================

class TestFailsClosed:

    def test_an_evaluator_crash_blocks(self):
        import core.operator.constitutional_filter as _cf
        with patch.object(_cf, "evaluate", side_effect=RuntimeError("boom")), \
             patch("core.operator.constitutional_guard.evaluate",
                   side_effect=RuntimeError("boom")):
            gate = enforce_check(_proposal(), application_point="test")
        assert gate.allowed is False
        assert "boom" in gate.reason
        assert gate.overall == "unavailable"

    def test_a_paused_control_refuses(self):
        constitutional_mode.pause(changed_by="test", reason="prueba")
        gate = enforce_check(_proposal(), application_point="test")
        assert gate.allowed is False
        assert "pausa" in gate.reason.lower()

    def test_pause_is_not_an_open_door(self):
        """La diferencia con el modo retirado: pausar DETIENE."""
        constitutional_mode.pause(changed_by="test")
        assert enforce_check(_proposal(), application_point="test").allowed is False

    def test_a_corrupt_state_file_fails_closed(self, tmp_path, monkeypatch):
        bad = tmp_path / "bad.json"
        bad.write_text("{no es json")
        monkeypatch.setattr(constitutional_mode, "_MODE_PATH", str(bad), raising=False)
        constitutional_mode._cache["mode"] = None
        assert constitutional_mode.get_mode() == constitutional_mode.PAUSED
        assert constitutional_mode.FAILSAFE_MODE == constitutional_mode.PAUSED

    def test_resume_restores_the_control(self):
        constitutional_mode.pause(changed_by="test")
        constitutional_mode.resume(changed_by="test")
        assert constitutional_mode.is_active()
        assert enforce_check(_proposal(), application_point="test").allowed is True

    def test_a_ledger_failure_never_propagates(self):
        with patch(
            "core.operator.constitutional_guard._record_ledger",
            side_effect=RuntimeError("ledger caído"),
        ):
            gate = enforce_check(_proposal(), application_point="test")
        assert gate.allowed is True, "un ledger caído cambió la decisión"


# ===========================================================================
# 8. Cada decisión es reconstruible
# ===========================================================================

class TestEveryDecisionIsAuditable:

    def test_the_gate_decision_carries_every_field(self):
        gate = enforce_check(
            _proposal(correlation_id="CID-AUDIT"),
            application_point="tests.audit", actor="mario",
        )
        d = gate.to_dict()
        for field in ("correlation_id", "action", "application_point", "actor",
                      "mode", "timestamp", "allowed", "overall", "rules"):
            assert field in d, f"falta {field!r}"
        assert d["correlation_id"] == "CID-AUDIT"
        assert d["application_point"] == "tests.audit"
        assert d["actor"] == "mario"
        assert d["action"] == "respond"
        assert d["mode"] == constitutional_mode.ACTIVE
        assert d["timestamp"] > 0

    def test_a_missing_correlation_id_is_generated_not_empty(self):
        gate = enforce_check(_proposal(correlation_id=""), application_point="t")
        assert gate.correlation_id, "una decisión sin correlación no es auditable"

    def test_the_ledger_receives_the_whole_decision(self):
        captured = {}

        def _capture(decision):
            captured["d"] = decision

        with patch("core.operator.constitutional_guard._record_ledger", _capture):
            enforce_check(
                _proposal(correlation_id="CID-LEDGER"),
                application_point="tests.ledger", actor="actor-x",
            )
        d = captured["d"]
        assert d.correlation_id == "CID-LEDGER"
        assert d.application_point == "tests.ledger"
        assert d.actor == "actor-x"


# ===========================================================================
# 9, 10 y 11. Sin sombra, sin mezcla, sin tocar PAPER
# ===========================================================================

class TestScopeAndCleanliness:

    def test_no_shadow_names_survive_in_the_constitutional_surface(self):
        """Comprobado por AST sobre IDENTIFICADORES, no sobre el texto.

        Los docstrings de estos módulos narran qué se retiró y por qué — esa
        documentación debe poder existir. Lo que no puede existir es una
        llamada, un import o un atributo con esos nombres.
        """
        retired = {"shadow_check", "gate_check", "is_enforce", "is_shadow",
                   "revert_to_shadow", "_simulate_decision_authority"}
        surface = [
            _GUARD,
            _ROOT / "core" / "operator" / "constitutional_mode.py",
            _ROOT / "core" / "operator" / "constitutional_filter.py",
        ] + list(_CHOKE_POINTS.values())

        offenders = []
        for path in surface:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                name = ""
                if isinstance(node, ast.Name):
                    name = node.id
                elif isinstance(node, ast.Attribute):
                    name = node.attr
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = node.name
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name in retired:
                            offenders.append(
                                f"{path.relative_to(_ROOT)}:{node.lineno}:{alias.name}"
                            )
                    continue
                if name in retired:
                    offenders.append(
                        f"{path.relative_to(_ROOT)}:{node.lineno}:{name}"
                    )
        assert not offenders, f"sobrevive un nombre de sombra en código: {offenders}"

    def test_the_mode_has_only_active_and_paused(self):
        assert set(constitutional_mode._VALID_MODES) == {"active", "paused"}
        assert constitutional_mode.DEFAULT_MODE == "active"

    def test_the_guard_does_not_touch_causal_learning(self):
        """El control constitucional y el puente causal no se mezclan."""
        tree = ast.parse(_GUARD.read_text(encoding="utf-8"))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
            elif isinstance(node, ast.Import):
                modules.update(a.name for a in node.names)
        assert not [m for m in modules if "causal" in m], (
            "el guard constitucional importa el puente causal"
        )

    def test_paper_trading_is_untouched(self):
        """PAPER es la única excepción permitida: este PR no toca trading."""
        executor = _ROOT / "connectors" / "etoro" / "auto_executor.py"
        text = executor.read_text(encoding="utf-8")
        assert "constitutional" not in text.lower(), (
            "el ejecutor de trading empezó a consultar el control constitucional"
        )
        # Y el control tampoco conoce al ejecutor.
        assert "etoro" not in _GUARD.read_text(encoding="utf-8").lower()

    def test_create_idea_is_classified(self):
        """Sin clasificar, activar el control detendría toda la creación de ideas."""
        from core.operator.decision_authority import AUTO_ACTIONS, check_authority
        assert "create_idea" in AUTO_ACTIONS
        assert check_authority(
            "create_idea", governor_mode="observe", risk_level="LOW",
        ).auto_approved is True
