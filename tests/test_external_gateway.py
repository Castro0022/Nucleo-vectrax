"""
Tests — External Gateway
==========================
Valida el flujo completo del gateway externo:
  - Emisión de eventos al Universal Bus
  - Registro en ledger
  - Respuesta correcta al caller
  - Sin acceso directo al núcleo
  - Validación de canales
  - Manejo de mensajes vacíos

Creado: 2026-03-19
"""

from __future__ import annotations

import os
import sys
import time

import pytest

# NOTE: repo root is made importable by tests/conftest.py. Do NOT inject a
# hardcoded home-dir checkout here (it shadows the worktree under test on a
# case-insensitive FS and breaks hermetic isolation).

from core.operator.universal_bus import (
    BusEvent,
    Channels,
    EventPriority,
    get_universal_bus,
    reset_bus,
)
from core.operator.external_gateway import (
    ALLOWED_CHANNELS,
    DEFAULT_CHANNEL,
    ExternalGateway,
    GatewayResult,
    get_external_gateway,
    reset_gateway,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_singletons():
    """Reset singletons before each test."""
    reset_bus()
    reset_gateway()
    yield
    reset_bus()
    reset_gateway()


# ---------------------------------------------------------------------------
# 1. GatewayResult
# ---------------------------------------------------------------------------

class TestGatewayResult:
    """GatewayResult debe ser serializable y contener todos los campos."""

    def test_default_values(self):
        r = GatewayResult()
        assert r.event_id == ""
        assert r.processed is False
        assert r.error == ""

    def test_to_dict(self):
        r = GatewayResult(
            event_id="abc123",
            user_id="user1",
            channel="web",
            response="Hola",
            timestamp=1234567890.0,
            processed=True,
        )
        d = r.to_dict()
        assert d["event_id"] == "abc123"
        assert d["user_id"] == "user1"
        assert d["response"] == "Hola"
        assert d["processed"] is True


# ---------------------------------------------------------------------------
# 2. ExternalGateway — Emisión de eventos
# ---------------------------------------------------------------------------

class TestGatewayEventEmission:
    """El gateway debe emitir eventos external.message_received y
    external.message_response al Universal Bus."""

    def test_emits_message_received_event(self):
        bus = get_universal_bus()
        captured = []

        bus.subscribe(
            Channels.EXTERNAL,
            lambda e: captured.append(e),
            subscriber_name="test.capture",
            filter_type="external.message_received",
        )

        gw = ExternalGateway()
        gw.receive_message(user_id="u1", content="Hola Vectrax", channel="web")

        received = [e for e in captured if e.event_type == "external.message_received"]
        assert len(received) >= 1
        assert received[0].payload["user_id"] == "u1"
        assert received[0].payload["content"] == "Hola Vectrax"
        assert received[0].payload["channel"] == "web"

    def test_emits_message_response_event(self):
        bus = get_universal_bus()
        captured = []

        bus.subscribe(
            Channels.EXTERNAL,
            lambda e: captured.append(e),
            subscriber_name="test.capture_resp",
            filter_type="external.message_response",
        )

        gw = ExternalGateway()
        gw.receive_message(user_id="u2", content="Test message", channel="telegram")

        responses = [e for e in captured if e.event_type == "external.message_response"]
        assert len(responses) >= 1
        assert responses[0].payload["user_id"] == "u2"
        assert "response" in responses[0].payload

    def test_events_have_correlation_id(self):
        bus = get_universal_bus()
        all_events = []

        bus.subscribe(
            Channels.EXTERNAL,
            lambda e: all_events.append(e),
            subscriber_name="test.all",
        )

        gw = ExternalGateway()
        result = gw.receive_message(user_id="u3", content="Correlation test")

        # Both events should have the same correlation_id
        correlation_ids = {e.metadata.get("correlation_id") for e in all_events}
        assert result.event_id in correlation_ids


# ---------------------------------------------------------------------------
# 3. Validación de canales
# ---------------------------------------------------------------------------

class TestGatewayChannelValidation:
    """El gateway debe validar y normalizar canales."""

    def test_allowed_channels(self):
        assert "web" in ALLOWED_CHANNELS
        assert "telegram" in ALLOWED_CHANNELS
        assert "api" in ALLOWED_CHANNELS
        assert "webhook" in ALLOWED_CHANNELS
        assert "custom" in ALLOWED_CHANNELS

    def test_invalid_channel_defaults_to_web(self):
        gw = ExternalGateway()
        result = gw.receive_message(
            user_id="u4", content="Test", channel="invalid_channel",
        )
        assert result.channel == DEFAULT_CHANNEL

    def test_valid_channel_preserved(self):
        gw = ExternalGateway()
        result = gw.receive_message(
            user_id="u5", content="Test", channel="telegram",
        )
        assert result.channel == "telegram"


# ---------------------------------------------------------------------------
# 4. Manejo de mensajes vacíos
# ---------------------------------------------------------------------------

class TestGatewayEmptyMessage:
    """El gateway debe rechazar mensajes vacíos."""

    def test_empty_content_rejected(self):
        gw = ExternalGateway()
        result = gw.receive_message(user_id="u6", content="")
        assert result.processed is False
        assert result.error == "Empty message content"

    def test_whitespace_content_rejected(self):
        gw = ExternalGateway()
        result = gw.receive_message(user_id="u7", content="   ")
        assert result.processed is False
        assert result.error == "Empty message content"


# ---------------------------------------------------------------------------
# 5. Respuesta y procesamiento
# ---------------------------------------------------------------------------

class TestGatewayResponse:
    """El gateway debe devolver una respuesta procesada."""

    def test_returns_gateway_result(self):
        gw = ExternalGateway()
        result = gw.receive_message(
            user_id="u8", content="Dime algo", channel="web",
        )
        assert isinstance(result, GatewayResult)
        assert result.processed is True
        assert result.event_id != ""
        assert result.user_id == "u8"
        assert result.timestamp > 0

    def test_response_comes_from_core_only(self):
        """La respuesta debe venir del núcleo o estar vacía.
        No debe contener texto hardcodeado genérico."""
        gw = ExternalGateway()
        result = gw.receive_message(user_id="u9", content="Hola")
        # Si hay respuesta, no debe ser texto genérico hardcodeado
        assert "Mensaje recibido. Vectrax procesará tu solicitud" not in result.response
        assert not result.response.startswith("Registrado:")


# ---------------------------------------------------------------------------
# 6. Sin acceso directo al núcleo
# ---------------------------------------------------------------------------

class TestGatewayNucleusIsolation:
    """El gateway NO debe importar ni acceder al núcleo directamente."""

    def test_no_nucleus_import_in_gateway(self):
        import inspect
        from core.operator import external_gateway

        source = inspect.getsource(external_gateway)
        # No debe importar nucleus directamente
        assert "from core.operator.nucleus import" not in source
        assert "nucleus.get_nucleus" not in source
        assert "Nucleus(" not in source

    def test_all_communication_via_bus(self):
        """Verify the gateway uses the bus for all event communication."""
        import inspect
        from core.operator import external_gateway

        source = inspect.getsource(external_gateway)
        # Debe usar el bus
        assert "self._bus.emit(" in source
        # Debe usar ledger
        assert "ledger.record_event(" in source


# ---------------------------------------------------------------------------
# 7. Estadísticas
# ---------------------------------------------------------------------------

class TestGatewayStats:
    """El gateway debe mantener estadísticas correctas."""

    def test_stats_initial(self):
        gw = ExternalGateway()
        s = gw.stats()
        assert s["total_received"] == 0
        assert s["total_responded"] == 0
        assert s["total_errors"] == 0

    def test_stats_after_messages(self):
        gw = ExternalGateway()
        gw.receive_message(user_id="u10", content="Msg 1")
        gw.receive_message(user_id="u10", content="Msg 2")
        s = gw.stats()
        assert s["total_received"] == 2
        assert s["total_responded"] == 2


# ---------------------------------------------------------------------------
# 8. Singleton
# ---------------------------------------------------------------------------

class TestGatewaySingleton:
    """get_external_gateway() debe retornar siempre la misma instancia."""

    def test_singleton_returns_same_instance(self):
        gw1 = get_external_gateway()
        gw2 = get_external_gateway()
        assert gw1 is gw2

    def test_reset_creates_new_instance(self):
        gw1 = get_external_gateway()
        reset_gateway()
        gw2 = get_external_gateway()
        assert gw1 is not gw2


# ---------------------------------------------------------------------------
# 9. Bus history — eventos registrados
# ---------------------------------------------------------------------------

class TestGatewayBusHistory:
    """Los eventos del gateway deben quedar en el historial del bus."""

    def test_events_in_bus_history(self):
        bus = get_universal_bus()
        gw = ExternalGateway()
        gw.receive_message(user_id="u11", content="History test", channel="api")

        history = bus.get_history(channel=Channels.EXTERNAL)
        types = [e.event_type for e in history]
        assert "external.message_received" in types
        assert "external.message_response" in types
