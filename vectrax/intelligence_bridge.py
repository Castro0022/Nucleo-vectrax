"""
Vectrax Intelligence Bridge
==============================
Bridges the synchronous ``creator chat`` loop with the async
IntelligenceRouter.  Provides simple sync functions that the CLI
can call without managing its own event loop.

Privacy: no message content is stored — only aggregated operational
metrics flow to the feedback layer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional

logger = logging.getLogger("vectrax.intelligence_bridge")

# Cache the router + event loop across calls within a session
_loop: Optional[asyncio.AbstractEventLoop] = None
_router = None
_initialized = False


def _get_loop() -> asyncio.AbstractEventLoop:
    """Return a persistent event loop for the bridge."""
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
    return _loop


def _run(coro):
    """
    Run an async coroutine synchronously.

    Race-safe: if a loop is already running in the current thread
    (e.g. caller is inside async context), close the coroutine
    cleanly and raise RuntimeError so caller can fall back to a
    sync-friendly path. Avoids 'coroutine was never awaited'
    warnings on RuntimeError paths.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        # Cannot nest run_until_complete inside a running loop.
        # Close the coroutine to suppress 'never awaited' warning.
        try:
            coro.close()
        except Exception:
            pass
        raise RuntimeError(
            "intelligence_bridge._run() called from within a running "
            "event loop; bridge is sync-only"
        )
    try:
        return _get_loop().run_until_complete(coro)
    except BaseException:
        # If run_until_complete fails *before* awaiting (rare), close.
        try:
            coro.close()
        except Exception:
            pass
        raise


def _get_router():
    """Lazy-init the IntelligenceRouter singleton."""
    global _router
    if _router is None:
        from core.intelligence.router import IntelligenceRouter
        _router = IntelligenceRouter()
    return _router


# ---------------------------------------------------------------------------
# Public API (sync)
# ---------------------------------------------------------------------------

def initialize() -> Dict:
    """
    Initialize the Intelligence Router: auto-detect providers.
    Call once at chat session start. Returns status dict.

    No-op when called from inside a running asyncio loop
    (production async pipelines): the caller will fall back
    to the direct httpx path in external_gateway.
    """
    global _initialized
    # Bail early if we are inside an async loop — the sync bridge
    # cannot run there. Caller falls back to httpx direct.
    try:
        asyncio.get_running_loop()
        _initialized = False
        return {
            "error": "running event loop detected — bridge skipped",
            "providers_detected": [],
        }
    except RuntimeError:
        pass

    try:
        router = _get_router()
    except Exception as exc:
        logger.debug("Intelligence Router build failed: %s", exc)
        _initialized = False
        return {"error": str(exc), "providers_detected": []}

    coro = router.initialize()
    try:
        result = _run(coro)
        _initialized = True
        return result
    except Exception as exc:
        logger.debug("Intelligence Router init failed: %s", exc)
        _initialized = False
        return {"error": str(exc), "providers_detected": []}


def is_ready() -> bool:
    """Check if the router was successfully initialized."""
    return _initialized


def route_single(
    prompt: str,
    *,
    context: Optional[str] = None,
    system_prompt: Optional[str] = None,
) -> Dict:
    """
    Route a prompt to the best single model and return the response.

    Returns dict with keys: success, content, provider, model, latency_ms, error.
    """
    if not _initialized:
        return {"success": False, "error": "Intelligence Router not initialized"}

    try:
        router = _get_router()
        result = _run(router.route(
            prompt,
            context=context,
            system_prompt=system_prompt,
        ))
        if result.success and result.response:
            return {
                "success": True,
                "content": result.response.content,
                "provider": result.response.provider,
                "model": result.response.model,
                "latency_ms": round(result.latency_ms, 1),
                "tokens": result.response.total_tokens,
                "task_type": result.routing_decision.task_classification.task_type.value,
            }
        else:
            return {
                "success": False,
                "error": result.error or "No response",
                "task_type": result.routing_decision.task_classification.task_type.value,
            }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def query_multi(
    prompt: str,
    *,
    context: Optional[str] = None,
    system_prompt: Optional[str] = None,
) -> Dict:
    """
    Query all available models in parallel, synthesize, and return the best.

    Returns dict with keys: success, content, best_provider, consensus_score,
    providers_compared, task_type, latency_ms, synthesis.
    """
    if not _initialized:
        return {"success": False, "error": "Intelligence Router not initialized"}

    try:
        router = _get_router()
        result = _run(router.query_synthesized(
            prompt,
            context=context,
            system_prompt=system_prompt,
        ))
        if result.best_content:
            return {
                "success": True,
                "content": result.best_content,
                "best_provider": result.synthesis.best_provider,
                "consensus_score": round(result.synthesis.consensus_score, 4),
                "providers_compared": result.synthesis.providers_compared,
                "task_type": result.task_type,
                "latency_ms": round(result.total_latency_ms, 1),
                "synthesis": result.synthesis.to_dict(),
            }
        else:
            return {
                "success": False,
                "error": "No successful responses from any model",
                "providers_compared": result.synthesis.providers_compared,
            }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def router_status() -> Dict:
    """Return full router status."""
    try:
        router = _get_router()
        return router.status()
    except Exception as exc:
        return {"error": str(exc)}


def cleanup():
    """Close all provider connections and event loop."""
    global _router, _loop, _initialized
    if _router is not None:
        try:
            _run(_router._registry.close_all())
        except Exception:
            pass
        _router = None
    if _loop is not None and not _loop.is_closed():
        _loop.close()
        _loop = None
    _initialized = False
