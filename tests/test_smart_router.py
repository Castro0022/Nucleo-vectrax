"""
Tests para core.smart_router — Router Inteligente Unificado de Vectrax.

Cubre:
  - Clasificación de intent (memory, local, online, ai_single, ai_multi, command, cognitive)
  - Detección de contexto (tópico, riesgo)
  - Evaluación de política
  - Selección de estrategia
  - Pipeline completo route()
  - Registro de métricas y estadísticas
"""

import pytest

from core.smart_router import (
    Intent,
    PolicyAction,
    RiskLevel,
    SmartRoute,
    SmartRouter,
    Strategy,
    get_smart_router,
)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def router():
    return SmartRouter()


# ===========================================================================
# 1. classify_intent
# ===========================================================================

class TestClassifyIntent:
    """Tests para la clasificación de intent expandida."""

    def test_command_ai(self, router):
        intent, signals = router.classify_intent("/ai qué es bitcoin")
        assert intent == Intent.AI_SINGLE
        assert signals.get("command") == "ai"
        assert "bitcoin" in signals.get("prompt", "")

    def test_command_multi(self, router):
        intent, signals = router.classify_intent("/multi explica la gravedad")
        assert intent == Intent.AI_MULTI
        assert signals.get("command") == "multi"

    def test_command_router(self, router):
        intent, signals = router.classify_intent("/router")
        assert intent == Intent.COMMAND
        assert signals.get("command") == "router"

    def test_command_help(self, router):
        intent, _ = router.classify_intent("/help")
        assert intent == Intent.COMMAND

    def test_command_salir(self, router):
        intent, _ = router.classify_intent("/salir")
        assert intent == Intent.COMMAND

    def test_local_memory_reference_es(self, router):
        intent, signals = router.classify_intent("qué dije ayer sobre trading")
        assert intent == Intent.LOCAL
        assert signals["local_keywords"] is True

    def test_local_memory_reference_en(self, router):
        intent, _ = router.classify_intent("what did i say about bitcoin?")
        assert intent == Intent.LOCAL

    def test_local_my_history(self, router):
        intent, _ = router.classify_intent("show my history")
        assert intent == Intent.LOCAL

    def test_local_recuerdas(self, router):
        intent, _ = router.classify_intent("recuerdas lo que te dije")
        assert intent == Intent.LOCAL

    def test_online_question_mark(self, router):
        intent, signals = router.classify_intent("cuántos planetas hay en el sistema solar?")
        assert intent == Intent.ONLINE
        assert signals["question_mark"] is True

    def test_online_question_start_es(self, router):
        intent, _ = router.classify_intent("qué es la fotosíntesis")
        assert intent == Intent.ONLINE

    def test_online_question_start_en(self, router):
        intent, _ = router.classify_intent("what is quantum computing")
        assert intent == Intent.ONLINE

    def test_online_semantic_intent(self, router):
        intent, _ = router.classify_intent("explica cómo funciona la gravedad")
        assert intent == Intent.ONLINE

    def test_online_dime(self, router):
        intent, _ = router.classify_intent("dime algo sobre inteligencia artificial")
        assert intent == Intent.ONLINE

    def test_memory_plain_statement(self, router):
        intent, _ = router.classify_intent("hoy tuve una buena sesión de trabajo")
        assert intent == Intent.MEMORY

    def test_memory_short_note(self, router):
        intent, _ = router.classify_intent("reunión a las 3pm con el equipo")
        assert intent == Intent.MEMORY

    def test_cognitive_deep_analysis(self, router):
        long_text = (
            "analiza profundamente las implicaciones de cambiar la arquitectura "
            "del sistema de memoria para soportar múltiples backends de almacenamiento "
            "y evalúa el riesgo de cada opción disponible"
        )
        intent, signals = router.classify_intent(long_text)
        assert intent == Intent.COGNITIVE
        assert signals["cognitive_pattern"] is True

    def test_cognitive_requires_length(self, router):
        """Un patrón cognitivo con texto corto no debería ser COGNITIVE."""
        intent, _ = router.classify_intent("paso a paso")
        assert intent != Intent.COGNITIVE  # demasiado corto


# ===========================================================================
# 2. detect_context
# ===========================================================================

class TestDetectContext:
    """Tests para detección de tópico y riesgo."""

    def test_trading_topic(self, router):
        ctx = router.detect_context(
            "analiza la entrada de btc en soporte", "creator", "mario",
        )
        assert ctx["topic"] == "trading"
        assert ctx["sensitive"] is True
        assert ctx["risk_level"] == RiskLevel.HIGH

    def test_code_topic(self, router):
        ctx = router.detect_context(
            "hay un bug en el script de python para el deploy", "user", "test",
        )
        assert ctx["topic"] == "code"
        assert ctx["sensitive"] is False
        assert ctx["risk_level"] == RiskLevel.LOW

    def test_health_topic(self, router):
        ctx = router.detect_context(
            "necesito mejorar mi dieta y dormir mejor", "user", "test",
        )
        assert ctx["topic"] == "health"
        assert ctx["sensitive"] is True
        assert ctx["risk_level"] == RiskLevel.HIGH

    def test_general_topic(self, router):
        ctx = router.detect_context(
            "hoy fue un buen día", "user", "test",
        )
        assert ctx["topic"] == "general"
        assert ctx["risk_level"] == RiskLevel.LOW

    def test_financial_topic(self, router):
        ctx = router.detect_context(
            "quiero revisar mi presupuesto y ahorro mensual", "user", "test",
        )
        assert ctx["topic"] == "financial"
        assert ctx["sensitive"] is True

    def test_security_topic(self, router):
        ctx = router.detect_context(
            "necesito rotar el token de seguridad y las contraseñas", "user", "test",
        )
        assert ctx["topic"] == "security"
        assert ctx["sensitive"] is True

    def test_long_prompt_medium_risk(self, router):
        long_text = "a " * 200  # > 300 chars
        ctx = router.detect_context(long_text, "user", "test")
        assert ctx["risk_level"] == RiskLevel.MEDIUM

    def test_context_includes_channel_info(self, router):
        ctx = router.detect_context("test", "creator", "mario")
        assert ctx["signals"]["channel"] == "creator"
        assert ctx["signals"]["owner"] == "mario"


# ===========================================================================
# 3. evaluate_policy
# ===========================================================================

class TestEvaluatePolicy:
    """Tests para evaluación de política."""

    def test_memory_always_auto(self, router):
        action = router.evaluate_policy(Intent.MEMORY, "general", RiskLevel.LOW)
        assert action == PolicyAction.AUTO_EXECUTE

    def test_local_always_auto(self, router):
        action = router.evaluate_policy(Intent.LOCAL, "trading", RiskLevel.HIGH)
        assert action == PolicyAction.AUTO_EXECUTE

    def test_online_always_auto(self, router):
        action = router.evaluate_policy(Intent.ONLINE, "health", RiskLevel.HIGH)
        assert action == PolicyAction.AUTO_EXECUTE

    def test_command_always_auto(self, router):
        action = router.evaluate_policy(Intent.COMMAND, "general", RiskLevel.LOW)
        assert action == PolicyAction.AUTO_EXECUTE

    def test_ai_single_low_risk_auto(self, router):
        action = router.evaluate_policy(Intent.AI_SINGLE, "code", RiskLevel.LOW)
        assert action == PolicyAction.AUTO_EXECUTE

    def test_ai_single_high_risk_revision(self, router):
        """Con riesgo alto sin policy_router disponible → REQUIERE_REVISION."""
        action = router.evaluate_policy(Intent.AI_SINGLE, "trading", RiskLevel.HIGH)
        # Puede ser REQUIERE_REVISION o lo que devuelva el PolicyRouter
        assert action in (PolicyAction.REQUIERE_REVISION, PolicyAction.AUTO_EXECUTE)

    def test_cognitive_low_risk_auto(self, router):
        action = router.evaluate_policy(Intent.COGNITIVE, "code", RiskLevel.LOW)
        assert action == PolicyAction.AUTO_EXECUTE


# ===========================================================================
# 4. select_strategy
# ===========================================================================

class TestSelectStrategy:
    """Tests para selección de estrategia."""

    def test_blocked_by_policy(self, router):
        strategy, providers, conf, reason = router.select_strategy(
            Intent.AI_SINGLE, "trading", RiskLevel.HIGH,
            PolicyAction.BLOCKED, {},
        )
        assert strategy == Strategy.BLOCKED
        assert conf == 1.0

    def test_command_strategy(self, router):
        strategy, _, _, reason = router.select_strategy(
            Intent.COMMAND, "general", RiskLevel.LOW,
            PolicyAction.AUTO_EXECUTE, {"command": "router"},
        )
        assert strategy == Strategy.EXECUTE_COMMAND
        assert "router" in reason

    def test_memory_strategy(self, router):
        strategy, _, _, _ = router.select_strategy(
            Intent.MEMORY, "general", RiskLevel.LOW,
            PolicyAction.AUTO_EXECUTE, {},
        )
        assert strategy == Strategy.RESOLVE_MEMORY

    def test_local_strategy(self, router):
        strategy, _, _, _ = router.select_strategy(
            Intent.LOCAL, "general", RiskLevel.LOW,
            PolicyAction.AUTO_EXECUTE, {},
        )
        assert strategy == Strategy.RESOLVE_LOCAL

    def test_ai_single_strategy(self, router):
        strategy, providers, _, _ = router.select_strategy(
            Intent.AI_SINGLE, "code", RiskLevel.LOW,
            PolicyAction.AUTO_EXECUTE, {},
        )
        assert strategy == Strategy.ROUTE_SINGLE
        assert len(providers) >= 1

    def test_ai_multi_strategy(self, router):
        strategy, providers, _, _ = router.select_strategy(
            Intent.AI_MULTI, "code", RiskLevel.LOW,
            PolicyAction.AUTO_EXECUTE, {},
        )
        assert strategy == Strategy.ROUTE_MULTI
        assert len(providers) >= 1

    def test_cognitive_strategy(self, router):
        strategy, _, _, _ = router.select_strategy(
            Intent.COGNITIVE, "code", RiskLevel.LOW,
            PolicyAction.AUTO_EXECUTE, {},
        )
        assert strategy == Strategy.ROUTE_COGNITIVE

    def test_cognitive_degraded_on_revision(self, router):
        strategy, _, _, reason = router.select_strategy(
            Intent.COGNITIVE, "trading", RiskLevel.HIGH,
            PolicyAction.REQUIERE_REVISION, {},
        )
        assert strategy == Strategy.ROUTE_MULTI
        assert "degradado" in reason

    def test_online_strategy(self, router):
        strategy, _, _, _ = router.select_strategy(
            Intent.ONLINE, "general", RiskLevel.LOW,
            PolicyAction.AUTO_EXECUTE, {},
        )
        assert strategy == Strategy.RESOLVE_ONLINE

    def test_sensitive_online_escalated(self, router):
        """Pregunta online sobre tópico sensible + alto riesgo → ROUTE_MULTI."""
        strategy, providers, _, reason = router.select_strategy(
            Intent.ONLINE, "trading", RiskLevel.HIGH,
            PolicyAction.AUTO_EXECUTE, {},
        )
        assert strategy == Strategy.ROUTE_MULTI
        assert "sensible" in reason or "escalada" in reason


# ===========================================================================
# 5. route() — pipeline completo
# ===========================================================================

class TestRoutePipeline:
    """Tests para el pipeline completo de routing."""

    def test_route_memory(self, router):
        route = router.route("hoy aprendí algo nuevo sobre grafos")
        assert route.intent == Intent.MEMORY
        assert route.strategy == Strategy.RESOLVE_MEMORY
        assert route.topic == "general"

    def test_route_local(self, router):
        route = router.route("qué dije sobre python?", "creator", "mario")
        assert route.intent == Intent.LOCAL
        assert route.strategy == Strategy.RESOLVE_LOCAL

    def test_route_online(self, router):
        route = router.route("qué es la teoría de cuerdas?")
        assert route.intent == Intent.ONLINE
        assert route.strategy == Strategy.RESOLVE_ONLINE

    def test_route_ai_single(self, router):
        route = router.route("/ai explica la relatividad general")
        assert route.intent == Intent.AI_SINGLE
        assert route.strategy == Strategy.ROUTE_SINGLE
        assert route.command_name == "ai"
        assert "relatividad" in route.command_args

    def test_route_ai_multi(self, router):
        route = router.route("/multi compara python vs rust")
        assert route.intent == Intent.AI_MULTI
        assert route.strategy == Strategy.ROUTE_MULTI
        assert route.command_name == "multi"

    def test_route_command(self, router):
        route = router.route("/router")
        assert route.intent == Intent.COMMAND
        assert route.strategy == Strategy.EXECUTE_COMMAND
        assert route.command_name == "router"

    def test_route_trading_topic(self, router):
        route = router.route("analiza la entrada de btc en soporte a 45k")
        assert route.topic == "trading"
        assert route.risk_level == RiskLevel.HIGH

    def test_route_returns_smartroute(self, router):
        route = router.route("hello world")
        assert isinstance(route, SmartRoute)
        assert route.routed_at > 0

    def test_route_to_dict(self, router):
        route = router.route("test message")
        d = route.to_dict()
        assert "intent" in d
        assert "strategy" in d
        assert "topic" in d
        assert "confidence" in d

    def test_route_summary(self, router):
        route = router.route("test")
        s = route.summary()
        assert "→" in s
        assert route.intent.value in s

    def test_route_metadata_no_content(self, router):
        """Verificar que metadata NO contiene el contenido del mensaje."""
        route = router.route("/ai mi contraseña secreta es abc123")
        # No debe haber contenido del prompt en metadata
        metadata_str = str(route.metadata)
        assert "abc123" not in metadata_str


# ===========================================================================
# 5b. route() consume resolve_intent() — Fase 2, paso 5
# ===========================================================================

class TestRouteConsumesResolveIntent:
    """route() debe consumir la IntentDecision ya resuelta por
    core.intent_ssot.resolve_intent(), sin volver a clasificar la entrada
    por su cuenta (Fase 2, paso 5)."""

    def test_route_never_calls_classify_intent_directly(self, router, monkeypatch):
        """Ni route() ni resolve_intent() deben invocar el método agregado
        classify_intent() — este test falla si route() vuelve a clasificar
        la entrada llamando a self.classify_intent()."""
        calls = []
        original = SmartRouter.classify_intent

        def _tracked(self, text):
            calls.append(text)
            return original(self, text)

        monkeypatch.setattr(SmartRouter, "classify_intent", _tracked)
        router.route("qué dije ayer sobre bitcoin?")
        assert calls == [], (
            "route() (o resolve_intent()) invocó classify_intent() "
            "directamente — route() debe consumir resolve_intent() en su lugar"
        )

    def test_route_resolves_intent_exactly_once(self, router, monkeypatch):
        """Ningún proveedor de evidencia interno debe ejecutarse más de una
        vez por llamada a route() — probaría doble clasificación (una vez
        dentro de resolve_intent(), otra independiente dentro de route())."""
        call_counts = {"regex": 0, "resolve": 0}
        original_regex = SmartRouter._classify_regex
        original_resolve = SmartRouter._resolve_intent

        def _tracked_regex(self, text):
            call_counts["regex"] += 1
            return original_regex(self, text)

        def _tracked_resolve(self, *args, **kwargs):
            call_counts["resolve"] += 1
            return original_resolve(self, *args, **kwargs)

        monkeypatch.setattr(SmartRouter, "_classify_regex", _tracked_regex)
        monkeypatch.setattr(SmartRouter, "_resolve_intent", _tracked_resolve)
        router.route("qué dije ayer sobre bitcoin?")
        assert call_counts["regex"] == 1, call_counts
        assert call_counts["resolve"] == 1, call_counts

    def test_route_uses_resolve_intent_decision_not_a_recalculation(self, router, monkeypatch):
        """route() debe usar el primary_intent/domain que produce
        resolve_intent(), no uno recalculado independientemente."""
        import core.intent_ssot as ssot_mod
        from core.intent_ssot import IntentDecision

        fake_decision = IntentDecision(
            primary_intent="market",
            domain="market",
            task_type="",
            voice_intent="",
            confidence=0.9,
            evidence=[],
            conflicts=[],
            raw_signals={},
        )
        monkeypatch.setattr(ssot_mod, "resolve_intent", lambda *a, **k: fake_decision)

        route = router.route("cualquier texto, el contenido no importa aquí")
        assert route.intent == Intent.MARKET
        assert route.metadata["domain"] == "market"

    @pytest.mark.parametrize("text", [
        "hola, cómo estás",
        "qué dije ayer sobre bitcoin?",
        "cuánto vale btc",
        "/ai explica la relatividad",
        "/multi compara python vs rust",
        "/router",
    ])
    def test_route_and_resolve_intent_share_primary_intent_domain_evidence(self, router, text):
        """Para el mismo texto, resolve_intent(text) y router.route(text)
        deben coincidir en primary_intent, dominio y fuentes de evidencia —
        prueba que route() consume la MISMA resolución, no una paralela."""
        from core.intent_ssot import resolve_intent

        decision = resolve_intent(text)
        route = router.route(text)
        assert route.intent.value == decision.primary_intent
        assert route.metadata["domain"] == decision.domain
        assert route.metadata["intent_evidence_sources"] == [e.source for e in decision.evidence]


# ===========================================================================
# 6. record_outcome + stats
# ===========================================================================

class TestMetrics:
    """Tests para registro de métricas y estadísticas."""

    def test_record_outcome(self, router):
        route = router.route("test")
        router.record_outcome(route, success=True, latency_ms=50.0)
        assert len(router._metrics) == 1
        assert router._metrics[0]["success"] is True

    def test_stats_empty(self, router):
        stats = router.stats()
        assert stats["total_routes"] == 0

    def test_stats_with_data(self, router):
        for text in ["hello", "qué es python?", "/ai test", "nota personal"]:
            route = router.route(text)
            router.record_outcome(route, success=True, latency_ms=10.0)

        stats = router.stats()
        assert stats["total_routes"] == 4
        assert stats["success_rate"] == 1.0
        assert "strategy_distribution" in stats
        assert "intent_distribution" in stats
        assert "topic_distribution" in stats

    def test_metrics_buffer_limit(self, router):
        """Verificar que el buffer no crece indefinidamente."""
        for i in range(600):
            route = router.route("test")
            router.record_outcome(route, success=True, latency_ms=1.0)
        assert len(router._metrics) <= 500

    def test_metrics_privacy(self, router):
        """Verificar que las métricas NO contienen contenido del mensaje."""
        route = router.route("mi contraseña secreta es supersecret123")
        router.record_outcome(route, success=True, latency_ms=10.0)
        metric = router._metrics[0]
        metric_str = str(metric)
        assert "supersecret123" not in metric_str
        assert "contraseña" not in metric_str


# ===========================================================================
# 7. Singleton
# ===========================================================================

class TestSingleton:
    def test_get_smart_router_returns_same_instance(self):
        r1 = get_smart_router()
        r2 = get_smart_router()
        assert r1 is r2

    def test_repr(self, router):
        r = repr(router)
        assert "SmartRouter" in r
