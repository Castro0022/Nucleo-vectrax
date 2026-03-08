"""
Vectrax Parallel Query Engine
===============================
Fires GenerateRequest to N providers concurrently.
Returns all responses with per-provider timing and error isolation.

Each provider is queried independently — a failure in one provider
does not affect others (fail-isolated by design).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core.abstraction.base import BaseLLMProvider, GenerateRequest, GenerateResponse
from core.routing.circuit_breaker import CircuitBreaker

logger = logging.getLogger("vectrax.intelligence.parallel")


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class ProviderResult:
    """Result from a single provider in a parallel query."""
    provider_name: str
    model: str
    response: Optional[GenerateResponse] = None
    error: Optional[str] = None
    latency_ms: float = 0.0
    success: bool = False

    def to_dict(self) -> Dict:
        d = {
            "provider": self.provider_name,
            "model": self.model,
            "success": self.success,
            "latency_ms": round(self.latency_ms, 2),
        }
        if self.response:
            d["content_length"] = len(self.response.content)
            d["total_tokens"] = self.response.total_tokens
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class ParallelQueryResult:
    """Aggregated result from a parallel multi-model query."""
    results: List[ProviderResult] = field(default_factory=list)
    total_latency_ms: float = 0.0
    providers_queried: int = 0
    providers_succeeded: int = 0

    @property
    def successful_results(self) -> List[ProviderResult]:
        return [r for r in self.results if r.success]

    @property
    def failed_results(self) -> List[ProviderResult]:
        return [r for r in self.results if not r.success]

    def to_dict(self) -> Dict:
        return {
            "providers_queried": self.providers_queried,
            "providers_succeeded": self.providers_succeeded,
            "total_latency_ms": round(self.total_latency_ms, 2),
            "results": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# Parallel Query Engine
# ---------------------------------------------------------------------------

class ParallelQueryEngine:
    """
    Fires a GenerateRequest to multiple providers concurrently.

    Features:
    - asyncio.gather with return_exceptions for fault isolation
    - Per-provider timeout enforcement
    - Circuit breaker integration (skip providers with open circuits)
    - Configurable concurrency limit

    Usage::

        engine = ParallelQueryEngine()
        result = await engine.query(
            providers={"ollama": ollama_instance, "openai": openai_instance},
            request=GenerateRequest(prompt="hello", model="auto"),
            model_map={"ollama": "llama3.2:3b", "openai": "gpt-4o-mini"},
        )
    """

    def __init__(
        self,
        circuit_breaker: Optional[CircuitBreaker] = None,
        timeout_per_provider: float = 30.0,
        max_concurrency: int = 5,
    ):
        self._cb = circuit_breaker or CircuitBreaker()
        self._timeout = timeout_per_provider
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def query(
        self,
        providers: Dict[str, BaseLLMProvider],
        request: GenerateRequest,
        model_map: Optional[Dict[str, str]] = None,
    ) -> ParallelQueryResult:
        """
        Query multiple providers in parallel.

        Args:
            providers: Dict mapping provider_name → BaseLLMProvider instance.
            request: Base GenerateRequest (model field is overridden per provider).
            model_map: Optional dict mapping provider_name → model name.
                       If not given, uses request.model for all.

        Returns:
            ParallelQueryResult with all individual ProviderResults.
        """
        model_map = model_map or {}
        t0 = time.time()

        # Filter by circuit breaker
        eligible = {
            name: prov for name, prov in providers.items()
            if self._cb.can_execute(name)
        }

        skipped = set(providers.keys()) - set(eligible.keys())
        for name in skipped:
            logger.debug("Skipping %s — circuit breaker open", name)

        # Build tasks
        tasks = []
        task_names = []
        for name, provider in eligible.items():
            model = model_map.get(name, request.model)
            tasks.append(self._query_single(name, provider, request, model))
            task_names.append(name)

        # Execute concurrently
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Build result
        results: List[ProviderResult] = []
        for name, raw in zip(task_names, raw_results):
            if isinstance(raw, ProviderResult):
                results.append(raw)
                # Update circuit breaker
                if raw.success:
                    self._cb.record_success(name)
                else:
                    self._cb.record_failure(name)
            elif isinstance(raw, Exception):
                results.append(ProviderResult(
                    provider_name=name,
                    model=model_map.get(name, request.model),
                    error=f"{type(raw).__name__}: {raw}",
                    success=False,
                ))
                self._cb.record_failure(name, raw)

        # Add skipped providers as failures
        for name in skipped:
            results.append(ProviderResult(
                provider_name=name,
                model=model_map.get(name, request.model),
                error="circuit_breaker_open",
                success=False,
            ))

        total_latency = (time.time() - t0) * 1000

        return ParallelQueryResult(
            results=results,
            total_latency_ms=total_latency,
            providers_queried=len(providers),
            providers_succeeded=sum(1 for r in results if r.success),
        )

    async def _query_single(
        self,
        name: str,
        provider: BaseLLMProvider,
        base_request: GenerateRequest,
        model: str,
    ) -> ProviderResult:
        """Query a single provider with timeout and error isolation."""
        async with self._semaphore:
            t0 = time.time()
            try:
                # Create provider-specific request
                req = GenerateRequest(
                    prompt=base_request.prompt,
                    model=model,
                    system_prompt=base_request.system_prompt,
                    temperature=base_request.temperature,
                    max_tokens=base_request.max_tokens,
                    stop_sequences=base_request.stop_sequences,
                )

                response = await asyncio.wait_for(
                    provider.generate(req),
                    timeout=self._timeout,
                )

                latency = (time.time() - t0) * 1000

                return ProviderResult(
                    provider_name=name,
                    model=model,
                    response=response,
                    latency_ms=latency,
                    success=True,
                )

            except asyncio.TimeoutError:
                latency = (time.time() - t0) * 1000
                logger.warning("Provider %s timed out after %.0fms", name, latency)
                return ProviderResult(
                    provider_name=name,
                    model=model,
                    error=f"timeout after {self._timeout}s",
                    latency_ms=latency,
                    success=False,
                )
            except Exception as exc:
                latency = (time.time() - t0) * 1000
                logger.warning("Provider %s failed: %s", name, exc)
                return ProviderResult(
                    provider_name=name,
                    model=model,
                    error=f"{type(exc).__name__}: {exc}",
                    latency_ms=latency,
                    success=False,
                )
