"""
tests/test_external_gateway_capability_shadow.py — Fase 3, paso 8.

Fija el contrato del gate en modo sombra conectado en
`core/operator/external_gateway.py::receive_message()` (STEP 4.2b),
detrás del flag `VX_CAPABILITY_SELF_AWARENESS` (default OFF):

  1. Flag apagado (default) → `build_capability_context()` NUNCA se
     invoca. Cero cambios de comportamiento respecto al camino existente
     (`is_self_referential` + `resolve_self_aware` sigue siendo lo único
     que decide la respuesta real).
  2. Flag encendido + `decision.capability_query=True` →
     `build_capability_context()` SÍ se invoca, y los gaps detectados se
     registran vía `record_capability_gap()` — pero la respuesta real
     (`response_text`) permanece EXACTAMENTE igual a la que ya resolvía
     `is_self_referential`/`resolve_self_aware` (nunca se sustituye).
  3. Flag encendido pero `decision.capability_query=False` →
     `build_capability_context()` tampoco se invoca (el gate de sombra
     respeta el SSOT, no reacciona a cualquier mensaje self-referencial).
  4. Defensivo: si el bloque de sombra lanza, la respuesta real no se ve
     afectada (passthrough silencioso, igual que el resto del pipeline).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from core.operator.external_gateway import ExternalGateway, reset_gateway
from core.operator.universal_bus import reset_bus


@pytest.fixture(autouse=True)
def clean_singletons():
    reset_bus()
    reset_gateway()
    yield
    reset_bus()
    reset_gateway()


# Content que satisface AMBOS detectores con sus patrones reales (sin
# mockear ninguno de los dos): vectrax/self_context.py::_SELF_REFERENCE
# ("motor(es)?") y core/intent_ssot.py::_CAPABILITY_QUESTION_RE
# ("qué motores tienes").
_CAPABILITY_CONTENT = "¿qué motores tienes conectados?"
# Self-referencial (dispara is_self_referential vía self_knowledge
# origin/provenance) pero NUNCA capability_query (regex deliberadamente
# acotado en intent_ssot.py para excluirlo).
_NON_CAPABILITY_SELF_REF_CONTENT = "¿quién te creó?"

_SELFAWARE_SENTINEL = "SELF_AWARE_SHADOW_TEST_SENTINEL"

_COMMON_PATCHES = dict(
    run_audit=patch(
        "vectrax.response_auditor.run_audit",
        side_effect=lambda **kw: kw.get("response", ""),
    ),
    enforce_language=patch(
        "core.language_gate.enforce_language",
        side_effect=lambda text, *a, **k: text,
    ),
    presence_policy=patch(
        "core.conversation.presence_policy.apply_presence_policy",
        side_effect=lambda text, *a, **k: (text, False),
    ),
)


def _send(gw, content, user_id):
    with _COMMON_PATCHES["run_audit"], _COMMON_PATCHES["enforce_language"], \
         _COMMON_PATCHES["presence_policy"]:
        return gw.receive_message(user_id=user_id, content=content, channel="telegram")


class TestCapabilityShadowFlagOff:
    """Fase 3, paso 9 (parcial): con el flag apagado, cero cambios de
    comportamiento — build_capability_context() ni se importa/invoca."""

    def test_shadow_never_invoked_when_flag_unset(self, monkeypatch):
        monkeypatch.delenv("VX_CAPABILITY_SELF_AWARENESS", raising=False)
        gw = ExternalGateway()
        with patch("vectrax.self_context.is_self_referential", return_value=True), \
             patch("vectrax.self_context.resolve_self_aware", return_value=_SELFAWARE_SENTINEL), \
             patch(
                 "core.self_observation.capability_context.build_capability_context",
             ) as mock_build:
            result = _send(gw, _CAPABILITY_CONTENT, user_id="tg:cap_shadow_flag_unset")
        mock_build.assert_not_called()
        assert result.response == _SELFAWARE_SENTINEL

    def test_shadow_never_invoked_when_flag_explicitly_off(self, monkeypatch):
        for off_value in ("0", "false", "off", "no", ""):
            monkeypatch.setenv("VX_CAPABILITY_SELF_AWARENESS", off_value)
            gw = ExternalGateway()
            with patch("vectrax.self_context.is_self_referential", return_value=True), \
                 patch("vectrax.self_context.resolve_self_aware", return_value=_SELFAWARE_SENTINEL), \
                 patch(
                     "core.self_observation.capability_context.build_capability_context",
                 ) as mock_build:
                result = _send(gw, _CAPABILITY_CONTENT, user_id=f"tg:off_{off_value}")
            mock_build.assert_not_called()
            assert result.response == _SELFAWARE_SENTINEL


class TestCapabilityShadowFlagOn:
    """Con el flag encendido: el gate de sombra corre EN PARALELO, nunca
    sustituye response_text."""

    def test_shadow_invoked_and_records_gaps_without_changing_response(self, monkeypatch):
        from core.self_observation.capability_context import CapabilityContext, CapabilityEntry
        from core.orchestration.bootstrap import HEALTH_UNAVAILABLE

        monkeypatch.setenv("VX_CAPABILITY_SELF_AWARENESS", "1")
        gw = ExternalGateway()
        user_id = "tg:cap_shadow_gaps"

        gap_entry = CapabilityEntry(
            name="fake_gap_engine", kind="engine", group="test",
            exists=True, connected=False, authorized=True,
            health=HEALTH_UNAVAILABLE, reason="ImportError: boom",
            evidence_source="test", observed_at=0.0,
        )
        fake_ctx = CapabilityContext(
            query_domain=None, query_task_type=None, query_capability=True,
            entries=[gap_entry], gaps=[gap_entry], fallback_sources=[],
        )

        with patch("vectrax.self_context.is_self_referential", return_value=True), \
             patch("vectrax.self_context.resolve_self_aware", return_value=_SELFAWARE_SENTINEL), \
             patch(
                 "core.self_observation.capability_context.build_capability_context",
                 return_value=fake_ctx,
             ) as mock_build, \
             patch(
                 "core.self_observation.capability_context.record_capability_gap",
             ) as mock_record:
            result = _send(gw, _CAPABILITY_CONTENT, user_id=user_id)

        mock_build.assert_called_once()
        mock_record.assert_called_once()
        _, kwargs = mock_record.call_args
        assert kwargs["capability_name"] == "fake_gap_engine"
        assert kwargs["health"] == HEALTH_UNAVAILABLE
        assert kwargs["authorized"] is True
        # La respuesta real NUNCA se sustituye por el gate de sombra.
        assert result.response == _SELFAWARE_SENTINEL

    def test_shadow_not_invoked_when_capability_query_is_false(self, monkeypatch):
        """Aun con el flag encendido, un mensaje self-referencial que NO es
        pregunta de capacidad (según el SSOT) no dispara el gate de sombra."""
        monkeypatch.setenv("VX_CAPABILITY_SELF_AWARENESS", "1")
        gw = ExternalGateway()
        with patch("vectrax.self_context.is_self_referential", return_value=True), \
             patch("vectrax.self_context.resolve_self_aware", return_value=_SELFAWARE_SENTINEL), \
             patch(
                 "core.self_observation.capability_context.build_capability_context",
             ) as mock_build:
            result = _send(gw, _NON_CAPABILITY_SELF_REF_CONTENT, user_id="tg:cap_shadow_non_capability")
        mock_build.assert_not_called()
        assert result.response == _SELFAWARE_SENTINEL

    def test_shadow_exception_is_defensive_passthrough(self, monkeypatch):
        """Si el gate de sombra lanza, la respuesta real no se ve afectada."""
        monkeypatch.setenv("VX_CAPABILITY_SELF_AWARENESS", "1")
        gw = ExternalGateway()
        with patch("vectrax.self_context.is_self_referential", return_value=True), \
             patch("vectrax.self_context.resolve_self_aware", return_value=_SELFAWARE_SENTINEL), \
             patch(
                 "core.self_observation.capability_context.build_capability_context",
                 side_effect=RuntimeError("boom"),
             ):
            result = _send(gw, _CAPABILITY_CONTENT, user_id="tg:cap_shadow_exception")
        assert result.response == _SELFAWARE_SENTINEL

    def test_shadow_never_calls_record_gap_when_no_gaps(self, monkeypatch):
        from core.self_observation.capability_context import CapabilityContext

        monkeypatch.setenv("VX_CAPABILITY_SELF_AWARENESS", "1")
        gw = ExternalGateway()
        empty_ctx = CapabilityContext(
            query_domain=None, query_task_type=None, query_capability=True,
            entries=[], gaps=[], fallback_sources=[],
        )
        with patch("vectrax.self_context.is_self_referential", return_value=True), \
             patch("vectrax.self_context.resolve_self_aware", return_value=_SELFAWARE_SENTINEL), \
             patch(
                 "core.self_observation.capability_context.build_capability_context",
                 return_value=empty_ctx,
             ) as mock_build, \
             patch(
                 "core.self_observation.capability_context.record_capability_gap",
             ) as mock_record:
            result = _send(gw, _CAPABILITY_CONTENT, user_id="tg:cap_shadow_no_gaps")
        mock_build.assert_called_once()
        mock_record.assert_not_called()
        assert result.response == _SELFAWARE_SENTINEL
