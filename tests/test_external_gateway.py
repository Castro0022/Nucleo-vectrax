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
    _resolve_query_domain,
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
        # Procedencia por-respuesta (Fase 1)
        assert "resolve_mode" in d
        assert "confidence" in d
        assert "evidence" in d
        assert d["evidence"] == {}


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

    def test_result_carries_provenance(self):
        """Fase 1: el objeto de respuesta lleva su procedencia adjunta
        (confianza + evidencia), sin depender de releer op_cycles."""
        gw = ExternalGateway()
        result = gw.receive_message(
            user_id="u_prov", content="¿qué opinas del mercado hoy?", channel="web",
        )
        assert isinstance(result, GatewayResult)
        assert result.processed is True
        assert 0.0 <= result.confidence <= 1.0
        assert isinstance(result.evidence, dict)
        assert "route" in result.evidence
        assert isinstance(result.resolve_mode, str)
        # Fase 5: capa de procedencia (personal / compartido / externo)
        assert result.evidence.get("layer") in {"personal", "shared", "external"}

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


# ---------------------------------------------------------------------------
# 10. DOMAIN-EVIDENCE GATE precedence (freight_logistics) over self-aware
# ---------------------------------------------------------------------------

class TestFreightDomainGatePrecedence:
    """Reproduce el incidente route_A a nivel receive_message: una consulta de
    freight_logistics debe resolverse por la compuerta de evidencia de dominio
    (grounded/abstención) ANTES de que el narrador SELF-AWARE pueda autorar
    afirmaciones no soportadas. Verifica la PRECEDENCIA de la compuerta.
    """

    _ROUTE_A_QUERY = (
        "\u00bfPor qu\u00e9 route_A y no route_B o route_C? Defiende esa "
        "decisi\u00f3n exclusivamente con la evidencia persistida en freight_logistics"
    )

    def test_explicit_evidence_request_returns_raw(self):
        """SOLO un pedido explícito de ver datos crudos ('muéstrame los datos/
        observaciones') resuelve por el volcado de evidencia freight, y precede
        al narrador self-aware. El criterio es el default; el volcado es la
        excepción bajo pedido explícito.
        """
        from unittest.mock import patch

        gw = ExternalGateway()
        FREIGHT = "FREIGHT_RAW_SENTINEL"
        SELFAWARE = "SELF_AWARE_FABRICATION_SENTINEL"
        query = "muéstrame los datos y observaciones de freight_logistics"

        with patch("intents.freight_intents.resolve_freight_query", return_value=FREIGHT), \
             patch("vectrax.self_context.is_self_referential", return_value=True), \
             patch("vectrax.self_context.resolve_self_aware", return_value=SELFAWARE), \
             patch("vectrax.response_auditor.run_audit", side_effect=lambda **kw: kw.get("response", "")), \
             patch("core.language_gate.enforce_language", side_effect=lambda text, *a, **k: text), \
             patch("core.conversation.presence_policy.apply_presence_policy", side_effect=lambda text, *a, **k: (text, False)):
            result = gw.receive_message(
                user_id="tg:test_evidence",
                content=query,
                channel="telegram",
            )

        assert FREIGHT in result.response, "el pedido explícito de datos no dio el volcado"
        assert SELFAWARE not in result.response, "el narrador self-aware rompió la precedencia"

    def test_domain_question_resolves_via_criterion(self):
        """Una pregunta de dominio SIN pedido explícito de datos → CRITERIO
        (build_criterion_result), nunca el volcado crudo. El criterio es la divisa.
        """
        from unittest.mock import patch
        from core.learn.criterion import CriterionResult, RenderAttempt

        gw = ExternalGateway()
        CRIT = "CRITERION_POSITION_SENTINEL"
        FREIGHT = "FREIGHT_RAW_SHOULD_NOT_APPEAR"
        _cr = CriterionResult("deterministic", CRIT, None, RenderAttempt(attempted=False))
        with patch("core.learn.criterion.detect_domain", return_value="freight_logistics"), \
             patch("core.learn.criterion.build_criterion_result", return_value=_cr), \
             patch("intents.freight_intents.resolve_freight_query", return_value=FREIGHT), \
             patch("vectrax.self_context.is_self_referential", return_value=True), \
             patch("vectrax.self_context.resolve_self_aware", return_value="SA"), \
             patch("vectrax.response_auditor.run_audit", side_effect=lambda **kw: kw.get("response", "")), \
             patch("core.language_gate.enforce_language", side_effect=lambda text, *a, **k: text), \
             patch("core.conversation.presence_policy.apply_presence_policy", side_effect=lambda text, *a, **k: (text, False)):
            result = gw.receive_message(
                user_id="tg:test_domain_crit",
                content="dime sobre freight logistics",
                channel="telegram",
            )
        assert CRIT in result.response            # criterio (divisa), no volcado
        assert FREIGHT not in result.response     # el volcado crudo NO aparece

    def test_non_freight_query_still_reaches_self_aware(self):
        """Una consulta self-referencial NO-freight sigue yendo al narrador
        self-aware (la compuerta no secuestra consultas ajenas al dominio).
        """
        from unittest.mock import patch

        gw = ExternalGateway()
        SELFAWARE = "SELF_AWARE_OK_SENTINEL"
        with patch("vectrax.self_context.is_self_referential", return_value=True), \
             patch("vectrax.self_context.resolve_self_aware", return_value=SELFAWARE), \
             patch("vectrax.response_auditor.run_audit", side_effect=lambda **kw: kw.get("response", "")), \
             patch("core.language_gate.enforce_language", side_effect=lambda text, *a, **k: text), \
             patch("core.conversation.presence_policy.apply_presence_policy", side_effect=lambda text, *a, **k: (text, False)):
            result = gw.receive_message(
                user_id="tg:test_selfaware",
                content="\u00bfqu\u00e9 son tus convergencias?",
                channel="telegram",
            )
        assert SELFAWARE in result.response

    def test_criterion_gate_precedes_self_aware(self):
        """Un pedido de OPINIÓN/criterio de dominio se resuelve por el Motor de
        Criterio Aprendido ANTES que el narrador self-aware (que antes fabricaba).
        """
        from unittest.mock import patch

        gw = ExternalGateway()
        CRIT = "CRITERION_OPINION_SENTINEL"
        SELFAWARE = "SELF_AWARE_SENTINEL_2"
        from core.learn.criterion import CriterionResult, RenderAttempt
        _cr = CriterionResult("deterministic", CRIT, None, RenderAttempt(attempted=False))
        # detect_criterion_request corre real (True sobre '¿qué opinas...?').
        # detect_domain/build_criterion_result se parchean para aislar la precedencia.
        with patch("core.learn.criterion.detect_domain", return_value="market"), \
             patch("core.learn.criterion.build_criterion_result", return_value=_cr), \
             patch("vectrax.self_context.is_self_referential", return_value=True), \
             patch("vectrax.self_context.resolve_self_aware", return_value=SELFAWARE), \
             patch("vectrax.response_auditor.run_audit", side_effect=lambda **kw: kw.get("response", "")), \
             patch("core.language_gate.enforce_language", side_effect=lambda text, *a, **k: text), \
             patch("core.conversation.presence_policy.apply_presence_policy", side_effect=lambda text, *a, **k: (text, False)):
            result = gw.receive_message(
                user_id="tg:test_criterion",
                content="\u00bfqu\u00e9 opinas de la bolsa seg\u00fan lo que aprendiste?",
                channel="telegram",
            )
        assert CRIT in result.response       # el criterio aprendido ganó
        assert SELFAWARE not in result.response  # el narrador self-aware fue omitido


# ---------------------------------------------------------------------------
# 11. _resolve_query_domain — dominio semántico con fallback léxico
# ---------------------------------------------------------------------------

class TestResolveQueryDomain:
    """El gate consume SemanticResult.domain (motor de intención) y cae a
    detect_domain() cuando la confianza es baja o no hay dominio semántico.
    Detrás de VX_SEMANTIC_DOMAIN. Tests herméticos: classify() y detect_domain()
    se inyectan, sin depender del modelo real ni de datos de gravity.
    """

    def test_semantic_high_confidence_wins_over_keyword(self, monkeypatch):
        import types
        monkeypatch.setenv("VX_SEMANTIC_DOMAIN", "1")
        monkeypatch.setattr(
            "core.semantic_classifier.classify",
            lambda t: types.SimpleNamespace(domain="cybersecurity", domain_confidence=0.72),
        )
        monkeypatch.setattr("core.learn.criterion.detect_domain", lambda t: "market")
        dom, conf, src = _resolve_query_domain("¿qué opinas de esto?")
        assert (dom, src) == ("cybersecurity", "semantic")
        assert conf == pytest.approx(0.72)

    def test_low_confidence_falls_back_to_keyword(self, monkeypatch):
        """Confianza semántica por debajo del piso → fallback a detect_domain()."""
        import types
        monkeypatch.setenv("VX_SEMANTIC_DOMAIN", "1")
        monkeypatch.setattr(
            "core.semantic_classifier.classify",
            lambda t: types.SimpleNamespace(domain="market", domain_confidence=0.20),
        )
        monkeypatch.setattr("core.learn.criterion.detect_domain", lambda t: "freight_logistics")
        dom, conf, src = _resolve_query_domain("algo ambiguo sin señal fuerte")
        assert (dom, src) == ("freight_logistics", "keyword")

    def test_no_semantic_domain_uses_keyword(self, monkeypatch):
        import types
        monkeypatch.setenv("VX_SEMANTIC_DOMAIN", "1")
        monkeypatch.setattr(
            "core.semantic_classifier.classify",
            lambda t: types.SimpleNamespace(domain="", domain_confidence=0.0),
        )
        monkeypatch.setattr("core.learn.criterion.detect_domain", lambda t: "market")
        dom, conf, src = _resolve_query_domain("precio de btc")
        assert (dom, src) == ("market", "keyword")

    def test_ambiguous_query_yields_no_domain(self, monkeypatch):
        """Consulta ambigua: ni semántico ni léxico detectan dominio → None.
        Evita que el gate secuestre chit-chat hacia un dominio ajeno.
        """
        import types
        monkeypatch.setenv("VX_SEMANTIC_DOMAIN", "1")
        monkeypatch.setattr(
            "core.semantic_classifier.classify",
            lambda t: types.SimpleNamespace(domain="", domain_confidence=0.0),
        )
        monkeypatch.setattr("core.learn.criterion.detect_domain", lambda t: None)
        dom, conf, src = _resolve_query_domain("no sé, tal vez mañana")
        assert dom is None
        assert conf == 0.0
        assert src == "none"

    def test_flag_off_skips_semantic(self, monkeypatch):
        """VX_SEMANTIC_DOMAIN=0 → no se invoca al clasificador; solo léxico."""
        import types
        calls = []

        def _record(t):
            calls.append(t)
            return types.SimpleNamespace(domain="market", domain_confidence=0.99)

        monkeypatch.setenv("VX_SEMANTIC_DOMAIN", "0")
        monkeypatch.setattr("core.semantic_classifier.classify", _record)
        monkeypatch.setattr("core.learn.criterion.detect_domain", lambda t: "freight_logistics")
        dom, conf, src = _resolve_query_domain("dame tu opinión")
        assert (dom, src) == ("freight_logistics", "keyword")
        assert calls == []  # el clasificador nunca se llamó

    def test_empty_content_yields_none(self):
        assert _resolve_query_domain("") == (None, 0.0, "none")

    def test_defensive_on_classifier_error(self, monkeypatch):
        """Si el clasificador lanza, cae a léxico sin propagar la excepción."""
        def _raise(t):
            raise RuntimeError("boom")

        monkeypatch.setenv("VX_SEMANTIC_DOMAIN", "1")
        monkeypatch.setattr("core.semantic_classifier.classify", _raise)
        monkeypatch.setattr("core.learn.criterion.detect_domain", lambda t: "market")
        dom, conf, src = _resolve_query_domain("una consulta cualquiera")
        assert (dom, src) == ("market", "keyword")
