"""
Vectrax Intelligence Router
==============================
Main entry point for the multi-model cognitive routing system.

Composes existing immutable subsystems:
  - ModelRouter      → task classification + single-model scoring
  - ProviderRegistry → provider instance management
  - ParallelQueryEngine → concurrent multi-model querying
  - ResponseSynthesizer → response comparison and synthesis
  - PerformanceFeedback → learning via KnowledgeGraph
  - CircuitBreaker   → per-provider resilience

Three query modes:
  1. route()            — classify + route to single best model
  2. query_parallel()   — fire to N models concurrently
  3. query_synthesized() — parallel + synthesize + feedback loop
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.abstraction.base import BaseLLMProvider, GenerateRequest, GenerateResponse
from core.routing.model_router import ModelRouter, RoutingDecision, get_model_router
from core.routing.circuit_breaker import CircuitBreaker
from core.routing.capabilities import (
    get_available_models,
    ModelProfile,
    refresh_availability,
)
from core.routing.task_classifier import classify_task

from core.intelligence.connector_registry import (
    IntelligenceConnectorRegistry,
    get_connector_registry,
)
from core.intelligence.parallel import (
    ParallelQueryEngine,
    ParallelQueryResult,
)
from core.intelligence.synthesizer import (
    ResponseSynthesizer,
    SynthesizedResult,
)
from core.intelligence.feedback import PerformanceFeedback

logger = logging.getLogger("vectrax.intelligence.router")


# ---------------------------------------------------------------------------
# Query result containers
# ---------------------------------------------------------------------------

@dataclass
class SingleQueryResult:
    """Result of a single-model routed query."""
    routing_decision: RoutingDecision
    response: Optional[GenerateResponse] = None
    error: Optional[str] = None
    latency_ms: float = 0.0
    success: bool = False

    def to_dict(self) -> Dict:
        d = {
            "routing": self.routing_decision.to_dict(),
            "success": self.success,
            "latency_ms": round(self.latency_ms, 2),
        }
        if self.response:
            d["provider"] = self.response.provider
            d["model"] = self.response.model
            d["content_length"] = len(self.response.content)
            d["total_tokens"] = self.response.total_tokens
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class SynthesizedQueryResult:
    """Full result of a synthesized multi-model query."""
    routing_decision: RoutingDecision
    parallel_result: ParallelQueryResult
    synthesis: SynthesizedResult
    best_content: str = ""
    task_type: str = ""
    total_latency_ms: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "task_type": self.task_type,
            "routing": self.routing_decision.to_dict(),
            "parallel": self.parallel_result.to_dict(),
            "synthesis": self.synthesis.to_dict(),
            "best_provider": self.synthesis.best_provider,
            "consensus_score": round(self.synthesis.consensus_score, 4),
            "total_latency_ms": round(self.total_latency_ms, 2),
        }


# ---------------------------------------------------------------------------
# Intelligence Router
# ---------------------------------------------------------------------------

class IntelligenceRouter:
    """
    Multi-model cognitive router.

    Orchestrates task classification, model selection, parallel querying,
    response synthesis, and performance feedback — while keeping all
    subsystems immutable and composable.

    Usage::

        router = IntelligenceRouter()
        await router.initialize()

        # Single model
        result = await router.route("Write a Python sort function")

        # Parallel (all available models)
        result = await router.query_parallel("Explain gravity")

        # Synthesized (parallel + comparison + learning)
        result = await router.query_synthesized("Solve x² + 3x - 4 = 0")
    """

    def __init__(
        self,
        registry: Optional[IntelligenceConnectorRegistry] = None,
        model_router: Optional[ModelRouter] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        timeout_per_provider: float = 30.0,
    ) -> None:
        self._registry = registry or get_connector_registry()
        self._model_router = model_router or get_model_router()
        self._cb = circuit_breaker or CircuitBreaker()

        self._parallel = ParallelQueryEngine(
            circuit_breaker=self._cb,
            timeout_per_provider=timeout_per_provider,
        )
        self._synthesizer = ResponseSynthesizer()
        self._feedback = PerformanceFeedback()
        self._initialized = False

    async def initialize(self) -> Dict:
        """
        Auto-detect and register available providers.
        Should be called once before querying.
        """
        detected = await self._registry.auto_detect()
        self._initialized = True
        logger.info(
            "IntelligenceRouter initialized with %d providers: %s",
            len(detected), detected,
        )
        return {
            "providers_detected": detected,
            "registry_status": self._registry.status(),
        }

    # -- Mode 1: Single model routing ---------------------------------------

    async def route(
        self,
        prompt: str,
        context: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> SingleQueryResult:
        """
        Route a prompt to the single best model and return the response.

        Uses ModelRouter for scoring, then dispatches via the provider registry.
        Falls back through circuit breaker if primary fails.
        """
        t0 = time.time()

        # Classify and select model
        decision = self._model_router.route(prompt, context)
        provider_name = decision.primary.provider
        model_name = decision.primary.model

        # Interaction-learning state — recorded EXACTLY ONCE in `finally`,
        # after the provider/fallback path completes (router-level SSOT for
        # provider_stars; providers are not instrumented individually).
        learn = {
            "provider": provider_name,
            "model": model_name,
            "outcome": "fail",
            "fallback_used": False,
            "error_type": None,
            "response": None,
        }

        try:
            # Get provider instance
            provider = self._registry.get(provider_name)
            if provider is None:
                # Try fallback
                if decision.fallback:
                    provider_name = decision.fallback.provider
                    model_name = decision.fallback.model
                    provider = self._registry.get(provider_name)
                    learn["provider"] = provider_name
                    learn["model"] = model_name
                    learn["fallback_used"] = True

            if provider is None:
                learn["error_type"] = "NoProviderAvailable"
                return SingleQueryResult(
                    routing_decision=decision,
                    error=f"No provider available for {decision.primary.provider}",
                    latency_ms=(time.time() - t0) * 1000,
                )

            # Execute — identity injected at provider level
            try:
                request = GenerateRequest(
                    prompt=prompt,
                    model=model_name,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                response = await provider.generate(request)
                latency = (time.time() - t0) * 1000

                self._cb.record_success(provider_name)
                self._audit("route_single", decision, provider_name, model_name, True)

                learn["outcome"] = "success"
                learn["response"] = response

                return SingleQueryResult(
                    routing_decision=decision,
                    response=response,
                    latency_ms=latency,
                    success=True,
                )

            except Exception as exc:
                latency = (time.time() - t0) * 1000
                self._cb.record_failure(provider_name, exc)
                self._audit("route_single", decision, provider_name, model_name, False)

                learn["outcome"] = self._outcome_from_exc(exc)
                learn["error_type"] = type(exc).__name__

                # Attempt fallback
                if decision.fallback and decision.fallback.provider != provider_name:
                    fb_provider = self._registry.get(decision.fallback.provider)
                    if fb_provider:
                        learn["provider"] = decision.fallback.provider
                        learn["model"] = decision.fallback.model
                        learn["fallback_used"] = True
                        try:
                            request = GenerateRequest(
                                prompt=prompt,
                                model=decision.fallback.model,
                                system_prompt=system_prompt,
                                temperature=temperature,
                                max_tokens=max_tokens,
                            )
                            response = await fb_provider.generate(request)
                            latency = (time.time() - t0) * 1000
                            self._cb.record_success(decision.fallback.provider)
                            learn["outcome"] = "success"
                            learn["error_type"] = None
                            learn["response"] = response
                            return SingleQueryResult(
                                routing_decision=decision,
                                response=response,
                                latency_ms=latency,
                                success=True,
                            )
                        except Exception as fb_exc:
                            self._cb.record_failure(decision.fallback.provider)
                            learn["outcome"] = self._outcome_from_exc(fb_exc)
                            learn["error_type"] = type(fb_exc).__name__

                return SingleQueryResult(
                    routing_decision=decision,
                    error=f"{type(exc).__name__}: {exc}",
                    latency_ms=latency,
                )
        finally:
            # SSOT: one provider_stars interaction per resolved request.
            self._learn_interaction(
                prompt=prompt,
                latency_ms=(time.time() - t0) * 1000,
                **learn,
            )

    # -- Interaction learning (router-level SSOT) ---------------------------

    @staticmethod
    def _outcome_from_exc(exc: Exception) -> str:
        """Map an exception to an abstract provider_stars outcome token."""
        name = type(exc).__name__.lower()
        text = str(exc).lower()
        if "timeout" in name or "timeout" in text:
            return "timeout"
        if "429" in text or "ratelimit" in name or "rate limit" in text or "rate_limit" in text:
            return "rate_limited"
        return "fail"

    def _learn_interaction(
        self,
        *,
        provider: str,
        model: Optional[str],
        prompt: str,
        outcome: str,
        latency_ms: float,
        fallback_used: bool = False,
        error_type: Optional[str] = None,
        response=None,
    ) -> None:
        """Record ONE provider interaction experience (router-level SSOT).

        Best-effort: never affects routing. Captures the resolved provider,
        model, route source, outcome, latency, fallback usage, error type, and
        response/token size. Providers are not instrumented individually.
        """
        try:
            from core.learn.provider_stars import record_interaction
            tokens = getattr(response, "total_tokens", None) if response is not None else None
            content = getattr(response, "content", None) if response is not None else None
            response_chars = len(content) if isinstance(content, str) else None
            record_interaction(
                provider=provider,
                model=model,
                prompt=prompt,
                route_source="intelligence_router",
                outcome=outcome,
                latency_ms=latency_ms,
                fallback_used=fallback_used,
                error_type=error_type,
                tokens=tokens,
                response_chars=response_chars,
            )
        except Exception:
            pass

    # -- Mode 2: Parallel multi-model query ---------------------------------

    async def query_parallel(
        self,
        prompt: str,
        *,
        providers: Optional[List[str]] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> ParallelQueryResult:
        """
        Query multiple models in parallel.

        Args:
            prompt: The prompt to send.
            providers: Optional list of provider names. If None, uses all healthy.
            system_prompt: Optional system prompt.
            temperature: Generation temperature.
            max_tokens: Max tokens per response.

        Returns:
            ParallelQueryResult with per-provider results.
        """
        # Resolve providers
        provider_map, model_map = self._resolve_providers(providers)

        if not provider_map:
            return ParallelQueryResult(
                providers_queried=0, providers_succeeded=0,
            )

        request = GenerateRequest(
            prompt=prompt,
            model="auto",  # overridden per provider
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        result = await self._parallel.query(provider_map, request, model_map)

        logger.info(
            "Parallel query: %d/%d succeeded in %.0fms",
            result.providers_succeeded,
            result.providers_queried,
            result.total_latency_ms,
        )

        return result

    # -- Mode 3: Synthesized multi-model query ------------------------------

    async def query_synthesized(
        self,
        prompt: str,
        *,
        providers: Optional[List[str]] = None,
        context: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> SynthesizedQueryResult:
        """
        Full cognitive query: parallel → synthesize → feedback.

        This is the primary multi-model intelligence method. It:
        1. Classifies the task
        2. Queries all available models in parallel
        3. Synthesizes responses (comparison + ranking)
        4. Records performance feedback in the KnowledgeGraph
        5. Returns the best response with full analysis

        Args:
            prompt: The prompt to send.
            providers: Optional list of provider names.
            context: Optional context for task classification.
            system_prompt: Optional system prompt.
            temperature: Generation temperature.
            max_tokens: Max tokens per response.

        Returns:
            SynthesizedQueryResult with best content, synthesis, and metadata.
        """
        t0 = time.time()

        # Step 1: Classify task (reuse existing ModelRouter logic)
        decision = self._model_router.route(prompt, context)
        task_type = decision.task_classification.task_type.value

        # Step 2: Parallel query — identity injected at provider level
        parallel_result = await self.query_parallel(
            prompt,
            providers=providers,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # Step 3: Synthesize
        synthesis = self._synthesizer.synthesize(parallel_result)

        # Step 4: Record feedback (non-blocking, fail-safe)
        try:
            self._feedback.record(
                parallel_result=parallel_result,
                synthesized=synthesis,
                task_type=task_type,
            )
        except Exception as exc:
            logger.debug("Feedback recording failed (non-blocking): %s", exc)

        # Step 5: Audit
        self._audit(
            "route_synthesized", decision,
            synthesis.best_provider,
            synthesis.best_response.model if synthesis.best_response else "?",
            synthesis.providers_compared > 0,
        )

        total_latency = (time.time() - t0) * 1000

        best_content = ""
        if synthesis.best_response and synthesis.best_response.response:
            best_content = synthesis.best_response.response.content

        return SynthesizedQueryResult(
            routing_decision=decision,
            parallel_result=parallel_result,
            synthesis=synthesis,
            best_content=best_content,
            task_type=task_type,
            total_latency_ms=total_latency,
        )

    # -- Helpers ------------------------------------------------------------

    def _resolve_providers(
        self, names: Optional[List[str]] = None,
    ) -> tuple:
        """
        Resolve provider names to (provider_map, model_map) for parallel query.

        If names is None, uses all healthy providers from the registry.
        Model selection uses the capability registry's best model per provider.
        """
        provider_map: Dict[str, BaseLLMProvider] = {}
        model_map: Dict[str, str] = {}

        if names:
            target_names = names
        else:
            target_names = self._registry.list_providers(healthy_only=True)

        # Get capability registry for model selection
        refresh_availability()
        available = get_available_models()

        for name in target_names:
            instance = self._registry.get(name)
            if instance is None:
                continue
            provider_map[name] = instance

            # Find best model for this provider from capability registry
            best_model = None
            best_priority = 999
            for key, profile in available.items():
                if profile.provider == name and profile.priority < best_priority:
                    best_model = profile.model
                    best_priority = profile.priority

            if best_model:
                model_map[name] = best_model
            else:
                # Fallback: use first available model from the provider descriptor
                desc = self._registry.get_descriptor(name)
                if desc and desc.models:
                    model_map[name] = desc.models[0]

        return provider_map, model_map

    def _audit(
        self, action: str, decision: RoutingDecision,
        provider: str, model: str, success: bool,
    ) -> None:
        """Record routing decision to audit ledger."""
        try:
            from core.audit_ledger import record
            record(
                action=action,
                actor="intelligence_router",
                decision="approved" if success else "failed",
                reason=decision.reason,
                metadata={
                    **decision.to_dict(),
                    "final_provider": provider,
                    "final_model": model,
                    "success": success,
                },
            )
        except Exception as exc:
            logger.debug("Audit log failed (non-blocking): %s", exc)

    # -- Status -------------------------------------------------------------

    def status(self) -> Dict:
        """Return full router status."""
        return {
            "initialized": self._initialized,
            "model_router": self._model_router.get_status(),
            "connector_registry": self._registry.status(),
            "circuit_breakers": self._cb.get_stats(),
            "feedback_summary": self._feedback.summary(),
        }

    def __repr__(self) -> str:
        providers = self._registry.list_providers()
        return (
            f"IntelligenceRouter(providers={len(providers)}, "
            f"initialized={self._initialized})"
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_router: Optional[IntelligenceRouter] = None


def get_intelligence_router() -> IntelligenceRouter:
    """Return the global IntelligenceRouter singleton."""
    global _router
    if _router is None:
        _router = IntelligenceRouter()
    return _router
