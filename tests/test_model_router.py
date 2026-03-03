"""
Tests for Vectrax Intelligence Router (ModelRouter)
=====================================================
Unit tests, golden tests, and integration tests for:
  - Capability registry
  - Task classifier
  - Routing policy / scoring
  - Fallback selection
  - Governor/Risk integration
  - Audit logging
"""

import json
import os
import sys
from pathlib import Path

import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.state_manager as sm

_orig_state_path = sm.STATE_PATH
_orig_runtime_dir = sm.RUNTIME_DIR


@pytest.fixture(autouse=True)
def isolate_state(tmp_path):
    """Redirect state_manager to temp dir for every test."""
    sm.RUNTIME_DIR = str(tmp_path)
    sm.STATE_PATH = os.path.join(str(tmp_path), "cognition_state.json")
    sm.save(dict(sm.DEFAULT_STATE))
    # Reset global singletons
    import core.risk_engine as re_mod
    re_mod._global_engine = None
    import core.routing.model_router as mr_mod
    mr_mod._global_router = None
    yield
    sm.RUNTIME_DIR = _orig_runtime_dir
    sm.STATE_PATH = _orig_state_path


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Ensure cloud env vars are unset so only local models are available."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VX_DEBUG", raising=False)
    monkeypatch.delenv("VX_LLM_CLASSIFIER", raising=False)
    # Refresh registry after clearing env
    from core.routing.capabilities import refresh_availability
    refresh_availability()


# ===================================================================
# 1. Capability Registry Tests
# ===================================================================

class TestCapabilityRegistry:
    def test_registry_not_empty(self):
        from core.routing.capabilities import get_registry
        reg = get_registry()
        assert len(reg) > 0

    def test_ollama_models_available(self):
        from core.routing.capabilities import get_available_models
        available = get_available_models()
        ollama_keys = [k for k in available if k.startswith("ollama:")]
        assert len(ollama_keys) >= 1

    def test_cloud_models_unavailable_without_env(self):
        from core.routing.capabilities import get_available_models
        available = get_available_models()
        cloud_keys = [k for k in available if not available[k].is_local]
        assert len(cloud_keys) == 0

    def test_cloud_models_available_with_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        from core.routing.capabilities import refresh_availability, get_available_models
        refresh_availability()
        available = get_available_models()
        openai_keys = [k for k in available if k.startswith("openai:")]
        assert len(openai_keys) >= 1

    def test_local_only_filter(self):
        from core.routing.capabilities import get_available_models
        local = get_available_models(local_only=True)
        for profile in local.values():
            assert profile.is_local is True

    def test_models_with_capability(self):
        from core.routing.capabilities import models_with_capability, Capability
        coding_models = models_with_capability(Capability.CODING)
        assert len(coding_models) > 0
        for profile in coding_models.values():
            assert Capability.CODING in profile.capabilities

    def test_model_profile_key(self):
        from core.routing.capabilities import get_registry
        reg = get_registry()
        for key, profile in reg.items():
            assert key == f"{profile.provider}:{profile.model}"

    def test_vision_capability_has_image_modality(self):
        from core.routing.capabilities import models_with_capability, Capability
        vision_models = models_with_capability(Capability.VISION)
        for profile in vision_models.values():
            assert "image" in profile.modalities


# ===================================================================
# 2. Task Classifier Tests
# ===================================================================

class TestTaskClassifier:
    def test_code_detection(self):
        from core.routing.task_classifier import classify_task, TaskType
        result = classify_task("Write a Python function to sort a list")
        assert result.task_type == TaskType.CODE

    def test_reasoning_detection(self):
        from core.routing.task_classifier import classify_task, TaskType
        result = classify_task("Solve this equation: 2x + 5 = 15")
        assert result.task_type == TaskType.REASONING

    def test_summarization_detection(self):
        from core.routing.task_classifier import classify_task, TaskType
        result = classify_task("Summarize the key points of this document")
        assert result.task_type == TaskType.SUMMARIZATION

    def test_translation_detection(self):
        from core.routing.task_classifier import classify_task, TaskType
        result = classify_task("Translate this text to Spanish")
        assert result.task_type == TaskType.TRANSLATION

    def test_creative_detection(self):
        from core.routing.task_classifier import classify_task, TaskType
        result = classify_task("Write a story about a robot")
        assert result.task_type == TaskType.CREATIVE

    def test_vision_detection(self):
        from core.routing.task_classifier import classify_task, TaskType
        result = classify_task("What is in this image? Describe the diagram")
        assert result.task_type == TaskType.VISION

    def test_structured_extraction_detection(self):
        from core.routing.task_classifier import classify_task, TaskType
        result = classify_task("Extract all fields from this JSON schema")
        assert result.task_type == TaskType.STRUCTURED_EXTRACTION

    def test_tool_use_detection(self):
        from core.routing.task_classifier import classify_task, TaskType
        result = classify_task("Use tool to search the web for latest news")
        assert result.task_type == TaskType.TOOL_USE

    def test_default_chat(self):
        from core.routing.task_classifier import classify_task, TaskType
        result = classify_task("Hello, how are you today?")
        assert result.task_type == TaskType.CHAT

    def test_long_context_detection(self):
        from core.routing.task_classifier import classify_task, TaskType
        long_text = "This is a very long prompt " * 500  # > 6000 chars
        result = classify_task(long_text)
        assert result.task_type == TaskType.LONG_CONTEXT

    def test_confidence_range(self):
        from core.routing.task_classifier import classify_task
        prompts = [
            "Write Python code to sort a list",
            "Hello",
            "Summarize this article for me please",
        ]
        for p in prompts:
            result = classify_task(p)
            assert 0.0 <= result.confidence <= 1.0

    def test_features_populated(self):
        from core.routing.task_classifier import classify_task
        result = classify_task("Debug this Python error")
        assert "prompt_length" in result.features
        assert result.method == "rules"

    def test_spanish_code_keywords(self):
        from core.routing.task_classifier import classify_task, TaskType
        result = classify_task("Programar una función para compilar el código")
        assert result.task_type == TaskType.CODE

    def test_json_structural_heuristic(self):
        from core.routing.task_classifier import classify_task, TaskType
        result = classify_task("Here is some data: {name: test} and {value: 42}")
        assert result.task_type == TaskType.STRUCTURED_EXTRACTION


# ===================================================================
# 3. Model Router Tests
# ===================================================================

class TestModelRouter:
    def test_route_returns_decision(self):
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        decision = router.route("Write a Python sort function")
        assert decision.primary is not None
        assert decision.primary.provider == "ollama"
        assert decision.score > 0

    def test_route_selects_coding_model_for_code(self):
        from core.routing.model_router import ModelRouter
        from core.routing.capabilities import Capability
        router = ModelRouter()
        decision = router.route("Implement a binary search in Python")
        assert Capability.CODING in decision.primary.capabilities

    def test_route_provides_fallback(self):
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        # Use high-confidence prompt so adaptive autonomy stays AUTO (allows fallback)
        decision = router.route("Solve this equation step by step and calculate the result")
        # With multiple ollama models, there should be a fallback
        assert decision.fallback is not None
        assert decision.fallback.key != decision.primary.key

    def test_route_task_classification_attached(self):
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        decision = router.route("Translate this to French")
        assert decision.task_classification is not None
        assert decision.task_classification.task_type.value == "translation"

    def test_route_local_only_in_recover_mode(self):
        sm.put("governor_mode", "recover")
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        decision = router.route("Do something")
        assert decision.primary.is_local
        assert decision.governor_mode == "recover"

    def test_route_risk_check_passes_normally(self):
        sm.put("governor_mode", "act")
        sm.put("cycles", 10)
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        decision = router.route("Hello world")
        assert decision.risk_check_passed is True

    def test_route_to_dict_serialisable(self):
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        decision = router.route("Test prompt")
        d = decision.to_dict()
        # Must be JSON-serialisable
        json.dumps(d)
        assert "primary_provider" in d
        assert "task_type" in d
        assert "score" in d

    def test_route_with_context(self):
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        decision = router.route(
            "What does this do?",
            context="def hello():\n    print('world')"
        )
        assert decision.primary is not None

    def test_circuit_breaker_skips_open_provider(self):
        from core.routing.model_router import ModelRouter
        from core.routing.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1))
        # Trip the circuit for ollama
        cb.record_failure("ollama")
        router = ModelRouter(circuit_breaker=cb)
        # Should still find a model (fallback logic broadens search)
        decision = router.route("Hello")
        assert decision.primary is not None


# ===================================================================
# 4. Scoring Tests
# ===================================================================

class TestScoring:
    def test_local_free_models_score_higher_on_cost(self):
        from core.routing.model_router import ModelRouter
        from core.routing.capabilities import Capability, ModelProfile

        router = ModelRouter()
        candidates = {
            "local:test": ModelProfile(
                provider="local", model="test",
                capabilities={Capability.CODING},
                cost_per_1k_tokens=0.0,
                avg_latency_ms=1000,
                max_context_tokens=8192,
                priority=10,
                is_local=True,
            ),
            "cloud:test": ModelProfile(
                provider="cloud", model="test",
                capabilities={Capability.CODING},
                cost_per_1k_tokens=0.005,
                avg_latency_ms=1000,
                max_context_tokens=128000,
                priority=30,
            ),
        }

        from core.routing.task_classifier import TaskClassification, TaskType
        cls = TaskClassification(task_type=TaskType.CODE, confidence=0.9)

        scored = router._score_candidates(candidates, Capability.CODING, cls)
        # Local should rank first (free + lower priority number)
        assert scored[0][0].provider == "local"

    def test_scores_always_between_0_and_1(self):
        from core.routing.model_router import ModelRouter
        from core.routing.capabilities import get_available_models, Capability
        from core.routing.task_classifier import TaskClassification, TaskType

        router = ModelRouter()
        candidates = get_available_models()
        cls = TaskClassification(task_type=TaskType.CHAT, confidence=0.5)

        scored = router._score_candidates(candidates, Capability.REASONING, cls)
        for profile, score in scored:
            assert 0.0 <= score <= 1.0, f"Score out of range: {score} for {profile.key}"


# ===================================================================
# 5. Golden Tests — fixed prompt → expected task_type → expected provider
# ===================================================================

class TestGoldenRouting:
    """
    Golden tests: verify that specific prompts consistently route to
    expected task types and providers.
    """

    GOLDEN_CASES = [
        # (prompt, expected_task_type, expected_provider)
        ("Write a Python function to merge two sorted lists", "code", "ollama"),
        ("Summarize the main points of this article", "summarization", "ollama"),
        ("Traduce este texto al inglés", "translation", "ollama"),
        ("Solve: if 3x + 7 = 22, what is x?", "reasoning", "ollama"),
        ("Write a story about a dragon and a knight", "creative", "ollama"),
        ("Describe this image in detail", "vision", "ollama"),
        ("Extract all fields from this JSON", "structured_extraction", "ollama"),
        ("Hi there, what's up?", "chat", "ollama"),
    ]

    @pytest.mark.parametrize("prompt,expected_type,expected_provider", GOLDEN_CASES)
    def test_golden_routing(self, prompt, expected_type, expected_provider):
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        decision = router.route(prompt)
        assert decision.task_classification.task_type.value == expected_type, (
            f"Expected {expected_type}, got {decision.task_classification.task_type.value} "
            f"for prompt: {prompt!r}"
        )
        assert decision.primary.provider == expected_provider


# ===================================================================
# 6. Governor/Risk Integration Tests
# ===================================================================

class TestGovernorIntegration:
    def test_risk_check_in_act_mode(self):
        sm.put("governor_mode", "act")
        sm.put("cycles", 10)
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        decision = router.route("Hello")
        assert decision.risk_check_passed is True

    def test_recover_mode_restricts_to_local(self):
        sm.put("governor_mode", "recover")
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        decision = router.route("Debug this code")
        assert decision.primary.is_local
        assert "recover" in decision.reason.lower() or decision.governor_mode == "recover"

    def test_observe_mode_still_routes(self):
        sm.put("governor_mode", "observe")
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        decision = router.route("What is 2+2?")
        assert decision.primary is not None


# ===================================================================
# 7. Audit Logging Integration Tests
# ===================================================================

class TestAuditLogging:
    def test_routing_creates_audit_entry(self, tmp_path):
        """Routing should write an entry to the audit ledger."""
        from core.routing.model_router import ModelRouter

        router = ModelRouter()
        router.route("Test audit logging")

        from core.audit_ledger import query
        entries = query(limit=5, action_filter="model_route")
        assert len(entries) >= 1
        latest = entries[0]
        assert latest["action"] == "model_route"
        assert latest["actor"] == "model_router"

        # Metadata should contain routing details
        meta = json.loads(latest["metadata"]) if isinstance(latest["metadata"], str) else latest["metadata"]
        assert "primary_provider" in meta
        assert "task_type" in meta
        assert "score" in meta

    def test_debug_mode_env(self, monkeypatch):
        """VX_DEBUG=1 should not break routing."""
        monkeypatch.setenv("VX_DEBUG", "1")
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        decision = router.route("Debug test")
        assert decision.primary is not None


# ===================================================================
# 8. Edge Cases
# ===================================================================

class TestEdgeCases:
    def test_empty_prompt(self):
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        decision = router.route("")
        assert decision.primary is not None

    def test_very_long_prompt(self):
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        long_prompt = "x " * 10000
        decision = router.route(long_prompt)
        assert decision.primary is not None

    def test_special_characters(self):
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        decision = router.route("¿Cómo está? 你好 🎉 <script>alert(1)</script>")
        assert decision.primary is not None

    def test_get_status(self):
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        status = router.get_status()
        assert "total_models" in status
        assert "local_models" in status
        assert "models" in status
        assert status["total_models"] >= 1

    def test_singleton_pattern(self):
        from core.routing.model_router import get_model_router
        r1 = get_model_router()
        r2 = get_model_router()
        assert r1 is r2

    def test_convenience_route_function(self):
        from core.routing.model_router import route
        decision = route("Quick test")
        assert decision.primary is not None
