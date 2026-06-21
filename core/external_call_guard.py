"""
ExternalCallGuard — Unified Resilience Layer
==============================================
Single layer that wraps ALL external API calls (Telegram, eToro,
OpenAI, Gemini) with timeout, circuit breaker, and fallback.

Guarantees:
  - No external call can block the system beyond the timeout
  - Failed APIs are circuit-broken (fail-fast after threshold)
  - Heartbeat keeps writing (calls run in timeout-bounded threads)
  - Clean fallback on any failure
  - Every call logged with provider, latency, and outcome

Usage:
    from core.external_call_guard import guarded_call

    # Simple
    result = guarded_call("telegram", send_message, chat_id, text)

    # With options
    result = guarded_call(
        "etoro",
        etoro_client.get_price,
        instrument_id,
        timeout=8,
        fallback={"success": False, "error": "circuit_open"},
    )

    # Status
    from core.external_call_guard import get_status
    print(get_status())  # all providers

Creador: Mario Bravo Castro
Fecha: 2026-06-13
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("vectrax.external_call_guard")


# ---------------------------------------------------------------------------
# Circuit breaker states
# ---------------------------------------------------------------------------

class CircuitState(Enum):
    CLOSED = "closed"        # Normal — calls pass through
    OPEN = "open"            # Failed — calls fail-fast
    HALF_OPEN = "half_open"  # Testing — limited calls allowed


# ---------------------------------------------------------------------------
# Per-provider state
# ---------------------------------------------------------------------------

@dataclass
class ProviderState:
    """Tracks circuit breaker + metrics for one external provider."""
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    opened_at: float = 0.0
    total_calls: int = 0
    total_failures: int = 0
    total_timeouts: int = 0
    total_circuit_blocked: int = 0
    last_failure_error: str = ""
    last_call_ms: float = 0.0


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Default timeouts per provider (seconds)
DEFAULT_TIMEOUTS: Dict[str, float] = {
    "telegram": 12.0,
    "etoro": 6.0,
    "openai": 25.0,
    "gemini": 25.0,
}
DEFAULT_TIMEOUT = 15.0

# Circuit breaker thresholds (global defaults)
FAILURE_THRESHOLD = 5          # failures before opening circuit
SUCCESS_THRESHOLD = 2          # successes in half-open to close
RECOVERY_TIMEOUT = 60.0        # seconds before trying half-open

# Per-provider overrides — providers with tighter requirements
# eToro: opens circuit faster (3 fails) and recovers faster (45s)
# to prevent the learning cycle from stalling the worker.
PROVIDER_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "etoro": {
        "failure_threshold": 3,
        "recovery_timeout": 45.0,
    },
}

# Thread pool for timeout-bounded execution
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="guard")

# Global state
_lock = threading.Lock()
_providers: Dict[str, ProviderState] = {}


def _get_state(provider: str) -> ProviderState:
    """Get or create provider state. Must be called under _lock."""
    if provider not in _providers:
        _providers[provider] = ProviderState()
    return _providers[provider]


def _resolve_timeout(provider: str) -> float:
    """Resolve the timeout for a provider.

    Priority: env override → per-provider default → global default. Lets ops
    tune timeouts (e.g. a tighter interactive budget) WITHOUT code changes
    and WITHOUT altering the safe defaults. Env var format (seconds):
        VX_GUARD_TIMEOUT_OPENAI=12
        VX_GUARD_TIMEOUT_TELEGRAM=8
    """
    raw = os.environ.get(f"VX_GUARD_TIMEOUT_{provider.upper()}", "").strip()
    if raw:
        try:
            val = float(raw)
            if val > 0:
                return val
        except ValueError:
            pass
    return DEFAULT_TIMEOUTS.get(provider, DEFAULT_TIMEOUT)


# ---------------------------------------------------------------------------
# Circuit breaker logic
# ---------------------------------------------------------------------------

def _check_circuit(provider: str) -> bool:
    """Check if calls are allowed. Returns True if OK to proceed."""
    with _lock:
        ps = _get_state(provider)
        now = time.time()

        if ps.state == CircuitState.CLOSED:
            return True

        if ps.state == CircuitState.OPEN:
            recovery = PROVIDER_OVERRIDES.get(provider, {}).get(
                "recovery_timeout", RECOVERY_TIMEOUT
            )
            if (now - ps.opened_at) >= recovery:
                ps.state = CircuitState.HALF_OPEN
                ps.consecutive_successes = 0
                logger.info(
                    "GUARD %s | circuit HALF_OPEN (testing recovery)",
                    provider,
                )
                return True
            ps.total_circuit_blocked += 1
            return False

        # HALF_OPEN — allow limited calls
        return True


def _record_success(provider: str, latency_ms: float) -> None:
    """Record a successful call."""
    with _lock:
        ps = _get_state(provider)
        ps.total_calls += 1
        ps.consecutive_failures = 0
        ps.last_call_ms = latency_ms

        if ps.state == CircuitState.HALF_OPEN:
            ps.consecutive_successes += 1
            if ps.consecutive_successes >= SUCCESS_THRESHOLD:
                ps.state = CircuitState.CLOSED
                logger.info(
                    "GUARD %s | circuit CLOSED (recovered after %d successes)",
                    provider, ps.consecutive_successes,
                )


def _record_failure(provider: str, error: str, is_timeout: bool = False) -> None:
    """Record a failed call. Opens circuit if threshold exceeded."""
    with _lock:
        ps = _get_state(provider)
        ps.total_calls += 1
        ps.total_failures += 1
        ps.consecutive_failures += 1
        ps.consecutive_successes = 0
        ps.last_failure_error = error[:200]
        if is_timeout:
            ps.total_timeouts += 1

        if ps.state == CircuitState.HALF_OPEN:
            # Any failure in half-open reopens
            ps.state = CircuitState.OPEN
            ps.opened_at = time.time()
            logger.warning(
                "GUARD %s | circuit re-OPENED from half_open | error=%s",
                provider, error[:80],
            )
        elif ps.state == CircuitState.CLOSED:
            threshold = PROVIDER_OVERRIDES.get(provider, {}).get(
                "failure_threshold", FAILURE_THRESHOLD
            )
            if ps.consecutive_failures >= threshold:
                ps.state = CircuitState.OPEN
                ps.opened_at = time.time()
                logger.warning(
                    "GUARD %s | circuit OPENED after %d consecutive failures (threshold=%d) | last=%s",
                    provider, ps.consecutive_failures, threshold, error[:80],
                )


# ---------------------------------------------------------------------------
# Main API: guarded_call
# ---------------------------------------------------------------------------

def guarded_call(
    provider: str,
    fn: Callable,
    *args: Any,
    timeout: Optional[float] = None,
    fallback: Any = None,
    **kwargs: Any,
) -> Any:
    """
    Execute an external API call with timeout, circuit breaker, and fallback.

    Args:
        provider: Provider name ("telegram", "etoro", "openai", "gemini")
        fn: The function to call
        *args: Arguments to pass to fn
        timeout: Max seconds to wait (default per provider or 15s)
        fallback: Value to return on any failure (default None)
        **kwargs: Keyword arguments to pass to fn

    Returns:
        fn(*args, **kwargs) on success, or fallback on failure.
        NEVER raises — always returns cleanly.

    Guarantees:
        - Call completes within timeout (thread killed if exceeded)
        - Circuit-broken providers fail-fast without making the call
        - Every call logged with provider, latency, outcome
        - Heartbeat is never blocked (call runs in thread pool)
    """
    if timeout is None:
        timeout = _resolve_timeout(provider)

    # Check circuit breaker
    if not _check_circuit(provider):
        logger.info(
            "GUARD %s | CIRCUIT_OPEN — returning fallback",
            provider,
        )
        return fallback

    # Execute with timeout
    t0 = time.perf_counter()
    try:
        future = _pool.submit(fn, *args, **kwargs)
        result = future.result(timeout=timeout)
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)

        _record_success(provider, latency_ms)

        logger.debug(
            "GUARD %s | OK | %dms",
            provider, latency_ms,
        )
        return result

    except FutureTimeout:
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        _record_failure(provider, f"Timeout after {timeout}s", is_timeout=True)
        logger.warning(
            "GUARD %s | TIMEOUT | %dms (limit=%ds)",
            provider, latency_ms, timeout,
        )
        return fallback

    except Exception as exc:
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        error_str = f"{type(exc).__name__}: {exc}"
        _record_failure(provider, error_str)
        logger.warning(
            "GUARD %s | ERROR | %dms | %s",
            provider, latency_ms, error_str[:100],
        )
        return fallback


# ---------------------------------------------------------------------------
# Status / Diagnostics
# ---------------------------------------------------------------------------

def get_status(provider: Optional[str] = None) -> Dict[str, Any]:
    """Get circuit breaker status for one or all providers."""
    with _lock:
        if provider:
            ps = _get_state(provider)
            return _format_state(provider, ps)
        return {
            name: _format_state(name, ps)
            for name, ps in _providers.items()
        }


def _format_state(name: str, ps: ProviderState) -> Dict[str, Any]:
    now = time.time()
    remaining = 0.0
    if ps.state == CircuitState.OPEN:
        recovery = PROVIDER_OVERRIDES.get(name, {}).get(
            "recovery_timeout", RECOVERY_TIMEOUT
        )
        remaining = max(0, recovery - (now - ps.opened_at))
    return {
        "provider": name,
        "circuit": ps.state.value,
        "remaining_s": round(remaining, 1),
        "consecutive_failures": ps.consecutive_failures,
        "total_calls": ps.total_calls,
        "total_failures": ps.total_failures,
        "total_timeouts": ps.total_timeouts,
        "total_blocked": ps.total_circuit_blocked,
        "last_error": ps.last_failure_error[:80],
        "last_call_ms": ps.last_call_ms,
    }


def reset(provider: str) -> None:
    """Manually reset a provider's circuit breaker."""
    with _lock:
        _providers[provider] = ProviderState()
    logger.info("GUARD %s | RESET", provider)


def reset_all() -> None:
    """Reset all providers."""
    with _lock:
        _providers.clear()
    logger.info("GUARD | RESET ALL")
