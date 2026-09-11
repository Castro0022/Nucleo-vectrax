"""
tests/test_speech_act_gate.py — Diseño A/B/C, Frentes A/B, integración.

Fija el contrato del gate ampliado en `core/operator/external_gateway.py`
(bloque self-aware de `_do_receive_message()`) y del despacho
narrar-vs-ejecutar (`ExternalGateway._maybe_execute_action_request()`),
detrás del flag `VX_SPEECH_ACT_ROUTING` (default OFF):

  1. Flag apagado (default) → `core.intent_ssot.resolve_intent()` NUNCA se
     invoca desde el gate — cero cambios de comportamiento respecto al
     camino existente (`is_self_referential` sigue siendo lo único que
     decide si se entra al bloque self-aware).
  2. Flag encendido + auto-referencia de marca (`is_self_referential=True`)
     → comportamiento LEGACY intacto (`resolve_self_aware()`), el
     respondedor de `capability_status` NUNCA se invoca para este camino.
  3. Flag encendido + SIN auto-referencia de marca + `speech_act=="query"`
     → responde desde `capability_status` (verificado), NUNCA desde
     `resolve_self_aware()` (LLM libre).
  4. Flag encendido + `speech_act=="action"` → el gate de SELF_KNOWLEDGE NO
     se activa (solo "query" lo amplía); la acción se decide más abajo, en
     `_resolve_via_pipeline` (Frente B/C, cubierto por
     `ExternalGateway._maybe_execute_action_request()`).
  5. `_maybe_execute_action_request()`: capability_status decide narrar vs
     ejecutar; `action_extraction` solo se invoca cuando ya hay autorización
     de ejecutar (para un dominio ya conocido) o para inferir el dominio
     cuando el router no lo determinó.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.action_extraction import PlaceSearchParams, ReminderParams
from core.intent_ssot import IntentDecision
from core.operator.external_gateway import ExternalGateway, reset_gateway
from core.operator.universal_bus import reset_bus
from core.self_observation.capability_status import (
    CapabilityStatusEntry,
    STATUS_CONDITIONAL,
    STATUS_CONFIRMED,
)
from core.smart_router import RiskLevel, SmartRoute, Strategy


@pytest.fixture(autouse=True)
def clean_singletons():
    reset_bus()
    reset_gateway()
    yield
    reset_bus()
    reset_gateway()


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


_QUERY_DECISION = IntentDecision(primary_intent="online", speech_act="query")
_ACTION_DECISION = IntentDecision(primary_intent="online", speech_act="action")


# ===========================================================================
# 1-4. Gate ampliado en _do_receive_message (nivel receive_message)
# ===========================================================================

class TestSpeechActGateFlagOff:
    """NOTA: `core.intent_ssot.resolve_intent()` se invoca en varios puntos
    del pipeline aguas abajo (p.ej. `SmartRouter.route()`) sin relación con
    este gate — por eso la aserción aquí es sobre el EFECTO observable
    (nunca se responde desde `capability_status`), no sobre si
    `resolve_intent` se llamó en algún lugar del sistema."""

    def test_capability_status_never_used_when_flag_unset(self, monkeypatch):
        monkeypatch.delenv("VX_SPEECH_ACT_ROUTING", raising=False)
        gw = ExternalGateway()
        with patch("vectrax.self_context.is_self_referential", return_value=False), \
             patch(
                 "core.self_observation.capability_status.build_capability_status_context",
             ) as mock_status:
            _send(gw, "¿En qué me puedes ayudar?", user_id="tg:sa_off_1")
        mock_status.assert_not_called()

    def test_capability_status_never_used_when_flag_explicitly_off(self, monkeypatch):
        for off_value in ("0", "false", "off", "no", ""):
            monkeypatch.setenv("VX_SPEECH_ACT_ROUTING", off_value)
            gw = ExternalGateway()
            with patch("vectrax.self_context.is_self_referential", return_value=False), \
                 patch(
                     "core.self_observation.capability_status.build_capability_status_context",
                 ) as mock_status:
                _send(gw, "¿En qué me puedes ayudar?", user_id=f"tg:sa_off_{off_value}")
            mock_status.assert_not_called()


class TestSpeechActGateFlagOnBrandStillWins:
    def test_brand_self_reference_uses_legacy_self_aware_untouched(self, monkeypatch):
        """is_self_referential=True (vocabulario de marca) sigue resolviendo
        por resolve_self_aware() exactamente igual que antes — el
        respondedor de capability_status NUNCA se invoca para este camino."""
        monkeypatch.setenv("VX_SPEECH_ACT_ROUTING", "1")
        gw = ExternalGateway()
        with patch("vectrax.self_context.is_self_referential", return_value=True), \
             patch("vectrax.self_context.resolve_self_aware", return_value="LEGACY_ANSWER"), \
             patch(
                 "core.self_observation.capability_status.build_capability_status_context",
             ) as mock_status:
            result = _send(gw, "cómo va tu universo", user_id="tg:sa_brand")
        mock_status.assert_not_called()
        assert result.response == "LEGACY_ANSWER"


class TestSpeechActGateFlagOnQueryRoutesToCapabilityStatus:
    def test_query_without_brand_reference_uses_capability_status(self, monkeypatch):
        monkeypatch.setenv("VX_SPEECH_ACT_ROUTING", "1")
        gw = ExternalGateway()
        with patch("vectrax.self_context.is_self_referential", return_value=False), \
             patch("vectrax.self_context.resolve_self_aware") as mock_legacy, \
             patch(
                 "core.intent_ssot.resolve_intent", return_value=_QUERY_DECISION,
             ), \
             patch(
                 "core.self_observation.capability_status.build_capability_status_context",
                 return_value="CAPABILITY_STATUS_ANSWER",
             ), \
             patch("vectrax.command_hints.detect_command_hint", return_value=""):
            result = _send(gw, "¿En qué me puedes ayudar?", user_id="tg:sa_query")
        mock_legacy.assert_not_called()
        assert result.response == "CAPABILITY_STATUS_ANSWER"

    def test_query_response_bypasses_response_auditor(self, monkeypatch):
        """La narración de capability_status ya es verificada — no debe
        pasar por el auditor LLM (mismo bypass que la Fase 4)."""
        monkeypatch.setenv("VX_SPEECH_ACT_ROUTING", "1")
        gw = ExternalGateway()
        with patch("vectrax.self_context.is_self_referential", return_value=False), \
             patch(
                 "core.intent_ssot.resolve_intent", return_value=_QUERY_DECISION,
             ), \
             patch(
                 "core.self_observation.capability_status.build_capability_status_context",
                 return_value="CAPABILITY_STATUS_ANSWER",
             ), \
             patch("vectrax.response_auditor.run_audit") as mock_audit, \
             patch(
                 "core.language_gate.enforce_language",
                 side_effect=lambda text, *a, **k: text,
             ), \
             patch(
                 "core.conversation.presence_policy.apply_presence_policy",
                 side_effect=lambda text, *a, **k: (text, False),
             ), \
             patch("vectrax.command_hints.detect_command_hint", return_value=""):
            result = gw.receive_message(
                user_id="tg:sa_query_audit", content="¿Qué capacidades tienes?",
                channel="telegram",
            )
        mock_audit.assert_not_called()
        assert result.response == "CAPABILITY_STATUS_ANSWER"


class TestSpeechActGateFlagOnActionDoesNotEnterSelfAware:
    def test_action_speech_act_does_not_trigger_self_aware_gate(self, monkeypatch):
        monkeypatch.setenv("VX_SPEECH_ACT_ROUTING", "1")
        gw = ExternalGateway()
        with patch("vectrax.self_context.is_self_referential", return_value=False), \
             patch("vectrax.self_context.resolve_self_aware") as mock_legacy, \
             patch(
                 "core.intent_ssot.resolve_intent", return_value=_ACTION_DECISION,
             ), \
             patch(
                 "core.self_observation.capability_status.build_capability_status_context",
             ) as mock_status:
            _send(gw, "Búscame un restaurante cerca de mí", user_id="tg:sa_action")
        mock_legacy.assert_not_called()
        mock_status.assert_not_called()


# ===========================================================================
# 5. _maybe_execute_action_request — despacho narrar-vs-ejecutar (unitario)
# ===========================================================================

def _route(strategy: Strategy) -> SmartRoute:
    return SmartRoute(
        intent=None, topic="general", risk_level=RiskLevel.LOW,
        strategy=strategy, confidence=0.8, reason="test",
    )


def _status_entry(status: str) -> CapabilityStatusEntry:
    return CapabilityStatusEntry(
        name="x", kind="capability", group="test", status=status,
        detail="ready" if status == STATUS_CONFIRMED else "unauthorized",
        condition="" if status == STATUS_CONFIRMED else "SOME_FLAG",
        observed_at=0.0,
    )


class TestActionDispatchSpeechActGuard:
    def test_returns_none_when_not_action(self):
        gw = ExternalGateway()
        with patch(
            "core.intent_ssot.resolve_intent",
            return_value=IntentDecision(primary_intent="online", speech_act="query"),
        ):
            result = gw._maybe_execute_action_request(
                content="¿puedes buscar restaurantes?", user_id="tg:x", lang="es",
                smart_route=_route(Strategy.RESOLVE_PLACES),
            )
        assert result is None

    def test_returns_none_when_smart_route_is_none(self):
        gw = ExternalGateway()
        assert gw._maybe_execute_action_request(
            content="búscame algo", user_id="tg:x", lang="es", smart_route=None,
        ) is None


class TestActionDispatchPlaceSearch:
    def test_executes_when_confirmed(self):
        gw = ExternalGateway()
        params = PlaceSearchParams(category="restaurant", cuisine="italian")
        with patch(
            "core.intent_ssot.resolve_intent",
            return_value=IntentDecision(primary_intent="place_search", speech_act="action"),
        ), patch(
            "core.self_observation.capability_status.get_capability_status",
            return_value=_status_entry(STATUS_CONFIRMED),
        ), patch(
            "core.action_extraction.extract_action_params", return_value=params,
        ), patch(
            "vectrax.integrations.place_search.search_places_structured",
            return_value={"found": True, "message": "Encontré 3 restaurantes italianos."},
        ) as mock_search, patch(
            "vectrax.user_memory.get_user_location", return_value=None,
        ):
            result = gw._maybe_execute_action_request(
                content="Quiero comer italiano cerca", user_id="tg:place_ok", lang="es",
                smart_route=_route(Strategy.RESOLVE_PLACES),
            )
        mock_search.assert_called_once()
        assert result == ("Encontré 3 restaurantes italianos.", "action_place_search")

    def test_narrates_instead_of_executing_when_not_confirmed(self):
        """No confirmada -> narra el estado real; NUNCA llama a la
        extracción ni a la integración (verifica el orden: capacidad
        primero, extracción/ejecución después, para un dominio ya
        conocido por el router)."""
        gw = ExternalGateway()
        with patch(
            "core.intent_ssot.resolve_intent",
            return_value=IntentDecision(primary_intent="place_search", speech_act="action"),
        ), patch(
            "core.self_observation.capability_status.get_capability_status",
            return_value=_status_entry(STATUS_CONDITIONAL),
        ), patch(
            "core.self_observation.capability_status.query_capability_status",
        ) as mock_query, patch(
            "core.self_observation.capability_status.render_status_block",
            return_value="Búsqueda de lugares: condicionada.",
        ), patch(
            "core.action_extraction.extract_action_params",
        ) as mock_extract, patch(
            "vectrax.integrations.place_search.search_places_structured",
        ) as mock_search:
            result = gw._maybe_execute_action_request(
                content="Búscame un restaurante cerca de mí", user_id="tg:place_blocked",
                lang="es", smart_route=_route(Strategy.RESOLVE_PLACES),
            )
        mock_extract.assert_not_called()
        mock_search.assert_not_called()
        assert result == ("Búsqueda de lugares: condicionada.", "capability_blocked")


class TestActionDispatchReminderInferredDomain:
    """El router no tiene Intent.REMINDER: el dominio se infiere DENTRO de
    la extracción, solo para estrategias genéricas."""

    def test_executes_reminder_when_confirmed(self):
        gw = ExternalGateway()
        params = ReminderParams(content="comprar toallas", when_text="mañana")
        with patch(
            "core.intent_ssot.resolve_intent",
            return_value=IntentDecision(primary_intent="local", speech_act="action"),
        ), patch(
            "core.self_observation.capability_status.get_capability_status",
            return_value=_status_entry(STATUS_CONFIRMED),
        ), patch(
            "core.action_extraction.extract_action_params", return_value=params,
        ), patch(
            "core.scheduler.add_task_structured",
            return_value={"id": 1, "message": "comprar toallas"},
        ) as mock_add:
            result = gw._maybe_execute_action_request(
                content="Recuérdame comprar toallas mañana", user_id="tg:12345",
                lang="es", smart_route=_route(Strategy.RESOLVE_ONLINE),
            )
        mock_add.assert_called_once()
        args, _ = mock_add.call_args
        assert args[0] == "tg:12345"
        assert args[1] == 12345  # chat_id derivado del user_id de Telegram
        assert args[2] == "comprar toallas"
        assert args[3] == "mañana"
        assert result is not None
        assert result[1] == "action_reminder"

    def test_narrates_instead_of_creating_task_when_not_confirmed(self):
        gw = ExternalGateway()
        params = ReminderParams(content="comprar toallas", when_text="mañana")
        with patch(
            "core.intent_ssot.resolve_intent",
            return_value=IntentDecision(primary_intent="local", speech_act="action"),
        ), patch(
            "core.self_observation.capability_status.get_capability_status",
            return_value=_status_entry(STATUS_CONDITIONAL),
        ), patch(
            "core.action_extraction.extract_action_params", return_value=params,
        ), patch(
            "core.self_observation.capability_status.query_capability_status",
        ), patch(
            "core.self_observation.capability_status.render_status_block",
            return_value="Recordatorios: condicionados.",
        ), patch(
            "core.scheduler.add_task_structured",
        ) as mock_add:
            result = gw._maybe_execute_action_request(
                content="Recuérdame comprar toallas mañana", user_id="tg:12345",
                lang="es", smart_route=_route(Strategy.RESOLVE_ONLINE),
            )
        mock_add.assert_not_called()
        assert result == ("Recordatorios: condicionados.", "capability_blocked")

    def test_no_action_domain_detected_falls_through(self):
        """extract_action_params devuelve None (dominio='none') -> el
        despacho no intercepta; el llamador sigue el pipeline normal."""
        gw = ExternalGateway()
        with patch(
            "core.intent_ssot.resolve_intent",
            return_value=IntentDecision(primary_intent="local", speech_act="action"),
        ), patch(
            "core.action_extraction.extract_action_params", return_value=None,
        ):
            result = gw._maybe_execute_action_request(
                content="cuéntame un chiste", user_id="tg:12345",
                lang="es", smart_route=_route(Strategy.RESOLVE_ONLINE),
            )
        assert result is None


class TestActionDispatchSkipsSpecificStrategies:
    def test_market_strategy_never_intercepted(self):
        """Estrategias ya específicas (market/identity/comando/etc.) no se
        interceptan — solo las genéricas donde el router no encontró ruta."""
        gw = ExternalGateway()
        with patch(
            "core.intent_ssot.resolve_intent",
            return_value=IntentDecision(primary_intent="market", speech_act="action"),
        ), patch(
            "core.action_extraction.extract_action_params",
        ) as mock_extract:
            result = gw._maybe_execute_action_request(
                content="compra 1 BTC ahora", user_id="tg:12345",
                lang="es", smart_route=_route(Strategy.RESOLVE_MARKET),
            )
        mock_extract.assert_not_called()
        assert result is None
