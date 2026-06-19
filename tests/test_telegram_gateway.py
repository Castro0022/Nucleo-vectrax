"""
Tests — Telegram Gateway
===========================
Valida el gateway de Telegram sin conexión real a la API:
  - Inicialización y validación de token
  - Parseo de updates
  - Integración con ExternalGateway y bus
  - Envío de respuestas (mock)
  - Manejo de updates sin texto
  - Estadísticas

Creado: 2026-03-19
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# NOTE: repo root is made importable by tests/conftest.py. Do NOT inject a
# hardcoded home-dir checkout here (it shadows the worktree under test on a
# case-insensitive FS and breaks hermetic isolation).

from core.operator.universal_bus import (
    Channels,
    get_universal_bus,
    reset_bus,
)
from core.operator.external_gateway import reset_gateway


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_update(
    update_id: int = 1,
    chat_id: int = 12345,
    user_id: int = 67890,
    username: str = "testuser",
    first_name: str = "Test",
    text: str = "Hola Vectrax",
) -> dict:
    """Crea un update de Telegram simulado."""
    return {
        "update_id": update_id,
        "message": {
            "message_id": 100,
            "from": {
                "id": user_id,
                "is_bot": False,
                "first_name": first_name,
                "username": username,
            },
            "chat": {
                "id": chat_id,
                "first_name": first_name,
                "username": username,
                "type": "private",
            },
            "date": 1710820000,
            "text": text,
        },
    }


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


@pytest.fixture
def gateway():
    """Crea un TelegramGateway con token fake y API mockeada."""
    from vectrax.telegram_gateway import TelegramGateway
    gw = TelegramGateway(token="FAKE_TOKEN_FOR_TESTING")
    # Mock el cliente HTTP para no hacer llamadas reales
    gw._client = MagicMock()
    return gw


# ---------------------------------------------------------------------------
# 1. Inicialización
# ---------------------------------------------------------------------------

class TestTelegramGatewayInit:
    """El gateway debe validar token e inicializarse correctamente."""

    def test_rejects_empty_token(self):
        from vectrax.telegram_gateway import TelegramGateway
        with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
            TelegramGateway(token="")

    def test_initializes_with_valid_token(self, gateway):
        assert gateway._token == "FAKE_TOKEN_FOR_TESTING"
        assert gateway._total_processed == 0
        assert gateway._offset == 0

    def test_base_url_contains_token(self, gateway):
        assert "FAKE_TOKEN_FOR_TESTING" in gateway._base_url


# ---------------------------------------------------------------------------
# 2. Parseo de updates
# ---------------------------------------------------------------------------

class TestUpdateProcessing:
    """El gateway debe parsear updates de Telegram correctamente."""

    def test_processes_text_message(self, gateway):
        """Un update con texto debe generar evento en el bus."""
        bus = get_universal_bus()
        captured = []
        bus.subscribe(
            Channels.EXTERNAL,
            lambda e: captured.append(e),
            subscriber_name="test.capture",
        )

        # Mock sendMessage para que no falle
        gateway._api_call = MagicMock(return_value={"message_id": 200})

        update = _make_update(text="Hola desde Telegram")
        gateway._process_update(update)

        assert gateway._total_processed == 1
        # Debe haber emitido eventos external.message_received y external.message_response
        received = [
            e for e in captured
            if e.event_type == "external.message_received"
        ]
        assert len(received) >= 1
        assert received[0].payload["content"] == "Hola desde Telegram"
        assert received[0].payload["channel"] == "telegram"

    def test_skips_update_without_message(self, gateway):
        """Un update sin campo message debe ser ignorado."""
        gateway._process_update({"update_id": 99})
        assert gateway._total_processed == 0

    def test_skips_message_without_text(self, gateway):
        """Un mensaje sin texto (ej: foto) debe ser ignorado."""
        update = {
            "update_id": 2,
            "message": {
                "message_id": 101,
                "from": {"id": 1, "first_name": "X"},
                "chat": {"id": 1, "type": "private"},
                "date": 1710820000,
                # sin campo "text"
            },
        }
        gateway._process_update(update)
        assert gateway._total_processed == 0

    def test_user_id_prefixed_with_tg(self, gateway):
        """El user_id enviado al ExternalGateway debe tener prefijo tg:."""
        bus = get_universal_bus()
        captured = []
        bus.subscribe(
            Channels.EXTERNAL,
            lambda e: captured.append(e),
            subscriber_name="test.capture_uid",
            filter_type="external.message_received",
        )

        gateway._api_call = MagicMock(return_value={"message_id": 200})
        update = _make_update(user_id=42, text="Test prefix")
        gateway._process_update(update)

        assert any(
            e.payload.get("user_id") == "tg:42" for e in captured
        )


# ---------------------------------------------------------------------------
# 3. Respuestas
# ---------------------------------------------------------------------------

class TestResponseSending:
    """El gateway debe enviar respuestas vía sendMessage."""

    def test_sends_response_back(self, gateway):
        """Después de procesar, debe llamar a sendMessage."""
        call_log = []

        def fake_api_call(method, **params):
            call_log.append((method, params))
            if method == "sendMessage":
                return {"message_id": 300}
            return None

        gateway._api_call = fake_api_call
        update = _make_update(text="Test response")
        gateway._process_update(update)

        send_calls = [c for c in call_log if c[0] == "sendMessage"]
        assert len(send_calls) == 1
        assert send_calls[0][1]["chat_id"] == 12345
        assert isinstance(send_calls[0][1]["text"], str)
        assert len(send_calls[0][1]["text"]) > 0

    def test_no_hardcoded_responses(self, gateway):
        """Si el núcleo no responde, debe usar el mensaje de inicialización."""
        call_log = []

        def fake_api_call(method, **params):
            call_log.append((method, params))
            if method == "sendMessage":
                return {"message_id": 301}
            return None

        gateway._api_call = fake_api_call
        update = _make_update(text="ping")
        gateway._process_update(update)

        send_calls = [c for c in call_log if c[0] == "sendMessage"]
        assert len(send_calls) == 1
        sent_text = send_calls[0][1]["text"]
        # No debe contener respuestas genéricas hardcodeadas
        assert "Mensaje recibido por Vectrax" not in sent_text
        assert "Vectrax procesará tu solicitud" not in sent_text
        assert "Registrado:" not in sent_text
        # Si no hay respuesta del núcleo, debe ser el único fallback permitido
        # o una respuesta real del resolver
        if "inicializando" in sent_text:
            assert sent_text == "Vectrax está inicializando pensamiento, intenta de nuevo."


# ---------------------------------------------------------------------------
# 4. Estadísticas
# ---------------------------------------------------------------------------

class TestGatewayStats:
    """El gateway debe reportar estadísticas correctas."""

    def test_initial_stats(self, gateway):
        stats = gateway.stats()
        assert stats["total_processed"] == 0
        assert stats["running"] is False
        assert stats["current_offset"] == 0

    def test_stats_after_processing(self, gateway):
        gateway._api_call = MagicMock(return_value={"message_id": 200})
        gateway._process_update(_make_update(text="Stat test"))
        stats = gateway.stats()
        assert stats["total_processed"] == 1


# ---------------------------------------------------------------------------
# 5. Truncado de mensajes largos
# ---------------------------------------------------------------------------

class TestMessageTruncation:
    """Mensajes mayores a 4096 chars deben truncarse."""

    def test_truncates_long_response(self, gateway):
        call_log = []

        def fake_api_call(method, **params):
            call_log.append((method, params))
            return {"message_id": 400}

        gateway._api_call = fake_api_call

        # Forzar respuesta larga
        long_text = "x" * 5000
        gateway._send_message(chat_id=1, text=long_text)

        send_calls = [c for c in call_log if c[0] == "sendMessage"]
        assert len(send_calls) == 1
        assert len(send_calls[0][1]["text"]) <= 4096
        assert send_calls[0][1]["text"].endswith("...")
