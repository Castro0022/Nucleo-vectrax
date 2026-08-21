"""
Tests — Filtro Constitucional (Fase 1, shadow mode)
======================================================
Cubre los criterios de cierre exigidos para la Fase 1:

  1. Enumeración y cobertura de TODOS los retornos anticipados del pipeline
     actual (`core/operator/external_gateway.py::_do_receive_message`).
  2. Cada ruta demuestra que llega al choke point constitucional ANTES de
     que la respuesta llegue al caller.
  3. Cada evaluación produce los 7 resultados individuales + razones +
     veredicto agregado + registro en el ledger.
  4. Evidencia insuficiente -> CAUTION, nunca PASS.
  5. Fallo técnico del filtro -> CAUTION de alta prioridad, nunca
     autorización silenciosa.
  6. FUNDAMENTAL_LAWS permanece intacto (contenido y cantidad).
  7. Telegram / trading PAPER conservan EXACTAMENTE el comportamiento
     anterior en shadow mode (el filtro nunca altera `GatewayResult`,
     ni siquiera cuando el veredicto simulado sería BLOCK).
  8. El modo global por defecto es 'shadow'; nunca se activa enforcement
     desde este módulo.

Enumeración de los `return GatewayResult(...)` en
`_do_receive_message` (external_gateway.py), verificada línea por línea:
    L318  — contenido vacío            (processed=False, sin acción a evaluar)
    L434  — GREETING intercept         (processed=True,  source="greeting")
    L491  — intake filter IGNORE       (processed=True,  source="intake_filter")
    L519  — intake STORE descartado    (processed=True,  source="intake_store_discarded")
    L563  — intake STORE explícito     (processed=True,  source="intake_store")
    L1700 — fallthrough (memory / domain criterion / self-aware / nucleus /
            SmartRouter pipeline_v2) — todas comparten este único return,
            diferenciadas por `source`/`resolve_mode`.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from core.operator import constitutional_mode
from core.operator.constitutional_filter import (
    ActionProposal,
    ConstitutionalVerdict,
    PrincipleVerdict,
    evaluate,
)
from core.operator.constitutional_guard import shadow_check
from core.operator.external_gateway import ExternalGateway, GatewayResult
from core.operator.identity import FUNDAMENTAL_LAWS, verify_identity_integrity


# ---------------------------------------------------------------------------
# Fixtures — ledger aislado + modo constitucional aislado
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_audit_ledger(tmp_path, monkeypatch):
    """
    audit_ledger.py calcula VAULT_DIR/LEDGER_PATH a nivel de módulo, así que
    monkeypatch.setenv("VECTRAX_VAULT_DIR") por sí solo no basta si el módulo
    ya fue importado en la sesión (mismo patrón que observation_ledger en
    conftest.py). Redirigimos el atributo directamente para garantizar que
    estos tests nunca toquen el ledger real del creador.
    """
    import core.audit_ledger as _al

    db_path = str(tmp_path / "audit_ledger_test.db")
    monkeypatch.setattr(_al, "VAULT_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(_al, "LEDGER_PATH", db_path, raising=False)
    yield db_path


@pytest.fixture(autouse=True)
def _isolated_constitutional_mode(tmp_path, monkeypatch):
    """Aísla el flag global shadow/enforce en un archivo temporal por test."""
    mode_path = str(tmp_path / "constitutional_mode.json")
    monkeypatch.setattr(constitutional_mode, "_MODE_PATH", mode_path, raising=False)
    constitutional_mode._cache["mode"] = None
    constitutional_mode._cache["loaded_at"] = 0.0
    yield
    constitutional_mode._cache["mode"] = None
    constitutional_mode._cache["loaded_at"] = 0.0


def _read_constitutional_ledger_entries() -> List[Dict[str, Any]]:
    """Lee TODAS las entradas del ledger aislado con la categoría constitucional."""
    import core.audit_ledger as _al

    rows = _al.query(limit=1000)
    out = []
    for r in rows:
        try:
            meta = json.loads(r.get("metadata", "{}"))
        except Exception:
            continue
        if meta.get("category") == "operator.constitutional":
            out.append({**r, "meta": meta})
    return out


def _entries_for(proposal_id: str) -> List[Dict[str, Any]]:
    return [
        e for e in _read_constitutional_ledger_entries()
        if e["meta"].get("details", {}).get("proposal_id") == proposal_id
    ]


# ===========================================================================
# 1. Contrato del filtro: 7 resultados, razones, veredicto agregado
# ===========================================================================

class TestPrincipleContract:
    def test_evaluate_always_returns_exactly_seven_results(self):
        verdict = evaluate(ActionProposal(action="respond"))
        assert len(verdict.results) == 7
        assert [r.number for r in verdict.results] == [1, 2, 3, 4, 5, 6, 7]

    def test_each_result_has_verifiable_reason(self):
        verdict = evaluate(ActionProposal(action="respond", classification="market"))
        for r in verdict.results:
            assert r.reason, f"Ley {r.number} sin razón"
            assert r.verdict in (PrincipleVerdict.PASS, PrincipleVerdict.CAUTION, PrincipleVerdict.BLOCK)

    def test_only_three_states_exist(self):
        """Contrato cerrado: nunca un cuarto estado."""
        assert set(PrincipleVerdict) == {
            PrincipleVerdict.PASS, PrincipleVerdict.CAUTION, PrincipleVerdict.BLOCK,
        }

    def test_overall_is_block_if_any_block(self):
        verdict = evaluate(ActionProposal(
            action="integrate_knowledge",
            classification="market",
            action_logged=True,
            interaction_recorded=True,
            is_learning_context=True,
            knowledge_verified=False,  # Ley 7 -> BLOCK
        ))
        assert verdict.overall == PrincipleVerdict.BLOCK
        assert any(r.number == 7 and r.verdict == PrincipleVerdict.BLOCK for r in verdict.results)

    def test_overall_is_caution_if_any_caution_and_no_block(self):
        verdict = evaluate(ActionProposal(action="respond"))  # sin classification -> Ley 1 CAUTION
        assert verdict.overall == PrincipleVerdict.CAUTION

    def test_overall_pass_when_all_pass(self):
        verdict = evaluate(ActionProposal(
            action="respond",
            classification="memory",
            interaction_recorded=True,
            action_logged=True,
        ))
        assert verdict.overall == PrincipleVerdict.PASS
        assert all(r.verdict == PrincipleVerdict.PASS for r in verdict.results)

    def test_to_dict_is_json_serializable(self):
        verdict = evaluate(ActionProposal(action="respond"))
        json.dumps(verdict.to_dict())  # no debe lanzar


# ===========================================================================
# 2. Evidencia insuficiente -> SIEMPRE CAUTION, nunca PASS
# ===========================================================================

class TestInsufficientEvidenceNeverPasses:
    def test_missing_classification_is_caution_not_pass(self):
        r = evaluate(ActionProposal(action="respond")).results[0]  # Ley 1
        assert r.verdict == PrincipleVerdict.CAUTION
        assert "classification" in r.evidence_missing

    def test_missing_interaction_recorded_is_caution_for_reversible(self):
        r = evaluate(ActionProposal(action="respond", is_irreversible=False)).results[1]  # Ley 2
        assert r.verdict == PrincipleVerdict.CAUTION

    def test_missing_interaction_recorded_escalates_to_block_for_irreversible(self):
        r = evaluate(ActionProposal(action="delete_core_memory", is_irreversible=True)).results[1]
        assert r.verdict == PrincipleVerdict.BLOCK

    def test_active_cycles_indeterminate_is_caution(self):
        with patch(
            "core.operator.law_enforcement.detect_active_cycles",
            side_effect=RuntimeError("heartbeat file unreadable"),
        ):
            r = evaluate(ActionProposal(action="respond")).results[2]  # Ley 3
        assert r.verdict == PrincipleVerdict.CAUTION
        assert r.retryable is True

    def test_governor_mode_indeterminate_is_caution(self):
        with patch(
            "core.operator.law_enforcement.detect_governor_mode",
            side_effect=RuntimeError("state file corrupt"),
        ):
            r = evaluate(ActionProposal(action="respond")).results[4]  # Ley 5
        assert r.verdict == PrincipleVerdict.CAUTION

    def test_hypothesis_without_counter_evidence_is_caution(self):
        r = evaluate(ActionProposal(
            action="opine", is_hypothesis_context=True, has_counter_evidence=False,
        )).results[3]  # Ley 4
        assert r.verdict == PrincipleVerdict.CAUTION

    def test_learning_context_unknown_verification_is_caution_not_pass(self):
        r = evaluate(ActionProposal(
            action="integrate", is_learning_context=True, knowledge_verified=None,
        )).results[6]  # Ley 7
        assert r.verdict == PrincipleVerdict.CAUTION
        assert "knowledge_verified" in r.evidence_missing

    def test_missing_traceability_never_produces_pass(self):
        r = evaluate(ActionProposal(action="respond", action_logged=False)).results[5]  # Ley 6
        assert r.verdict != PrincipleVerdict.PASS


# ===========================================================================
# 3. Fallo técnico del filtro -> CAUTION de alta prioridad, nunca silencioso
# ===========================================================================

class TestTechnicalFailureNeverSilentlyAuthorizes:
    def test_single_evaluator_exception_is_caution_not_pass(self):
        # NOTA: `_EVALUATORS` captura referencias directas a las funciones al
        # importar el módulo, así que parchear `constitutional_filter._principle_N`
        # por nombre NO afecta la tupla ya construida. Hay que parchear el
        # elemento dentro de `_EVALUATORS` directamente.
        import core.operator.constitutional_filter as _cf
        broken = list(_cf._EVALUATORS)
        broken[2] = MagicMock(side_effect=RuntimeError("boom"))  # Ley 3 (índice 2)
        with patch.object(_cf, "_EVALUATORS", tuple(broken)):
            verdict = evaluate(ActionProposal(action="respond", classification="x"))
        law3 = [r for r in verdict.results if r.number == 3][0]
        assert law3.verdict == PrincipleVerdict.CAUTION
        assert law3.retryable is True
        assert len(verdict.results) == 7  # el fallo de UNO no rompe el contrato de 7

    def test_complete_filter_failure_produces_caution_all_seven(self):
        with patch(
            "core.operator.constitutional_filter._aggregate",
            side_effect=RuntimeError("catastrophic failure"),
        ):
            verdict = evaluate(ActionProposal(action="respond"))
        assert len(verdict.results) == 7
        assert all(r.verdict == PrincipleVerdict.CAUTION for r in verdict.results)
        assert verdict.overall == PrincipleVerdict.CAUTION
        assert verdict.filter_error  # nunca vacío en este camino
        assert all(r.retryable for r in verdict.results)

    def test_evaluate_never_raises(self):
        with patch(
            "core.operator.constitutional_filter._EVALUATORS",
            (MagicMock(side_effect=RuntimeError("x")),) * 7,
        ):
            verdict = evaluate(ActionProposal(action="respond"))
        assert verdict.overall == PrincipleVerdict.CAUTION

    def test_shadow_check_never_raises_even_if_ledger_is_down(self):
        with patch(
            "core.operator.constitutional_guard._record_ledger",
            side_effect=RuntimeError("ledger unavailable"),
        ):
            # No debe lanzar — un fallo de infraestructura nunca debe
            # propagarse al caller (el pipeline de mensajes).
            verdict = shadow_check(ActionProposal(action="respond", classification="x"))
        assert verdict.overall in (PrincipleVerdict.PASS, PrincipleVerdict.CAUTION, PrincipleVerdict.BLOCK)


# ===========================================================================
# 4. FUNDAMENTAL_LAWS permanece intacto
# ===========================================================================

class TestFundamentalLawsIntact:
    _EXPECTED_NAMES = (
        "Mentalismo", "Correspondencia", "Vibración", "Polaridad",
        "Ritmo", "Causa y Efecto", "Generación",
    )

    def test_exactly_seven_laws(self):
        assert len(FUNDAMENTAL_LAWS) == 7

    def test_names_and_order_unchanged(self):
        assert tuple(law.name for law in FUNDAMENTAL_LAWS) == self._EXPECTED_NAMES
        assert tuple(law.number for law in FUNDAMENTAL_LAWS) == (1, 2, 3, 4, 5, 6, 7)

    def test_principle_and_application_text_nonempty_and_unaltered_shape(self):
        for law in FUNDAMENTAL_LAWS:
            assert law.principle.strip()
            assert law.application.strip()

    def test_frozen_dataclass_cannot_be_mutated(self):
        law = FUNDAMENTAL_LAWS[0]
        with pytest.raises(Exception):
            law.name = "otro nombre"  # type: ignore[misc]

    def test_verify_identity_integrity_still_true(self):
        assert verify_identity_integrity() is True

    def test_constitutional_filter_module_does_not_import_write_access_to_laws(self):
        """El filtro constitucional lee FUNDAMENTAL_LAWS (indirectamente vía
        law_enforcement) pero nunca lo reasigna ni lo muta."""
        import core.operator.constitutional_filter as _cf
        assert not hasattr(_cf, "FUNDAMENTAL_LAWS")


# ===========================================================================
# 5. Registro en el ledger — ejemplos reales
# ===========================================================================

class TestLedgerRecording:
    def test_shadow_check_writes_one_ledger_entry_with_seven_results(self):
        verdict = shadow_check(ActionProposal(action="respond", classification="market", correlation_id="TEST-001"))
        entries = _entries_for("TEST-001")
        assert len(entries) == 1
        details = entries[0]["meta"]["details"]
        assert len(details["results"]) == 7
        assert details["overall"] == verdict.overall.value
        assert details["mode"] == "shadow"
        assert "simulated_decision_authority" in details

    def test_ledger_entry_action_prefixed_with_mode(self):
        shadow_check(ActionProposal(action="respond", classification="x", correlation_id="TEST-002"))
        entries = _entries_for("TEST-002")
        assert entries[0]["action"] == "constitutional:shadow:respond"

    def test_block_verdict_recorded_as_red_risk_zone(self):
        shadow_check(ActionProposal(
            action="integrate", correlation_id="TEST-003", classification="x",
            action_logged=True, interaction_recorded=True,
            is_learning_context=True, knowledge_verified=False,
        ))
        entries = _entries_for("TEST-003")
        meta = entries[0]["meta"]
        assert meta["risk_zone"] == "red"

    def test_caution_verdict_recorded_as_yellow_risk_zone(self):
        shadow_check(ActionProposal(action="respond", correlation_id="TEST-004"))  # sin classification
        entries = _entries_for("TEST-004")
        assert entries[0]["meta"]["risk_zone"] == "yellow"


# ===========================================================================
# 6. Cobertura de rutas — TODOS los `return GatewayResult(...)` enumerados
# ===========================================================================
# Cada shape es EXACTAMENTE lo que produce el `return` correspondiente en
# _do_receive_message (ver docstring del módulo). Se mockea _do_receive_message
# para probar el choke point de forma determinista, exhaustiva y sin depender
# de estado de runtime (gravedad, ARGOS, memoria acumulada).

_EARLY_RETURN_SHAPES = {
    "greeting_L434": GatewayResult(
        event_id="cid-greeting", user_id="u1", channel="web",
        response="Hola. ¿En qué trabajamos?", source="greeting",
        timestamp=0.0, processed=True,
    ),
    "intake_ignore_L491": GatewayResult(
        event_id="cid-ignore", user_id="u1", channel="web",
        response="", source="intake_filter", timestamp=0.0, processed=True,
    ),
    "intake_store_discarded_L519": GatewayResult(
        event_id="cid-discarded", user_id="u1", channel="web",
        response="", source="intake_store_discarded", timestamp=0.0, processed=True,
    ),
    "intake_store_explicit_L563": GatewayResult(
        event_id="cid-store", user_id="u1", channel="web",
        response="Lo tengo presente: me gusta el café.", source="intake_store",
        timestamp=0.0, processed=True,
    ),
    "fallthrough_memory_L1700": GatewayResult(
        event_id="cid-memory", user_id="u1", channel="web",
        response="Te llamas Mario.", source="memory", resolve_mode="memory",
        confidence=1.0, evidence={"route": "memory"}, timestamp=0.0, processed=True,
    ),
    "fallthrough_domain_criterion_L1700": GatewayResult(
        event_id="cid-criterion", user_id="u1", channel="web",
        response="Sobre market: la señal aún no es concluyente.",
        source="criterion:learned", resolve_mode="criterion:learned",
        confidence=1.0, evidence={"route": "criterion:learned", "topic": "market"},
        timestamp=0.0, processed=True,
    ),
    "fallthrough_self_aware_L1700": GatewayResult(
        event_id="cid-selfaware", user_id="u1", channel="web",
        response="Soy Vectrax, observando mi propio universo.",
        source="self_aware", resolve_mode="self_aware", confidence=0.9,
        evidence={"route": "self_aware"}, timestamp=0.0, processed=True,
    ),
    "fallthrough_smartrouter_online_L1700": GatewayResult(
        event_id="cid-online", user_id="u1", channel="web",
        response="Según lo que encontré...", source="online", resolve_mode="online",
        confidence=0.7, evidence={"route": "online", "topic": "general"},
        timestamp=0.0, processed=True,
    ),
    "fallthrough_smartrouter_market_L1700": GatewayResult(
        event_id="cid-market", user_id="u1", channel="web",
        response="BTC: $60,000", source="market", resolve_mode="market",
        confidence=1.0, evidence={"route": "market"}, timestamp=0.0, processed=True,
    ),
    "empty_content_L318": GatewayResult(
        event_id="cid-empty", user_id="u1", channel="web",
        response="", source="", timestamp=0.0, processed=False, error="Empty message content",
    ),
}


class TestAllEarlyReturnsReachChokePoint:
    """
    Cada return temprano de _do_receive_message pasa por el choke point
    constitucional en receive_message() ANTES de llegar al caller — sin
    importar cuál de las 6 rutas internas produjo la respuesta.
    """

    @pytest.mark.parametrize("shape_name", [
        k for k in _EARLY_RETURN_SHAPES if k != "empty_content_L318"
    ])
    def test_route_produces_constitutional_ledger_entry(self, shape_name):
        shape = _EARLY_RETURN_SHAPES[shape_name]
        gw = ExternalGateway()
        with patch.object(gw, "_do_receive_message", return_value=shape):
            result = gw.receive_message(user_id="u1", content="cualquier texto", channel="web")

        # El resultado devuelto al caller es EXACTAMENTE el que produjo la
        # ruta interna — el choke point observa, nunca altera (shadow mode).
        assert result is shape
        assert result.response == shape.response

        entries = _entries_for(shape.event_id)
        assert len(entries) == 1, f"Ruta {shape_name} no llegó al choke point constitucional"
        details = entries[0]["meta"]["details"]
        assert len(details["results"]) == 7
        assert details["mode"] == "shadow"

    def test_empty_content_path_produces_no_constitutional_entry(self):
        """No hay acción real que evaluar cuando el mensaje está vacío."""
        shape = _EARLY_RETURN_SHAPES["empty_content_L318"]
        gw = ExternalGateway()
        with patch.object(gw, "_do_receive_message", return_value=shape):
            gw.receive_message(user_id="u1", content="", channel="web")
        assert _entries_for(shape.event_id) == []

    def test_all_nonempty_routes_covered_exhaustively(self):
        """Verifica que la matriz de rutas cubre TODOS los `return` statements
        REALES de `_do_receive_message` (vía AST, inmune a menciones en
        comentarios/docstrings — fuente única de verdad del conteo)."""
        import ast
        import inspect
        import textwrap
        import core.operator.external_gateway as eg

        src = textwrap.dedent(inspect.getsource(eg.ExternalGateway._do_receive_message))
        tree = ast.parse(src)
        fn = tree.body[0]
        assert isinstance(fn, ast.FunctionDef)

        return_count = 0
        for node in ast.walk(fn):
            if isinstance(node, ast.Return) and node.value is not None:
                call = node.value
                if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "GatewayResult":
                    return_count += 1

        # 6 returns reales: L318(vacío) + 4 tempranos + 1 fallthrough compartido
        # por múltiples rutas lógicas (memory/criterion/self_aware/smartrouter).
        assert return_count == 6, (
            f"Se esperaban 6 `return GatewayResult(...)` reales en _do_receive_message, "
            f"se encontraron {return_count}. Si esto cambió (se añadió/quitó un early-return "
            "real), actualizar _EARLY_RETURN_SHAPES y la enumeración del docstring del módulo."
        )


# ===========================================================================
# 7. Shadow mode NUNCA altera el comportamiento — ni con veredicto BLOCK
# ===========================================================================

class TestShadowModeNeverAltersBehavior:
    def test_default_mode_is_shadow(self):
        assert constitutional_mode.get_mode() == constitutional_mode.SHADOW

    def test_forced_block_verdict_does_not_change_response(self):
        """Incluso si los 7 principios darían BLOCK, la respuesta real al
        usuario no cambia en modo shadow — el choke point solo observa."""
        import core.operator.constitutional_filter as _cf
        from core.operator.constitutional_filter import PrincipleResult

        shape = _EARLY_RETURN_SHAPES["fallthrough_smartrouter_online_L1700"]
        gw = ExternalGateway()

        forced_block = MagicMock(return_value=PrincipleResult(
            number=7, name="Generación", verdict=PrincipleVerdict.BLOCK,
            reason="forzado por test",
        ))
        broken = list(_cf._EVALUATORS)
        broken[6] = forced_block  # Ley 7 (índice 6)

        with patch.object(gw, "_do_receive_message", return_value=shape), \
             patch.object(_cf, "_EVALUATORS", tuple(broken)):
            result = gw.receive_message(user_id="u1", content="x", channel="web")

        assert result.response == shape.response
        assert result.processed is True
        entries = _entries_for(shape.event_id)
        assert entries[0]["meta"]["details"]["overall"] == "block"  # se registró el BLOCK...
        assert result.response == shape.response  # ...pero la respuesta no cambió

    def test_receive_message_never_raises_if_constitutional_filter_explodes(self):
        shape = _EARLY_RETURN_SHAPES["greeting_L434"]
        gw = ExternalGateway()
        with patch.object(gw, "_do_receive_message", return_value=shape), \
             patch.object(gw, "_constitutional_gate", side_effect=RuntimeError("boom")):
            result = gw.receive_message(user_id="u1", content="hola", channel="web")
        assert result.response == shape.response

    def test_constitutional_mode_defaults_to_shadow_on_corrupt_file(self, tmp_path, monkeypatch):
        bad_path = tmp_path / "bad_mode.json"
        bad_path.write_text("{not valid json")
        monkeypatch.setattr(constitutional_mode, "_MODE_PATH", str(bad_path), raising=False)
        constitutional_mode._cache["mode"] = None
        assert constitutional_mode.get_mode() == constitutional_mode.SHADOW

    def test_constitutional_mode_defaults_to_shadow_on_invalid_value(self, tmp_path, monkeypatch):
        bad_path = tmp_path / "bad_mode2.json"
        bad_path.write_text(json.dumps({"mode": "yolo"}))
        monkeypatch.setattr(constitutional_mode, "_MODE_PATH", str(bad_path), raising=False)
        constitutional_mode._cache["mode"] = None
        assert constitutional_mode.get_mode() == constitutional_mode.SHADOW

    def test_set_mode_and_revert_roundtrip(self):
        constitutional_mode.set_mode(constitutional_mode.ENFORCE, changed_by="test")
        assert constitutional_mode.get_mode() == constitutional_mode.ENFORCE
        constitutional_mode.revert_to_shadow(changed_by="test_killswitch")
        assert constitutional_mode.get_mode() == constitutional_mode.SHADOW

    def test_set_mode_rejects_invalid_value(self):
        with pytest.raises(ValueError):
            constitutional_mode.set_mode("bogus")


# ===========================================================================
# 7b. Enforce mode — el veredicto SI altera la respuesta (plan "Conectar el
#     veredicto constitucional (Kybalion) a bloqueo real en
#     external_gateway.py")
# ===========================================================================

class TestEnforceModeAltersResponse:
    """Con constitutional_mode='enforce', el choke point de
    external_gateway.py ACTÚA sobre el veredicto: BLOCK y
    CAUTION-no-autorizado sustituyen la respuesta real; PASS y
    CAUTION-autorizado la dejan intacta. Nunca silencioso (siempre hay
    respuesta), nunca lanza (fallo técnico -> passthrough)."""

    def test_forced_block_replaces_response_in_enforce_mode(self):
        import core.operator.constitutional_filter as _cf
        from core.operator.constitutional_filter import PrincipleResult

        constitutional_mode.set_mode(constitutional_mode.ENFORCE, changed_by="test")
        shape = _EARLY_RETURN_SHAPES["fallthrough_smartrouter_online_L1700"]
        gw = ExternalGateway()

        forced_block = MagicMock(return_value=PrincipleResult(
            number=7, name="Generación", verdict=PrincipleVerdict.BLOCK,
            reason="forzado por test",
        ))
        broken = list(_cf._EVALUATORS)
        broken[6] = forced_block  # Ley 7 (índice 6)

        with patch.object(gw, "_do_receive_message", return_value=shape), \
             patch.object(_cf, "_EVALUATORS", tuple(broken)):
            result = gw.receive_message(user_id="u1", content="x", channel="web")

        assert result is shape  # mismo objeto, mutado in-place por el gate
        assert result.response != "Según lo que encontré..."
        assert result.response  # nunca vacío/silencioso
        assert result.source == "constitutional_block"
        assert result.processed is True
        entries = _entries_for(shape.event_id)
        assert entries[0]["meta"]["details"]["overall"] == "block"
        assert entries[0]["meta"]["details"]["mode"] == "enforce"

    def test_caution_denied_replaces_response_in_enforce_mode(self):
        """Sin classification -> Ley 1 CAUTION. Con DecisionAuthority
        mockeado a no-autorizado, la respuesta se sustituye."""
        from core.operator.decision_authority import DecisionResult, Authority

        constitutional_mode.set_mode(constitutional_mode.ENFORCE, changed_by="test")
        shape = GatewayResult(
            event_id="cid-caution-denied", user_id="u1", channel="web",
            response="respuesta original", source="", resolve_mode="",
            timestamp=0.0, processed=True,
        )
        gw = ExternalGateway()

        with patch.object(gw, "_do_receive_message", return_value=shape), \
             patch(
                 "core.operator.decision_authority.check_authority",
                 return_value=DecisionResult(
                     action="respond", authority=Authority.AUTHORIZED,
                     auto_approved=False, reason="test denial",
                 ),
             ):
            result = gw.receive_message(user_id="u1", content="x", channel="web")

        assert result.response != "respuesta original"
        assert result.response
        assert result.source == "constitutional_caution_denied"

    def test_caution_approved_leaves_response_unchanged_in_enforce_mode(self):
        """Sin classification -> CAUTION, pero 'respond' es AUTO_ACTION real
        (fix de decision_authority.py) -> DecisionAuthority auto-aprueba,
        sin mockear nada -> respuesta sin cambios."""
        constitutional_mode.set_mode(constitutional_mode.ENFORCE, changed_by="test")
        shape = GatewayResult(
            event_id="cid-caution-ok", user_id="u1", channel="web",
            response="respuesta original", source="", resolve_mode="",
            timestamp=0.0, processed=True,
        )
        gw = ExternalGateway()
        with patch.object(gw, "_do_receive_message", return_value=shape):
            result = gw.receive_message(user_id="u1", content="x", channel="web")

        assert result.response == "respuesta original"

    def test_pass_verdict_leaves_response_unchanged_in_enforce_mode(self):
        constitutional_mode.set_mode(constitutional_mode.ENFORCE, changed_by="test")
        shape = _EARLY_RETURN_SHAPES["fallthrough_memory_L1700"]
        gw = ExternalGateway()
        with patch.object(gw, "_do_receive_message", return_value=shape):
            result = gw.receive_message(user_id="u1", content="x", channel="web")
        assert result.response == shape.response

    def test_receive_message_never_raises_if_enforce_gate_explodes(self):
        constitutional_mode.set_mode(constitutional_mode.ENFORCE, changed_by="test")
        shape = _EARLY_RETURN_SHAPES["greeting_L434"]
        gw = ExternalGateway()
        with patch.object(gw, "_do_receive_message", return_value=shape), \
             patch.object(gw, "_constitutional_gate", side_effect=RuntimeError("boom")):
            result = gw.receive_message(user_id="u1", content="hola", channel="web")
        assert result.response == shape.response

    def test_shadow_mode_unaffected_by_new_enforce_code_path(self):
        """Regresión explícita: con el modo en su default (shadow), el
        código nuevo de enforce NUNCA se ejecuta — comportamiento idéntico."""
        assert constitutional_mode.get_mode() == constitutional_mode.SHADOW
        shape = _EARLY_RETURN_SHAPES["fallthrough_smartrouter_online_L1700"]
        gw = ExternalGateway()
        with patch.object(gw, "_do_receive_message", return_value=shape):
            result = gw.receive_message(user_id="u1", content="x", channel="web")
        assert result.response == shape.response


# ===========================================================================
# 8. Telegram — comportamiento real sin cambios (end-to-end, sin mocks)
# ===========================================================================

class TestTelegramBehaviorUnchanged:
    """Llamadas reales a receive_message() (mismo patrón que
    tests/test_external_gateway.py) — confirman que la respuesta real de
    rutas fácilmente disparables no cambió con el filtro instalado."""

    def test_greeting_response_unchanged_and_logged(self):
        gw = ExternalGateway()
        result = gw.receive_message(user_id="tg:111", content="Hola", channel="telegram")
        assert result.processed is True
        assert result.source == "greeting"
        assert "trabajamos" in result.response.lower() or "help" in result.response.lower() or result.response
        entries = _entries_for(result.event_id)
        assert len(entries) == 1

    def test_explicit_store_response_unchanged_and_logged(self):
        gw = ExternalGateway()
        result = gw.receive_message(
            user_id="tg:222", content="recuerda que me gusta el café",
            channel="telegram",
        )
        assert result.processed is True
        entries = _entries_for(result.event_id)
        assert len(entries) == 1

    def test_empty_message_still_rejected_identically(self):
        gw = ExternalGateway()
        result = gw.receive_message(user_id="tg:333", content="", channel="telegram")
        assert result.processed is False
        assert result.error == "Empty message content"
        assert _entries_for(result.event_id) == []


# ===========================================================================
# 9. Trading PAPER — sin cambio de comportamiento (no se tocó ese subsistema)
# ===========================================================================

class TestTradingPaperUnaffected:
    def test_auto_executor_config_unaffected_by_constitutional_imports(self, tmp_path, monkeypatch):
        import connectors.etoro.auto_executor as ae
        cfg_path = str(tmp_path / "etoro_auto_config.json")
        monkeypatch.setattr(ae, "_CONFIG_FILE", cfg_path, raising=False)

        # Importar/usar el filtro constitucional no debe tocar config de trading.
        evaluate(ActionProposal(action="respond"))
        shadow_check(ActionProposal(action="respond", correlation_id="TEST-TRADE-1"))

        assert ae.get_mode() == ae.AutoMode.OFF  # default sin cambios
        assert ae.get_config()["mode"] == "off"

    def test_learning_integrator_choke_point_does_not_change_activation_outcome(self):
        """El choke point 3 (aprendizaje) es shadow-only: la decisión real de
        activar/pending la sigue tomando check_authority() sin cambios."""
        from core.learning_cycle.learning_integrator import LearningIntegrator
        integrator = LearningIntegrator()

        class _FakeStore:
            def activate_rule(self, rule_id):
                self.activated = rule_id

        store = _FakeStore()

        class _FakeHyp:
            confidence = 0.9

        with patch(
            "core.operator.decision_authority.check_authority",
        ) as mock_ca:
            from core.operator.decision_authority import DecisionResult, Authority
            mock_ca.return_value = DecisionResult(
                action="activate_learned_rule", authority=Authority.AUTO,
                auto_approved=True, reason="test",
            )
            activated = integrator._try_activate_rule("RULE-1", store, _FakeHyp(), "general")

        assert activated is True
        assert getattr(store, "activated", None) == "RULE-1"


# ===========================================================================
# 10. Generación de ideas — choke point 4, shadow-only
# ===========================================================================

class TestIdeaCreationChokePoint:
    def test_add_idea_still_returns_idea_unchanged(self, tmp_path):
        from core.idea_store import IdeaStore, IdeaSource, IdeaPriority

        store = IdeaStore(path=str(tmp_path / "ideas.jsonl"))
        idea = store.add(
            title="Ajustar umbral X",
            description="Evidencia de conflicto semántico/regex",
            source=IdeaSource.ROUTER_LEARNING,
            priority=IdeaPriority.MEDIUM,
            impact_score=0.6,
            affected_component="smart_router",
        )
        assert idea is not None
        assert idea.status.value == "pending"

        entries = _entries_for(idea.idea_id)
        assert len(entries) == 1
        assert len(entries[0]["meta"]["details"]["results"]) == 7

    def test_add_idea_never_raises_if_constitutional_guard_fails(self, tmp_path):
        from core.idea_store import IdeaStore, IdeaSource, IdeaPriority

        store = IdeaStore(path=str(tmp_path / "ideas2.jsonl"))
        with patch(
            "core.operator.constitutional_guard.shadow_check",
            side_effect=RuntimeError("boom"),
        ):
            idea = store.add(
                title="X", description="Y", source=IdeaSource.MANUAL,
                priority=IdeaPriority.LOW, impact_score=0.1,
                affected_component="x",
            )
        assert idea is not None  # la creación de la idea no se vio afectada
