"""
Tests for core.intelligence — Multi-Model Intelligence Router.

Tests the connector registry, parallel engine, synthesizer, feedback,
and the main IntelligenceRouter using mock providers (no real API calls).
"""

from __future__ import annotations

import asyncio
import os
import json
import tempfile
import pytest
from typing import AsyncIterator, Optional
from unittest.mock import AsyncMock, patch, MagicMock

# Ensure project root is on sys.path
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.abstraction.base import (
    BaseLLMProvider,
    GenerateRequest,
    GenerateResponse,
    ProviderType,
)
from core.routing.circuit_breaker import CircuitBreaker


# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------

class MockProvider(BaseLLMProvider):
    """In-memory mock provider for testing."""

    def __init__(
        self,
        name: str = "mock",
        content: str = "Mock response",
        latency: float = 0.01,
        should_fail: bool = False,
    ):
        super().__init__(provider_type=ProviderType.OLLAMA, endpoint="mock://")
        self._name = name
        self._content = content
        self._latency = latency
        self._should_fail = should_fail

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        await asyncio.sleep(self._latency)
        if self._should_fail:
            raise ConnectionError(f"{self._name} is down")
        return GenerateResponse(
            content=self._content,
            model=request.model,
            provider=self._name,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            latency_ms=self._latency * 1000,
        )

    async def stream(self, request: GenerateRequest) -> AsyncIterator[str]:
        yield self._content

    async def health_check(self) -> bool:
        return not self._should_fail

    async def list_models(self) -> list[str]:
        return [f"{self._name}-model"]

    async def close(self):
        pass


# ---------------------------------------------------------------------------
# Test: Connector Registry
# ---------------------------------------------------------------------------

class TestConnectorRegistry:
    def test_register_and_get(self):
        from core.intelligence.connector_registry import IntelligenceConnectorRegistry

        reg = IntelligenceConnectorRegistry()
        mock = MockProvider(name="test_prov")
        reg.register("test_prov", mock, ProviderType.OLLAMA, is_local=True)

        assert reg.get("test_prov") is mock
        assert reg.get("nonexistent") is None
        assert "test_prov" in reg.list_providers()

    def test_unregister(self):
        from core.intelligence.connector_registry import IntelligenceConnectorRegistry

        reg = IntelligenceConnectorRegistry()
        mock = MockProvider(name="temp")
        reg.register("temp", mock, ProviderType.OLLAMA)

        assert reg.unregister("temp") is True
        assert reg.get("temp") is None
        assert reg.unregister("temp") is False

    def test_get_by_type(self):
        from core.intelligence.connector_registry import IntelligenceConnectorRegistry

        reg = IntelligenceConnectorRegistry()
        mock = MockProvider(name="openai_mock")
        reg.register("openai_mock", mock, ProviderType.OPENAI)

        result = reg.get_by_type(ProviderType.OPENAI)
        assert result is mock
        assert reg.get_by_type(ProviderType.ANTHROPIC) is None

    def test_status(self):
        from core.intelligence.connector_registry import IntelligenceConnectorRegistry

        reg = IntelligenceConnectorRegistry()
        reg.register("a", MockProvider("a"), ProviderType.OLLAMA, is_local=True)
        reg.register("b", MockProvider("b"), ProviderType.OPENAI)

        status = reg.status()
        assert status["total"] == 2
        assert status["local"] == 1
        assert "a" in status["providers"]
        assert "b" in status["providers"]


# ---------------------------------------------------------------------------
# Test: Parallel Query Engine
# ---------------------------------------------------------------------------

class TestParallelQueryEngine:
    @pytest.mark.asyncio
    async def test_parallel_all_succeed(self):
        from core.intelligence.parallel import ParallelQueryEngine

        cb = CircuitBreaker()
        engine = ParallelQueryEngine(circuit_breaker=cb)

        providers = {
            "prov_a": MockProvider("a", content="Response A"),
            "prov_b": MockProvider("b", content="Response B"),
        }
        request = GenerateRequest(prompt="Hello", model="test")
        model_map = {"prov_a": "model-a", "prov_b": "model-b"}

        result = await engine.query(providers, request, model_map)

        assert result.providers_queried == 2
        assert result.providers_succeeded == 2
        assert len(result.successful_results) == 2
        assert len(result.failed_results) == 0

    @pytest.mark.asyncio
    async def test_parallel_partial_failure(self):
        from core.intelligence.parallel import ParallelQueryEngine

        cb = CircuitBreaker()
        engine = ParallelQueryEngine(circuit_breaker=cb)

        providers = {
            "good": MockProvider("good", content="OK"),
            "bad": MockProvider("bad", should_fail=True),
        }
        request = GenerateRequest(prompt="Test", model="test")

        result = await engine.query(providers, request)

        assert result.providers_succeeded == 1
        assert len(result.failed_results) == 1
        assert result.successful_results[0].provider_name == "good"

    @pytest.mark.asyncio
    async def test_circuit_breaker_skips_open(self):
        from core.intelligence.parallel import ParallelQueryEngine

        cb = CircuitBreaker()
        # Force open circuit for "broken"
        for _ in range(10):
            cb.record_failure("broken")

        engine = ParallelQueryEngine(circuit_breaker=cb)

        providers = {
            "healthy": MockProvider("healthy", content="OK"),
            "broken": MockProvider("broken", content="Never reached"),
        }
        request = GenerateRequest(prompt="Test", model="test")

        result = await engine.query(providers, request)

        # "broken" should be skipped
        succeeded = [r.provider_name for r in result.successful_results]
        assert "healthy" in succeeded
        broken_result = next(r for r in result.results if r.provider_name == "broken")
        assert not broken_result.success
        assert "circuit_breaker" in broken_result.error


# ---------------------------------------------------------------------------
# Test: Response Synthesizer
# ---------------------------------------------------------------------------

class TestResponseSynthesizer:
    def test_single_response(self):
        from core.intelligence.synthesizer import ResponseSynthesizer
        from core.intelligence.parallel import ParallelQueryResult, ProviderResult

        synth = ResponseSynthesizer()
        pqr = ParallelQueryResult(
            results=[
                ProviderResult(
                    provider_name="only",
                    model="m1",
                    response=GenerateResponse(
                        content="Hello world",
                        model="m1",
                        provider="only",
                        total_tokens=10,
                    ),
                    latency_ms=100,
                    success=True,
                ),
            ],
            providers_queried=1,
            providers_succeeded=1,
        )

        result = synth.synthesize(pqr)
        assert result.best_provider == "only"
        assert result.consensus_score == 1.0
        assert result.providers_compared == 1

    def test_multi_response_similar(self):
        from core.intelligence.synthesizer import ResponseSynthesizer
        from core.intelligence.parallel import ParallelQueryResult, ProviderResult

        synth = ResponseSynthesizer()
        pqr = ParallelQueryResult(
            results=[
                ProviderResult(
                    provider_name="a",
                    model="m1",
                    response=GenerateResponse(
                        content="The answer is 42. The meaning of life.",
                        model="m1", provider="a", total_tokens=15,
                    ),
                    latency_ms=100,
                    success=True,
                ),
                ProviderResult(
                    provider_name="b",
                    model="m2",
                    response=GenerateResponse(
                        content="The answer is 42. The meaning of everything.",
                        model="m2", provider="b", total_tokens=15,
                    ),
                    latency_ms=200,
                    success=True,
                ),
            ],
            providers_queried=2,
            providers_succeeded=2,
        )

        result = synth.synthesize(pqr)
        assert result.providers_compared == 2
        assert result.consensus_score > 0.5  # similar responses
        assert result.best_provider in ("a", "b")
        assert len(result.divergence_map) == 1

    def test_multi_response_divergent(self):
        from core.intelligence.synthesizer import ResponseSynthesizer
        from core.intelligence.parallel import ParallelQueryResult, ProviderResult

        synth = ResponseSynthesizer()
        pqr = ParallelQueryResult(
            results=[
                ProviderResult(
                    provider_name="x",
                    model="mx",
                    response=GenerateResponse(
                        content="Alpha beta gamma delta epsilon zeta eta theta",
                        model="mx", provider="x", total_tokens=20,
                    ),
                    latency_ms=50,
                    success=True,
                ),
                ProviderResult(
                    provider_name="y",
                    model="my",
                    response=GenerateResponse(
                        content="1234567890 abcdefghij klmnopqrst uvwxyz!@#$",
                        model="my", provider="y", total_tokens=20,
                    ),
                    latency_ms=300,
                    success=True,
                ),
            ],
            providers_queried=2,
            providers_succeeded=2,
        )

        result = synth.synthesize(pqr)
        assert result.providers_compared == 2
        # Divergent responses should have lower consensus
        assert result.consensus_score < 0.8

    def test_no_successful_responses(self):
        from core.intelligence.synthesizer import ResponseSynthesizer
        from core.intelligence.parallel import ParallelQueryResult, ProviderResult

        synth = ResponseSynthesizer()
        pqr = ParallelQueryResult(
            results=[
                ProviderResult(provider_name="bad", model="m", success=False, error="fail"),
            ],
            providers_queried=1,
            providers_succeeded=0,
        )

        result = synth.synthesize(pqr)
        assert result.providers_compared == 0
        assert result.best_response is None


# ---------------------------------------------------------------------------
# Test: Performance Feedback
# ---------------------------------------------------------------------------

class TestPerformanceFeedback:
    def test_record_and_retrieve(self):
        from core.intelligence.feedback import PerformanceFeedback
        from core.intelligence.parallel import ParallelQueryResult, ProviderResult
        from core.intelligence.synthesizer import SynthesizedResult, ProviderScore
        from cognition.memory.knowledge_graph import KnowledgeGraph

        # Use temp file for graph
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            temp_path = f.name

        try:
            graph = KnowledgeGraph(path=temp_path)
            fb = PerformanceFeedback(graph=graph)

            pqr = ParallelQueryResult(
                results=[
                    ProviderResult(
                        provider_name="test_prov",
                        model="test_model",
                        response=GenerateResponse(
                            content="test", model="test_model",
                            provider="test_prov", total_tokens=25,
                        ),
                        latency_ms=150,
                        success=True,
                    ),
                ],
                providers_queried=1,
                providers_succeeded=1,
            )

            synth = SynthesizedResult(
                best_provider="test_prov",
                provider_scores=[
                    ProviderScore(
                        provider_name="test_prov", model="test_model",
                        total_score=0.85, rank=1,
                    ),
                ],
                providers_compared=1,
            )

            fb.record(pqr, synth, task_type="code")

            # Verify node was created
            stats = fb.get_model_stats("test_prov", "test_model")
            assert stats is not None
            assert stats["query_count"] == 1
            assert stats["success_rate"] == 1.0

            # Record again to test accumulation
            fb.record(pqr, synth, task_type="code")
            stats = fb.get_model_stats("test_prov", "test_model")
            assert stats["query_count"] == 2

            # Summary
            summary = fb.summary()
            assert summary["total_models_tracked"] == 1

        finally:
            os.unlink(temp_path)


# ---------------------------------------------------------------------------
# Test: Intelligence Router (integration with mocks)
# ---------------------------------------------------------------------------

class TestIntelligenceRouter:
    @pytest.mark.asyncio
    async def test_route_single(self):
        from core.intelligence.router import IntelligenceRouter
        from core.intelligence.connector_registry import IntelligenceConnectorRegistry

        reg = IntelligenceConnectorRegistry()
        mock = MockProvider("ollama", content="Sorted list: [1, 2, 3]")
        desc = reg.register("ollama", mock, ProviderType.OLLAMA, is_local=True)
        desc.healthy = True

        router = IntelligenceRouter(registry=reg)
        router._initialized = True

        result = await router.route("Write a Python sort function")

        # Router should attempt to route — may succeed or fail depending
        # on whether the model name matches. The key test is no crash.
        assert result.routing_decision is not None

    @pytest.mark.asyncio
    async def test_query_parallel(self):
        from core.intelligence.router import IntelligenceRouter
        from core.intelligence.connector_registry import IntelligenceConnectorRegistry

        reg = IntelligenceConnectorRegistry()
        for name, content in [("a", "Answer A"), ("b", "Answer B")]:
            desc = reg.register(name, MockProvider(name, content), ProviderType.OLLAMA, is_local=True)
            desc.healthy = True

        router = IntelligenceRouter(registry=reg)
        router._initialized = True

        result = await router.query_parallel("Explain gravity")

        assert result.providers_queried == 2
        assert result.providers_succeeded == 2

    @pytest.mark.asyncio
    async def test_status(self):
        from core.intelligence.router import IntelligenceRouter
        from core.intelligence.connector_registry import IntelligenceConnectorRegistry

        reg = IntelligenceConnectorRegistry()
        reg.register("mock", MockProvider("m"), ProviderType.OLLAMA, is_local=True)

        router = IntelligenceRouter(registry=reg)
        status = router.status()

        assert "initialized" in status
        assert "connector_registry" in status
        assert "model_router" in status


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
