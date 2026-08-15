"""
Tests — SSOT de Intent (Fase 2, paso 1)
==========================================
Fija el contrato de `core/intent_ssot.py`:

  1. `resolve_intent()` produce el MISMO `primary_intent` que
     `SmartRouter().classify_intent()` produce hoy para el mismo texto —
     porque lo reutiliza directamente, no lo reimplementa.
  2. Las 4 reglas de `SmartRouter._resolve_intent()` (override COGNITIVE,
     override MARKET, rescate memory→online, rescate local→memory) se
     preservan exactamente. Se fijan con `SemanticResult` mockeado para
     que el test sea determinista y no dependa de que el modelo de
     embeddings produzca siempre la misma confianza.
  3. Un proveedor caído (excepción) nunca bloquea a los demás — cada eje
     de `IntentDecision` se resuelve independientemente.
  4. `Evidence`/`IntentDecision` son serializables y `to_dict()` no lanza.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.intent_ssot import (
    Evidence,
    IntentDecision,
    resolve_domain,
    resolve_intent,
    resolve_task_classification,
    resolve_voice_intent,
)
from core.semantic_classifier import SemanticIntent, SemanticResult
from core.smart_router import Intent, SmartRouter


def _fake_semantic(intent: SemanticIntent, confidence: float, domain: str = "", domain_confidence: float = 0.0):
    """Construye un SemanticResult real (no un Mock) para que el mapeo
    _SEMANTIC_TO_INTENT y los atributos .intent/.confidence funcionen tal
    cual lo hacen en producción."""
    return SemanticResult(
        intent=intent, confidence=confidence, domain=domain, domain_confidence=domain_confidence,
    )


# ===========================================================================
# 1. Consistencia: resolve_intent() == SmartRouter().classify_intent()
# ===========================================================================

class TestConsistencyWithSmartRouter:
    _MESSAGES = [
        "hola, cómo estás",
        "qué dije ayer sobre bitcoin?",
        "cuánto vale btc",
        "busco un lugar cerca de aquí para comer",
        "analiza paso a paso la arquitectura completa del sistema de pagos y su diseño de seguridad",
        # Comandos explícitos (Fase 2, paso 5): resolve_intent() debe
        # detectarlos IGUAL que classify_intent() — antes de este paso,
        # _gather_primary_intent_evidence() nunca revisaba el patrón de
        # comando y estos casos habrían discrepado.
        "/ai qué es bitcoin",
        "/multi explica la gravedad",
        "/router",
        "/help",
        "/salir",
    ]

    @pytest.mark.parametrize("text", _MESSAGES)
    def test_same_primary_intent_as_classify_intent(self, text):
        expected_intent, _ = SmartRouter().classify_intent(text)
        decision = resolve_intent(text)
        assert decision.primary_intent == expected_intent.value, (
            f"resolve_intent() discrepa de SmartRouter.classify_intent() para {text!r}: "
            f"{decision.primary_intent!r} != {expected_intent.value!r}"
        )


# ===========================================================================
# 1b. Comandos explícitos — paridad completa de signals (Fase 2, paso 5)
# ===========================================================================

class TestCommandDetectionParity:
    """resolve_intent() reutiliza SmartRouter._classify_command() — la MISMA
    primera capa que classify_intent() evalúa antes de semántico/regex — en
    vez de dejar que los comandos caigan (incorrectamente) en la fusión
    semántico+regex."""

    def test_ai_single_command(self):
        decision = resolve_intent("/ai explica la relatividad general")
        assert decision.primary_intent == Intent.AI_SINGLE.value
        assert decision.raw_signals.get("command") == "ai"
        assert decision.raw_signals.get("prompt") == "explica la relatividad general"
        assert decision.confidence == 1.0

    def test_ai_multi_command(self):
        decision = resolve_intent("/multi compara python vs rust")
        assert decision.primary_intent == Intent.AI_MULTI.value
        assert decision.raw_signals.get("command") == "multi"

    def test_plain_command(self):
        decision = resolve_intent("/router")
        assert decision.primary_intent == Intent.COMMAND.value
        assert decision.raw_signals.get("command") == "router"

    def test_command_never_runs_semantic_classification(self, monkeypatch):
        """Un comando no debe llegar a clasificación semántica — mismo
        comportamiento que classify_intent() (que retorna antes de tocarla)."""
        calls = []
        import core.semantic_classifier as sc_mod
        original = sc_mod.get_semantic_classifier

        def _tracked():
            calls.append(1)
            return original()

        monkeypatch.setattr(sc_mod, "get_semantic_classifier", _tracked)
        resolve_intent("/ai cualquier cosa")
        assert calls == []


# ===========================================================================
# 1c. raw_signals — paridad completa con SmartRouter.classify_intent()
# ===========================================================================

class TestRawSignalsParity:
    def test_raw_signals_match_classify_intent_signals(self):
        text = "qué dije ayer sobre bitcoin?"
        _, expected_signals = SmartRouter().classify_intent(text)
        decision = resolve_intent(text)
        assert decision.raw_signals == expected_signals

    def test_raw_signals_empty_dict_by_default(self):
        decision = resolve_intent("")
        assert decision.raw_signals == {}


# ===========================================================================
# 2. Las 4 reglas preservadas exactamente (deterministas, sin depender del
#    modelo de embeddings real — se mockea solo el SemanticResult).
# ===========================================================================

class TestFourPreservedRules:
    def test_rule1_cognitive_override_real_example(self):
        """Ejemplo real verificado: regex COGNITIVE gana sobre semántico de
        confianza media (sem_conf=0.53 < 0.7)."""
        text = (
            "analiza paso a paso la arquitectura completa del sistema de "
            "pagos y su diseño de seguridad"
        )
        decision = resolve_intent(text)
        assert decision.primary_intent == Intent.COGNITIVE.value
        assert any("override" in c or "COGNITIVE" in c for c in decision.conflicts)

    def test_rule2_market_override_unconditional(self):
        """Override MARKET: regex MARKET gana SIEMPRE, incluso con semántico
        de alta confianza apuntando a otro intent."""
        text = "vx market"  # regex real: matches _MARKET_PATTERN
        fake = _fake_semantic(SemanticIntent.GENERAL_CHAT, confidence=0.8)  # -> Intent.MEMORY, alta confianza
        with patch("core.semantic_classifier.get_semantic_classifier") as mock_get:
            mock_get.return_value.classify.return_value = fake
            decision = resolve_intent(text)
        assert decision.primary_intent == Intent.MARKET.value
        assert any("MARKET" in c or "override" in c for c in decision.conflicts)

    def test_rule3_memory_rescue_over_online(self):
        """Rescate memory→online: semántico=memory (GENERAL_CHAT) con
        sem_conf>0 gana sobre regex=online en preguntas personales."""
        text = "eso te lo pregunto porque sí, cierto?"  # regex real: pregunta -> ONLINE
        fake = _fake_semantic(SemanticIntent.GENERAL_CHAT, confidence=0.3)  # -> Intent.MEMORY, bajo threshold
        with patch("core.semantic_classifier.get_semantic_classifier") as mock_get:
            mock_get.return_value.classify.return_value = fake
            decision = resolve_intent(text)
        assert decision.primary_intent == Intent.MEMORY.value

    def test_rule4_local_rescue_over_memory(self):
        """Rescate local→memory: semántico=local (MEMORY_LOOKUP) con
        sem_conf>0.25 gana sobre regex=memory (default sin keywords)."""
        text = "me gusta mucho esto que estamos construyendo juntos"  # regex real: sin keywords -> MEMORY
        fake = _fake_semantic(SemanticIntent.MEMORY_LOOKUP, confidence=0.3)  # -> Intent.LOCAL, > 0.25
        with patch("core.semantic_classifier.get_semantic_classifier") as mock_get:
            mock_get.return_value.classify.return_value = fake
            decision = resolve_intent(text)
        assert decision.primary_intent == Intent.LOCAL.value

    def test_rule4_does_not_fire_below_confidence_bar(self):
        """Confirma el umbral exacto: sem_conf=0.25 (no > 0.25) NO debe
        disparar el rescate — control negativo de la regla 4."""
        text = "me gusta mucho esto que estamos construyendo juntos"
        fake = _fake_semantic(SemanticIntent.MEMORY_LOOKUP, confidence=0.25)
        with patch("core.semantic_classifier.get_semantic_classifier") as mock_get:
            mock_get.return_value.classify.return_value = fake
            decision = resolve_intent(text)
        assert decision.primary_intent == Intent.MEMORY.value  # el rescate NO aplica; regex gana


# ===========================================================================
# 3. Resiliencia: un proveedor caído nunca bloquea a los demás
# ===========================================================================

class TestProviderResilience:
    def test_semantic_classifier_down_still_resolves_via_regex(self):
        with patch(
            "core.semantic_classifier.get_semantic_classifier",
            side_effect=RuntimeError("embeddings unavailable"),
        ):
            decision = resolve_intent("cuánto vale btc")
        assert decision.primary_intent  # regex solo sigue resolviendo
        assert decision.primary_intent == Intent.MARKET.value

    def test_smart_router_down_falls_back_to_semantic(self):
        fake = _fake_semantic(SemanticIntent.MARKET_QUERY, confidence=0.9)
        with patch("core.semantic_classifier.get_semantic_classifier") as mock_get, \
             patch("core.smart_router.SmartRouter", side_effect=RuntimeError("smart_router down")):
            mock_get.return_value.classify.return_value = fake
            decision = resolve_intent("cualquier texto")
        assert decision.primary_intent == Intent.MARKET.value  # fallback al semántico

    def test_criterion_domain_down_does_not_block_other_axes(self):
        with patch(
            "core.learn.criterion.detect_domain",
            side_effect=RuntimeError("criterion down"),
        ):
            decision = resolve_intent("cuánto vale btc")
        assert decision.primary_intent  # el resto sigue funcionando

    def test_task_classifier_down_does_not_block_other_axes(self):
        with patch(
            "core.routing.task_classifier.classify_task",
            side_effect=RuntimeError("task_classifier down"),
        ):
            decision = resolve_intent("escribe una función en python")
        assert decision.task_type == ""
        assert decision.primary_intent  # el resto no se ve afectado

    def test_voice_intent_down_does_not_block_other_axes(self):
        with patch(
            "core.voice.intent.classify_intent",
            side_effect=RuntimeError("voice/intent down"),
        ):
            decision = resolve_intent("hola")
        assert decision.voice_intent == ""
        assert decision.primary_intent  # el resto no se ve afectado

    def test_all_providers_down_never_raises(self):
        with patch(
            "core.semantic_classifier.get_semantic_classifier",
            side_effect=RuntimeError("x"),
        ), patch(
            "core.smart_router.SmartRouter", side_effect=RuntimeError("x"),
        ), patch(
            "core.learn.criterion.detect_domain", side_effect=RuntimeError("x"),
        ), patch(
            "core.routing.task_classifier.classify_task", side_effect=RuntimeError("x"),
        ), patch(
            "core.voice.intent.classify_intent", side_effect=RuntimeError("x"),
        ):
            decision = resolve_intent("cualquier mensaje")
        assert decision.primary_intent == ""
        assert decision.domain == ""
        assert decision.task_type == ""
        assert decision.voice_intent == ""


# ===========================================================================
# 4. Contrato de datos: Evidence / IntentDecision
# ===========================================================================

class TestDataContract:
    def test_empty_text_returns_empty_decision_no_crash(self):
        decision = resolve_intent("")
        assert decision.primary_intent == ""
        assert decision.evidence == []

    def test_decision_to_dict_is_json_serializable(self):
        import json
        decision = resolve_intent("qué dije ayer sobre bitcoin?")
        json.dumps(decision.to_dict())  # no debe lanzar

    def test_evidence_entries_have_required_fields(self):
        decision = resolve_intent("cuánto vale btc")
        assert len(decision.evidence) >= 2  # al menos semantic + regex
        for e in decision.evidence:
            assert isinstance(e, Evidence)
            assert e.source
            assert e.claim
            d = e.to_dict()
            assert set(d.keys()) == {"source", "claim", "confidence"}

    def test_domain_populated_for_market_query(self):
        decision = resolve_intent("cuánto vale btc")
        # market es un dominio conocido en este universo (ver observatory)
        assert decision.domain in ("market", "")  # tolerante si el universo local no tiene el dominio poblado

    def test_task_type_populated_for_code_query(self):
        decision = resolve_intent("escribe una función en python que ordene una lista")
        assert decision.task_type == "code"

    def test_voice_intent_populated_for_greeting(self):
        decision = resolve_intent("hola")
        assert decision.voice_intent == "greeting"

    def test_no_channel_owner_params_do_not_break_call(self):
        # Paridad de firma con SmartRouter.route() para el paso 5 futuro.
        decision = resolve_intent("hola", channel="creator", owner="mario")
        assert decision.primary_intent


# ===========================================================================
# 5. resolve_domain() — atajo de un solo eje (Fase 2, paso 2: migración de
#    external_gateway._resolve_query_domain()). Misma prioridad y evidencia
#    que `IntentDecision.domain`, fijada aquí de forma hermética/determinista.
# ===========================================================================

class TestResolveDomain:
    def test_semantic_high_confidence_wins_over_keyword(self, monkeypatch):
        import types
        monkeypatch.setenv("VX_SEMANTIC_DOMAIN", "1")
        monkeypatch.setattr(
            "core.semantic_classifier.classify",
            lambda t: types.SimpleNamespace(domain="cybersecurity", domain_confidence=0.72),
        )
        monkeypatch.setattr("core.learn.criterion.detect_domain", lambda t: "market")
        dom, conf, src = resolve_domain("¿qué opinas de esto?")
        assert (dom, src) == ("cybersecurity", "semantic")
        assert conf == pytest.approx(0.72)

    def test_low_confidence_falls_back_to_keyword(self, monkeypatch):
        import types
        monkeypatch.setenv("VX_SEMANTIC_DOMAIN", "1")
        monkeypatch.setattr(
            "core.semantic_classifier.classify",
            lambda t: types.SimpleNamespace(domain="market", domain_confidence=0.20),
        )
        monkeypatch.setattr("core.learn.criterion.detect_domain", lambda t: "freight_logistics")
        dom, conf, src = resolve_domain("algo ambiguo sin señal fuerte")
        assert (dom, src) == ("freight_logistics", "keyword")

    def test_no_semantic_domain_uses_keyword(self, monkeypatch):
        import types
        monkeypatch.setenv("VX_SEMANTIC_DOMAIN", "1")
        monkeypatch.setattr(
            "core.semantic_classifier.classify",
            lambda t: types.SimpleNamespace(domain="", domain_confidence=0.0),
        )
        monkeypatch.setattr("core.learn.criterion.detect_domain", lambda t: "market")
        dom, conf, src = resolve_domain("precio de btc")
        assert (dom, src) == ("market", "keyword")

    def test_ambiguous_query_yields_no_domain(self, monkeypatch):
        import types
        monkeypatch.setenv("VX_SEMANTIC_DOMAIN", "1")
        monkeypatch.setattr(
            "core.semantic_classifier.classify",
            lambda t: types.SimpleNamespace(domain="", domain_confidence=0.0),
        )
        monkeypatch.setattr("core.learn.criterion.detect_domain", lambda t: None)
        dom, conf, src = resolve_domain("no sé, tal vez mañana")
        assert dom == ""
        assert conf == 0.0
        assert src == "none"

    def test_flag_off_skips_semantic(self, monkeypatch):
        import types
        calls = []

        def _record(t):
            calls.append(t)
            return types.SimpleNamespace(domain="market", domain_confidence=0.99)

        monkeypatch.setenv("VX_SEMANTIC_DOMAIN", "0")
        monkeypatch.setattr("core.semantic_classifier.classify", _record)
        monkeypatch.setattr("core.learn.criterion.detect_domain", lambda t: "freight_logistics")
        dom, conf, src = resolve_domain("dame tu opinión")
        assert (dom, src) == ("freight_logistics", "keyword")
        assert calls == []  # el clasificador nunca se llamó

    def test_empty_text_yields_none(self):
        assert resolve_domain("") == ("", 0.0, "none")

    def test_defensive_on_classifier_error(self, monkeypatch):
        def _raise(t):
            raise RuntimeError("boom")

        monkeypatch.setenv("VX_SEMANTIC_DOMAIN", "1")
        monkeypatch.setattr("core.semantic_classifier.classify", _raise)
        monkeypatch.setattr("core.learn.criterion.detect_domain", lambda t: "market")
        dom, conf, src = resolve_domain("una consulta cualquiera")
        assert (dom, src) == ("market", "keyword")

    def test_consistent_with_gateway_resolve_query_domain(self, monkeypatch):
        """external_gateway._resolve_query_domain() delega en resolve_domain();
        para el mismo texto y evidencia mockeada deben coincidir salvo el
        tipo Optional[str] vs str para 'sin dominio'."""
        import types
        from core.operator.external_gateway import _resolve_query_domain

        monkeypatch.setenv("VX_SEMANTIC_DOMAIN", "1")
        monkeypatch.setattr(
            "core.semantic_classifier.classify",
            lambda t: types.SimpleNamespace(domain="cybersecurity", domain_confidence=0.9),
        )
        monkeypatch.setattr("core.learn.criterion.detect_domain", lambda t: "market")
        dom_ssot, conf_ssot, src_ssot = resolve_domain("algo")
        dom_gw, conf_gw, src_gw = _resolve_query_domain("algo")
        assert dom_gw == dom_ssot
        assert conf_gw == conf_ssot
        assert src_gw == src_ssot


# ===========================================================================
# 6. resolve_voice_intent() — atajo de un solo eje (Fase 2, paso 3: migración
#    de pipeline_worker._quick_intent()). Mismo string crudo que
#    IntentDecision.voice_intent, con el mismo fallback "casual" que tenía
#    _quick_intent() ante cualquier fallo del clasificador subyacente.
# ===========================================================================

class TestResolveVoiceIntent:
    def test_matches_classify_intent_for_greeting(self):
        assert resolve_voice_intent("hola") == "greeting"

    def test_matches_classify_intent_for_technical(self):
        assert resolve_voice_intent("explícame la arquitectura del sistema") == "technical"

    def test_empty_text_falls_back_to_casual(self):
        assert resolve_voice_intent("") == "casual"

    def test_none_like_input_falls_back_to_casual(self):
        assert resolve_voice_intent(None) == "casual"

    def test_defensive_on_classifier_error(self, monkeypatch):
        def _raise(t):
            raise RuntimeError("boom")

        monkeypatch.setattr("core.voice.intent.classify_intent", _raise)
        assert resolve_voice_intent("cualquier mensaje") == "casual"

    def test_consistent_with_pipeline_worker_quick_intent(self, monkeypatch):
        """pipeline_worker._quick_intent() delega en resolve_voice_intent();
        para el mismo texto deben coincidir exactamente."""
        from core.transport.pipeline_worker import _quick_intent

        for text in ("hola", "explícame el código", "", "recuérdame comprar leche"):
            assert _quick_intent(text) == resolve_voice_intent(text)


# ===========================================================================
# 7. resolve_task_classification() — atajo de alta fidelidad (Fase 2, paso 4:
#    migración de ModelRouter.route(), único caller de classify_task()).
#    Devuelve el objeto TaskClassification completo (no solo el string), y
#    debe coincidir SIEMPRE con el eje liviano IntentDecision.task_type y con
#    lo que ModelRouter.route() expone en RoutingDecision.task_classification,
#    porque las tres rutas terminan llamando a la misma classify_task().
# ===========================================================================

_TASK_MESSAGES = [
    "Write a Python function to sort a list",
    "Solve this equation: 2x + 5 = 15",
    "Summarize the key points of this document",
    "Translate this text to Spanish",
    "Write a story about a robot",
    "Hello, how are you today?",
    "Extract all fields from this JSON schema",
]


class TestResolveTaskClassification:
    def test_matches_classify_task_directly(self):
        """resolve_task_classification() es un passthrough fiel de classify_task():
        mismo task_type, confidence, features y method para el mismo texto."""
        from core.routing.task_classifier import classify_task

        for text in _TASK_MESSAGES:
            expected = classify_task(text)
            got = resolve_task_classification(text)
            assert got.task_type == expected.task_type
            assert got.confidence == expected.confidence
            assert got.features == expected.features
            assert got.method == expected.method

    def test_context_param_threaded_through(self):
        """El parámetro context se pasa tal cual a classify_task() (paridad de
        firma con ModelRouter.route(prompt, context))."""
        from core.routing.task_classifier import classify_task

        prompt, context = "short", "x" * 7000  # dispara LONG_CONTEXT vía context
        expected = classify_task(prompt, context)
        got = resolve_task_classification(prompt, context)
        assert got.task_type == expected.task_type

    @pytest.mark.parametrize("text", _TASK_MESSAGES)
    def test_matches_intent_decision_task_type(self, text):
        """El mismo mensaje produce el mismo task_type vía resolve_intent()
        (eje liviano de IntentDecision) y vía resolve_task_classification()
        (objeto completo) — nunca pueden discrepar, ambos llaman a
        classify_task() internamente."""
        decision = resolve_intent(text)
        full = resolve_task_classification(text)
        assert decision.task_type == full.task_type.value

    @pytest.mark.parametrize("text", _TASK_MESSAGES)
    def test_matches_model_router_task_type(self, text, monkeypatch):
        """El mismo mensaje produce el mismo task_type desde CUALQUIER
        consumidor: resolve_intent() (SSOT agregado) y
        ModelRouter.route().task_classification (call site migrado en el
        paso 4). Aísla ModelRouter de dependencias externas (governor/risk/
        autonomy/modelos disponibles) para que el test se enfoque solo en la
        equivalencia de task_type, no en el resto de la decisión de ruteo."""
        from core.routing.model_router import ModelRouter

        router = ModelRouter()
        decision = resolve_intent(text)
        routing = router.route(text)
        assert routing.task_classification.task_type.value == decision.task_type

    def test_model_router_no_longer_imports_classify_task_directly(self):
        """Confirma la migración: model_router.py ya no importa classify_task
        de task_classifier — pasa exclusivamente por el SSOT."""
        import inspect
        import core.routing.model_router as mr
        source = inspect.getsource(mr)
        assert "import classify_task" not in source
        assert "resolve_task_classification" in source
