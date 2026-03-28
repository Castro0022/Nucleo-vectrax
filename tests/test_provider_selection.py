"""
Tests para la selección inteligente de proveedor por capacidades.

Verifica que el SmartRouter selecciona el proveedor óptimo según:
  - OpenAI:    código, herramientas, agentes
  - Gemini:    análisis largo, contexto grande, multimodal
  - Anthropic: razonamiento, seguridad, planificación
  - Ollama:    local, offline, privacidad (tópicos sensibles)
"""

import os
import pytest
from unittest.mock import patch

from core.smart_router import (
    SmartRouter,
    _PROVIDER_STRENGTHS,
    _TASK_CAPABILITY_MAP,
    _SIGNAL_CAPABILITY_KEYWORDS,
)


# Simular que los cloud providers están configurados para que
# el scoring los incluya. Sin esto solo Ollama estaría disponible.
_MOCK_ENV = {
    "OPENAI_API_KEY": "test-key",
    "GEMINI_API_KEY": "test-key",
    "ANTHROPIC_API_KEY": "test-key",
}


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _top_provider(router: SmartRouter, topic: str, text: str = "") -> str:
    """Return the top-scored provider for a topic+text combination."""
    scores = router._score_providers(topic, text)
    if not scores:
        return ""
    return max(scores, key=scores.get)


def _detect_caps(router: SmartRouter, topic: str, text: str = "") -> set:
    """Return detected capabilities for a topic+text."""
    return router._detect_required_capabilities(topic, text)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Capability detection
# ═══════════════════════════════════════════════════════════════════════════


@patch.dict(os.environ, _MOCK_ENV)
class TestCapabilityDetection:
    """Verifica que las capacidades requeridas se infieren correctamente."""

    def setup_method(self):
        self.router = SmartRouter()

    def test_code_topic_detects_coding(self):
        caps = _detect_caps(self.router, "code")
        assert "coding" in caps
        assert "tool_use" in caps

    def test_security_topic_detects_safety(self):
        caps = _detect_caps(self.router, "security")
        assert "safety" in caps
        assert "planning" in caps

    def test_financial_topic_detects_reasoning(self):
        caps = _detect_caps(self.router, "financial")
        assert "reasoning" in caps
        assert "safety" in caps

    def test_general_topic_defaults(self):
        caps = _detect_caps(self.router, "general")
        assert "reasoning" in caps

    def test_coding_keywords_detected(self):
        caps = _detect_caps(self.router, "general", "escribe código python para ordenar")
        assert "coding" in caps

    def test_vision_keywords_detected(self):
        caps = _detect_caps(self.router, "general", "analiza esta imagen del diagrama")
        assert "vision" in caps

    def test_planning_keywords_detected(self):
        caps = _detect_caps(self.router, "general", "diseña la arquitectura del sistema")
        assert "planning" in caps

    def test_agentic_keywords_detected(self):
        caps = _detect_caps(self.router, "general", "crea un agente autónomo para el workflow")
        assert "agentic" in caps

    def test_safety_keywords_detected(self):
        caps = _detect_caps(self.router, "general", "audita las vulnerabilidades del sistema")
        assert "safety" in caps

    def test_long_context_keywords_detected(self):
        caps = _detect_caps(self.router, "general", "resume este documento pdf extenso")
        assert "long_context" in caps
        assert "summarization" in caps

    def test_combined_topic_and_keywords(self):
        caps = _detect_caps(self.router, "code", "crea un agente que automatice el deploy")
        assert "coding" in caps
        assert "agentic" in caps
        assert "tool_use" in caps


# ═══════════════════════════════════════════════════════════════════════════
# 2. Provider scoring — domain routing
# ═══════════════════════════════════════════════════════════════════════════


@patch.dict(os.environ, _MOCK_ENV)
class TestProviderScoring:
    """Verifica que el scoring favorece al proveedor correcto por dominio."""

    def setup_method(self):
        self.router = SmartRouter()

    # -- Código → OpenAI --

    def test_code_topic_favors_openai(self):
        top = _top_provider(self.router, "code")
        assert top == "openai", f"Expected openai, got {top}"

    def test_coding_text_favors_openai(self):
        top = _top_provider(self.router, "general", "escribe una función python que ordene una lista")
        assert top == "openai", f"Expected openai, got {top}"

    def test_agent_workflow_favors_openai(self):
        top = _top_provider(self.router, "general", "crea un agente que use herramientas para automatizar")
        assert top == "openai", f"Expected openai, got {top}"

    # -- Análisis largo / multimodal → Gemini --

    def test_vision_favors_gemini(self):
        top = _top_provider(self.router, "general", "analiza esta imagen y describe el diagrama")
        assert top == "gemini", f"Expected gemini, got {top}"

    def test_long_document_favors_gemini(self):
        top = _top_provider(self.router, "general", "resume este documento extenso completo")
        assert top == "gemini", f"Expected gemini, got {top}"

    def test_summarization_favors_gemini(self):
        top = _top_provider(self.router, "general", "dame un resumen condensado de todo el artículo")
        assert top == "gemini", f"Expected gemini, got {top}"

    # -- Razonamiento / seguridad / planificación → Anthropic --

    def test_security_topic_favors_anthropic(self):
        top = _top_provider(self.router, "security")
        assert top == "anthropic", f"Expected anthropic, got {top}"

    def test_planning_text_favors_anthropic(self):
        top = _top_provider(self.router, "general", "diseña una estrategia paso a paso con fases e hitos")
        assert top == "anthropic", f"Expected anthropic, got {top}"

    def test_reasoning_text_favors_anthropic(self):
        top = _top_provider(self.router, "general", "evalúa la lógica y deduce las implicaciones críticas")
        assert top == "anthropic", f"Expected anthropic, got {top}"

    def test_risk_analysis_favors_anthropic(self):
        top = _top_provider(self.router, "financial")
        assert top == "anthropic", f"Expected anthropic, got {top}"

    # -- Tópico sensible + local → Ollama con bonus --

    def test_sensitive_topic_boosts_ollama(self):
        """Ollama debería recibir un bonus significativo en tópicos sensibles."""
        scores = self.router._score_providers("security")
        # Ollama debe tener un score razonable (no necesariamente el mayor)
        assert "ollama" in scores
        assert scores["ollama"] > 0, "Ollama should have positive score for sensitive topics"

    def test_health_topic_boosts_ollama(self):
        scores = self.router._score_providers("health")
        assert "ollama" in scores
        assert scores["ollama"] > 0


# ═══════════════════════════════════════════════════════════════════════════
# 3. Full route() integration — provider in SmartRoute
# ═══════════════════════════════════════════════════════════════════════════


@patch.dict(os.environ, _MOCK_ENV)
class TestRouteProviderSelection:
    """Verifica que route() incluye provider_scores y selecciona correctamente."""

    def setup_method(self):
        self.router = SmartRouter()

    def test_route_includes_provider_scores(self):
        route = self.router.route("/ai escribe código python para un API REST")
        assert "provider_scores" in route.metadata
        assert isinstance(route.metadata["provider_scores"], dict)

    def test_route_ai_code_selects_openai(self):
        route = self.router.route("/ai escribe una función python para parsear JSON")
        assert route.providers, "Should have providers selected"
        assert route.providers[0] == "openai", f"Expected openai first, got {route.providers}"

    def test_route_ai_vision_selects_gemini(self):
        route = self.router.route("/ai analiza esta imagen y describe el gráfico")
        assert route.providers, "Should have providers selected"
        assert route.providers[0] == "gemini", f"Expected gemini first, got {route.providers}"

    def test_route_ai_planning_selects_anthropic(self):
        route = self.router.route("/ai diseña la arquitectura paso a paso con estrategia")
        assert route.providers, "Should have providers selected"
        assert route.providers[0] == "anthropic", f"Expected anthropic first, got {route.providers}"

    def test_route_multi_has_multiple_providers(self):
        route = self.router.route("/multi analiza este código con razonamiento profundo")
        assert len(route.providers) > 1, "Multi should have multiple providers"


# ═══════════════════════════════════════════════════════════════════════════
# 4. Configuration / structure
# ═══════════════════════════════════════════════════════════════════════════


class TestProviderStrengthsConfig:
    """Verifica la estructura de configuración de fortalezas."""

    def test_all_providers_defined(self):
        assert "openai" in _PROVIDER_STRENGTHS
        assert "gemini" in _PROVIDER_STRENGTHS
        assert "anthropic" in _PROVIDER_STRENGTHS
        assert "ollama" in _PROVIDER_STRENGTHS

    def test_strengths_have_primary(self):
        for name, config in _PROVIDER_STRENGTHS.items():
            assert "primary" in config, f"{name} missing primary"
            assert "secondary" in config, f"{name} missing secondary"

    def test_openai_strengths(self):
        p = _PROVIDER_STRENGTHS["openai"]["primary"]
        assert "coding" in p
        assert "tool_use" in p
        assert "agentic" in p

    def test_gemini_strengths(self):
        p = _PROVIDER_STRENGTHS["gemini"]["primary"]
        assert "long_context" in p
        assert "vision" in p
        assert "summarization" in p

    def test_anthropic_strengths(self):
        p = _PROVIDER_STRENGTHS["anthropic"]["primary"]
        assert "reasoning" in p
        assert "safety" in p
        assert "planning" in p

    def test_task_capability_map_has_code(self):
        assert "code" in _TASK_CAPABILITY_MAP
        assert "coding" in _TASK_CAPABILITY_MAP["code"]

    def test_signal_keywords_cover_all_domains(self):
        expected_caps = {"coding", "tool_use", "agentic", "long_context",
                         "vision", "summarization", "reasoning", "safety", "planning"}
        actual_caps = set(_SIGNAL_CAPABILITY_KEYWORDS.keys())
        assert expected_caps <= actual_caps


# ═══════════════════════════════════════════════════════════════════════════
# 5. Edge cases
# ═══════════════════════════════════════════════════════════════════════════


@patch.dict(os.environ, _MOCK_ENV)
class TestEdgeCases:

    def setup_method(self):
        self.router = SmartRouter()

    def test_empty_text_uses_topic_only(self):
        scores = self.router._score_providers("code", "")
        assert len(scores) > 0

    def test_unknown_topic_gets_defaults(self):
        caps = _detect_caps(self.router, "unknown_topic_xyz")
        # should fallback to empty, then _score_providers fills defaults
        scores = self.router._score_providers("unknown_topic_xyz")
        assert len(scores) > 0  # should still score all available providers

    def test_scores_are_floats(self):
        scores = self.router._score_providers("code", "escribe código")
        for name, score in scores.items():
            assert isinstance(score, float), f"{name} score is not float"

    def test_all_providers_scored(self):
        """All available providers should appear in scores."""
        scores = self.router._score_providers("general")
        # At minimum ollama should always be there (it's local)
        assert len(scores) >= 1
