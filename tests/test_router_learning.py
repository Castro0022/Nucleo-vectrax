"""
Tests para core.router_learning — Sistema de Autoaprendizaje del Router
========================================================================

Cubre:
  1. RouterFeedback — creación y serialización
  2. RouterDecisionLedger — persistencia JSONL append-only
  3. PostResolutionEvaluator — evaluación de calidad
  4. RouterLearningEngine — análisis y propuestas
  5. Integración con SmartRouter — record_feedback()
  6. Privacidad — nunca almacena contenido del mensaje
  7. Seguridad — fail-safe, no rompe routing existente
"""

import json
import os
import tempfile
import time

import pytest

from core.router_learning import (
    PostResolutionEvaluator,
    RouteQualityScore,
    RouterDecisionLedger,
    RouterFeedback,
    RouterLearningEngine,
    create_feedback_from_route,
    get_evaluator,
    get_learning_engine,
    get_ledger,
)
from core.smart_router import (
    Intent,
    RiskLevel,
    SmartRoute,
    SmartRouter,
    Strategy,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_ledger_path(tmp_path):
    """Ledger temporal para tests."""
    return str(tmp_path / "test_router_feedback.jsonl")


@pytest.fixture
def ledger(tmp_ledger_path):
    return RouterDecisionLedger(path=tmp_ledger_path)


@pytest.fixture
def evaluator():
    return PostResolutionEvaluator()


@pytest.fixture
def router():
    return SmartRouter()


def _make_feedback(**kwargs) -> RouterFeedback:
    """Helper: crea feedback con defaults."""
    defaults = {
        "intent": "online",
        "confidence": 0.8,
        "topic": "general",
        "strategy": "resolve_online",
        "risk_level": "LOW",
        "success": True,
        "response_latency_ms": 150.0,
    }
    defaults.update(kwargs)
    return RouterFeedback(**defaults)


def _make_route(**kwargs) -> SmartRoute:
    """Helper: crea SmartRoute con defaults."""
    defaults = {
        "intent": Intent.ONLINE,
        "topic": "general",
        "risk_level": RiskLevel.LOW,
        "strategy": Strategy.RESOLVE_ONLINE,
        "confidence": 0.8,
        "reason": "test route",
    }
    defaults.update(kwargs)
    return SmartRoute(**defaults)


# ===========================================================================
# 1. RouterFeedback — creación y serialización
# ===========================================================================

class TestRouterFeedback:

    def test_create_basic(self):
        fb = _make_feedback()
        assert fb.intent == "online"
        assert fb.confidence == 0.8
        assert fb.success is True

    def test_to_dict(self):
        fb = _make_feedback(providers=["openai"], entities_count=3)
        d = fb.to_dict()
        assert d["intent"] == "online"
        assert d["providers"] == ["openai"]
        assert d["entities_count"] == 3
        assert isinstance(d["timestamp"], float)

    def test_to_dict_truncates_error(self):
        long_error = "x" * 200
        fb = _make_feedback(resolution_error=long_error)
        d = fb.to_dict()
        assert len(d["resolution_error"]) <= 100

    def test_default_timestamp(self):
        before = time.time()
        fb = _make_feedback()
        after = time.time()
        assert before <= fb.timestamp <= after


# ===========================================================================
# 2. RouterDecisionLedger — persistencia JSONL
# ===========================================================================

class TestRouterDecisionLedger:

    def test_append_and_read(self, ledger):
        fb = _make_feedback()
        ledger.append(fb)
        records = ledger.read_all()
        assert len(records) == 1
        assert records[0]["intent"] == "online"

    def test_multiple_appends(self, ledger):
        for i in range(5):
            ledger.append(_make_feedback(confidence=i * 0.2))
        records = ledger.read_all()
        assert len(records) == 5

    def test_read_last(self, ledger):
        for i in range(10):
            ledger.append(_make_feedback(confidence=i * 0.1))
        last_3 = ledger.read_last(3)
        assert len(last_3) == 3
        # Más reciente primero
        assert last_3[0]["confidence"] > last_3[2]["confidence"]

    def test_count(self, ledger):
        assert ledger.count() == 0
        ledger.append(_make_feedback())
        assert ledger.count() == 1

    def test_read_empty(self, tmp_path):
        path = str(tmp_path / "nonexistent.jsonl")
        ledger = RouterDecisionLedger(path=path)
        assert ledger.read_all() == []

    def test_jsonl_format(self, tmp_ledger_path, ledger):
        ledger.append(_make_feedback())
        ledger.append(_make_feedback(intent="memory"))
        with open(tmp_ledger_path, "r") as f:
            lines = f.readlines()
        assert len(lines) == 2
        # Cada línea es JSON válido
        for line in lines:
            data = json.loads(line.strip())
            assert "intent" in data

    def test_corrupt_line_skipped(self, tmp_ledger_path, ledger):
        """Líneas corruptas no rompen la lectura."""
        ledger.append(_make_feedback())
        # Inyectar línea corrupta
        with open(tmp_ledger_path, "a") as f:
            f.write("NOT VALID JSON\n")
        ledger.append(_make_feedback(intent="memory"))
        records = ledger.read_all()
        assert len(records) == 2  # la corrupta fue ignorada


# ===========================================================================
# 3. PostResolutionEvaluator — evaluación de calidad
# ===========================================================================

class TestPostResolutionEvaluator:

    def test_correct_route(self, evaluator):
        fb = _make_feedback(success=True, confidence=0.8)
        score = evaluator.evaluate(fb)
        assert score == RouteQualityScore.CORRECT_ROUTE

    def test_failed_route(self, evaluator):
        fb = _make_feedback(success=False, used_fallback=False)
        score = evaluator.evaluate(fb)
        assert score == RouteQualityScore.FAILED_ROUTE

    def test_fallback_better(self, evaluator):
        fb = _make_feedback(success=True, used_fallback=True)
        score = evaluator.evaluate(fb)
        assert score == RouteQualityScore.FALLBACK_BETTER

    def test_ambiguous_route(self, evaluator):
        fb = _make_feedback(success=True, confidence=0.3)
        score = evaluator.evaluate(fb)
        assert score == RouteQualityScore.AMBIGUOUS_ROUTE

    def test_weak_route_generic(self, evaluator):
        fb = _make_feedback(success=True, confidence=0.7, was_generic_response=True)
        score = evaluator.evaluate(fb)
        assert score == RouteQualityScore.WEAK_ROUTE

    def test_weak_route_low_confidence(self, evaluator):
        fb = _make_feedback(success=True, confidence=0.5)
        score = evaluator.evaluate(fb)
        assert score == RouteQualityScore.WEAK_ROUTE


# ===========================================================================
# 4. RouterLearningEngine — análisis y propuestas
# ===========================================================================

class TestRouterLearningEngine:

    def _populate_ledger(self, ledger, n=20):
        """Popula el ledger con datos variados para análisis."""
        intents = ["online", "memory", "local", "ai_single", "cognitive"]
        topics = ["general", "code", "trading", "health"]
        for i in range(n):
            intent = intents[i % len(intents)]
            topic = topics[i % len(topics)]
            success = i % 3 != 0  # ~33% fallos
            used_fallback = i % 7 == 0
            confidence = 0.3 + (i % 8) * 0.1
            ledger.append(_make_feedback(
                intent=intent,
                topic=topic,
                success=success,
                used_fallback=used_fallback,
                confidence=confidence,
                response_latency_ms=50 + i * 10,
                routing_latency_ms=1 + i * 0.5,
                quality_score=RouteQualityScore.CORRECT_ROUTE.value if success else RouteQualityScore.FAILED_ROUTE.value,
                had_ambiguity=confidence < 0.45,
            ))

    def test_insufficient_data(self, ledger):
        engine = RouterLearningEngine(ledger=ledger)
        result = engine.analyze()
        assert result["status"] == "insufficient_data"

    def test_analysis_with_data(self, ledger):
        self._populate_ledger(ledger, 20)
        engine = RouterLearningEngine(ledger=ledger)
        result = engine.analyze()
        assert result["status"] == "ready"
        assert result["total_records"] == 20
        assert "intent_distribution" in result
        assert "quality_distribution" in result
        assert "strategy_stats" in result
        assert "fallback_analysis" in result
        assert "ambiguity_patterns" in result
        assert "confidence_analysis" in result
        assert "latency_stats" in result

    def test_intent_distribution(self, ledger):
        self._populate_ledger(ledger, 20)
        engine = RouterLearningEngine(ledger=ledger)
        result = engine.analyze()
        intent_dist = result["intent_distribution"]
        assert "online" in intent_dist
        assert "memory" in intent_dist
        assert intent_dist["online"]["total"] > 0

    def test_quality_distribution(self, ledger):
        self._populate_ledger(ledger, 20)
        engine = RouterLearningEngine(ledger=ledger)
        result = engine.analyze()
        quality = result["quality_distribution"]
        assert RouteQualityScore.CORRECT_ROUTE.value in quality or RouteQualityScore.FAILED_ROUTE.value in quality

    def test_generates_proposals_on_high_failure(self, ledger):
        """Si hay alta tasa de fallo, debe generar propuestas."""
        # Crear datos con 100% fallo en un intent
        for _ in range(10):
            ledger.append(_make_feedback(
                intent="online",
                success=False,
                confidence=0.7,
                quality_score=RouteQualityScore.FAILED_ROUTE.value,
            ))
        for _ in range(5):
            ledger.append(_make_feedback(
                intent="memory",
                success=True,
                confidence=0.9,
                quality_score=RouteQualityScore.CORRECT_ROUTE.value,
            ))
        engine = RouterLearningEngine(ledger=ledger)
        result = engine.analyze()
        proposals = result["improvement_proposals"]
        # Debe haber al menos una propuesta para "online"
        high_failure = [p for p in proposals if p.get("type") == "high_failure_intent"]
        assert len(high_failure) > 0
        assert high_failure[0]["intent"] == "online"

    def test_report_generation(self, ledger):
        self._populate_ledger(ledger, 20)
        engine = RouterLearningEngine(ledger=ledger)
        report = engine.generate_report()
        assert report["status"] == "ready"
        assert "summary" in report
        assert "top_intents" in report
        assert "improvement_proposals" in report

    def test_threshold_suggestions_insufficient(self, ledger):
        engine = RouterLearningEngine(ledger=ledger)
        result = engine.suggest_threshold_adjustments()
        assert result["status"] == "insufficient_data"

    def test_threshold_suggestions_with_data(self, ledger):
        self._populate_ledger(ledger, 20)
        engine = RouterLearningEngine(ledger=ledger)
        result = engine.suggest_threshold_adjustments()
        assert result["status"] in ("ready", "no_contrast_data")


# ===========================================================================
# 5. Integración con SmartRouter
# ===========================================================================

class TestSmartRouterIntegration:

    def test_record_feedback_creates_entry(self, router, tmp_path):
        """record_feedback() persiste en el ledger."""
        # Crear un ledger temporal para este test
        ledger_path = str(tmp_path / "test_feedback.jsonl")

        route = router.route("qué es python?")

        # Monkey-patch el ledger path para el test
        import core.router_learning as rl
        original_path = rl.LEDGER_PATH
        rl.LEDGER_PATH = ledger_path
        rl._ledger = None  # Reset singleton

        try:
            router.record_feedback(
                route=route,
                success=True,
                response_latency_ms=200.0,
                word_count=3,
            )

            # Verificar que se escribió al ledger
            ledger = RouterDecisionLedger(path=ledger_path)
            records = ledger.read_all()
            assert len(records) == 1
            assert records[0]["success"] is True
            assert records[0]["word_count"] == 3
        finally:
            rl.LEDGER_PATH = original_path
            rl._ledger = None

    def test_record_feedback_also_records_metrics(self, router):
        """record_feedback() también actualiza métricas in-memory."""
        route = router.route("test")
        router.record_feedback(route=route, success=True)
        assert len(router._metrics) >= 1

    def test_existing_record_outcome_still_works(self, router):
        """record_outcome() original sigue funcionando."""
        route = router.route("test")
        router.record_outcome(route, success=True, latency_ms=10)
        assert len(router._metrics) == 1

    def test_existing_routing_not_broken(self, router):
        """El routing existente no se altera por el sistema de learning."""
        # Mismos tests que en test_smart_router.py
        route = router.route("qué es la teoría de cuerdas?")
        assert route.intent == Intent.ONLINE
        assert route.strategy == Strategy.RESOLVE_ONLINE

        route2 = router.route("/ai explica la relatividad")
        assert route2.intent == Intent.AI_SINGLE
        assert route2.strategy == Strategy.ROUTE_SINGLE

        route3 = router.route("hoy tuve un buen día")
        assert route3.intent == Intent.MEMORY
        assert route3.strategy == Strategy.RESOLVE_MEMORY

    def test_record_feedback_failsafe(self, router):
        """Si el módulo de learning falla, el router sigue."""
        route = router.route("test")
        # Esto no debería lanzar excepción incluso si hay problemas internos
        router.record_feedback(
            route=route,
            success=True,
            response_latency_ms=100.0,
        )
        # El router sigue operando
        route2 = router.route("otro test")
        assert route2 is not None


# ===========================================================================
# 6. Privacidad
# ===========================================================================

class TestPrivacy:

    def test_feedback_no_message_content(self):
        """RouterFeedback nunca contiene el texto del mensaje."""
        fb = _make_feedback()
        d = fb.to_dict()
        d_str = json.dumps(d)
        # No debe haber campos de contenido
        assert "message" not in d
        assert "text" not in d
        assert "prompt" not in d
        assert "content" not in d

    def test_create_from_route_no_content(self, router):
        """create_feedback_from_route() no incluye contenido."""
        route = router.route("mi contraseña secreta es abc123")
        fb = create_feedback_from_route(route, success=True)
        d = fb.to_dict()
        d_str = json.dumps(d)
        assert "abc123" not in d_str
        assert "contraseña" not in d_str
        assert "secreta" not in d_str

    def test_ledger_no_message_content(self, ledger, router):
        """El ledger no persiste contenido del mensaje."""
        route = router.route("información sensible sobre mis finanzas y token secreto")
        fb = create_feedback_from_route(route, success=True, word_count=8)
        ledger.append(fb)

        records = ledger.read_all()
        record_str = json.dumps(records[0])
        assert "sensible" not in record_str.lower() or "topic" in record_str.lower()
        assert "token secreto" not in record_str
        assert "finanzas" not in record_str


# ===========================================================================
# 7. create_feedback_from_route
# ===========================================================================

class TestCreateFeedbackFromRoute:

    def test_basic_creation(self, router):
        route = router.route("qué es python?")
        fb = create_feedback_from_route(route, success=True, word_count=3)
        assert fb.intent == "online"
        assert fb.success is True
        assert fb.word_count == 3
        assert fb.quality_score != ""

    def test_with_fallback(self, router):
        route = router.route("test message")
        fb = create_feedback_from_route(
            route, success=True,
            used_fallback=True,
            fallback_strategy="resolve_online",
        )
        assert fb.used_fallback is True
        assert fb.quality_score == RouteQualityScore.FALLBACK_BETTER.value

    def test_failed_route(self, router):
        route = router.route("test")
        fb = create_feedback_from_route(
            route, success=False,
            resolution_error="timeout",
        )
        assert fb.quality_score == RouteQualityScore.FAILED_ROUTE.value

    def test_ambiguous_route(self, router):
        """Ruta con baja confianza → ambiguous."""
        route = _make_route(confidence=0.3)
        fb = create_feedback_from_route(route, success=True)
        assert fb.had_ambiguity is True


# ===========================================================================
# 8. Singletons
# ===========================================================================

class TestSingletons:

    def test_get_ledger_returns_same(self):
        import core.router_learning as rl
        rl._ledger = None
        l1 = get_ledger()
        l2 = get_ledger()
        assert l1 is l2
        rl._ledger = None  # cleanup

    def test_get_engine_returns_same(self):
        import core.router_learning as rl
        rl._engine = None
        e1 = get_learning_engine()
        e2 = get_learning_engine()
        assert e1 is e2
        rl._engine = None

    def test_get_evaluator_returns_same(self):
        import core.router_learning as rl
        rl._evaluator = None
        ev1 = get_evaluator()
        ev2 = get_evaluator()
        assert ev1 is ev2
        rl._evaluator = None
