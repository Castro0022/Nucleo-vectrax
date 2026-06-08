"""
API Rate Limit Gate — Centralized 429 Protection
===================================================
Single source of truth for API rate limit state across ALL call sites.
When any call site receives a 429, the gate closes for that provider
with exponential backoff. All other call sites check the gate BEFORE
making a request, avoiding wasted calls.

Usage:
    from core.api_gate import check_gate, record_429, record_success

    if not check_gate("openai"):
        # Gate is closed — skip the call, return fallback
        return None
    # ... make the API call ...
    if response.status_code == 429:
        record_429("openai")
    else:
        record_success("openai")

Supports: openai, gemini, tavily, any string provider key.

Backoff schedule (429-specific):
    1st 429:   60s cooldown
    2nd 429:  120s
    3rd 429:  300s  (5 min)
    4th 429:  600s  (10 min)
    5th+ 429: 900s  (15 min, max)

Separate from the circuit breaker (which handles general failures).
This gate is ONLY for HTTP 429 rate limits.

Creado: 2026-06-08
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import time
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

logger = logging.getLogger("vectrax.api_gate")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Backoff schedule: consecutive 429 count → cooldown seconds
_BACKOFF_SCHEDULE = [60, 120, 300, 600, 900]  # max 15 min
_MAX_COOLDOWN = 900  # 15 minutes ceiling


@dataclass
class ProviderGate:
    """Rate limit state for a single provider."""
    consecutive_429s: int = 0
    gate_closed_until: float = 0.0
    last_429_at: float = 0.0
    last_success_at: float = 0.0
    total_429s: int = 0
    total_successes: int = 0
    total_blocked: int = 0  # calls blocked by gate


# Global state — thread-safe via lock
_lock = threading.Lock()
_gates: Dict[str, ProviderGate] = {}


def _get_gate(provider: str) -> ProviderGate:
    """Get or create gate for a provider. Must be called under _lock."""
    if provider not in _gates:
        _gates[provider] = ProviderGate()
    return _gates[provider]


def _get_cooldown(consecutive: int) -> float:
    """Get cooldown seconds for the given consecutive 429 count."""
    idx = min(consecutive - 1, len(_BACKOFF_SCHEDULE) - 1)
    if idx < 0:
        return _BACKOFF_SCHEDULE[0]
    return _BACKOFF_SCHEDULE[idx]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_gate(provider: str) -> bool:
    """
    Check if a request to this provider is allowed.

    Returns True if the gate is open (proceed with request).
    Returns False if the gate is closed (skip the request).
    """
    with _lock:
        gate = _get_gate(provider)
        now = time.time()

        if now >= gate.gate_closed_until:
            # Gate is open
            return True

        # Gate is closed — exponential backoff active
        remaining = gate.gate_closed_until - now
        gate.total_blocked += 1

        # Log only occasionally to avoid spam
        if gate.total_blocked % 10 == 1:
            logger.info(
                "API_GATE CLOSED | %s | %ds remaining | "
                "consecutive_429s=%d | total_blocked=%d",
                provider, int(remaining),
                gate.consecutive_429s, gate.total_blocked,
            )
        return False


def record_429(provider: str) -> float:
    """
    Record a 429 response from this provider.
    Closes the gate with exponential backoff.

    Returns the cooldown duration in seconds.
    """
    with _lock:
        gate = _get_gate(provider)
        now = time.time()
        gate.consecutive_429s += 1
        gate.total_429s += 1
        gate.last_429_at = now

        cooldown = _get_cooldown(gate.consecutive_429s)
        gate.gate_closed_until = now + cooldown

        logger.warning(
            "API_GATE 429 | %s | consecutive=%d | cooldown=%ds | "
            "total_429s=%d | gate_until=%s",
            provider, gate.consecutive_429s, int(cooldown),
            gate.total_429s,
            time.strftime("%H:%M:%S", time.localtime(gate.gate_closed_until)),
        )
        return cooldown


def record_success(provider: str) -> None:
    """
    Record a successful response. Resets consecutive 429 count.
    """
    with _lock:
        gate = _get_gate(provider)
        gate.consecutive_429s = 0
        gate.gate_closed_until = 0.0
        gate.last_success_at = time.time()
        gate.total_successes += 1


def get_gate_status(provider: str) -> Dict:
    """Get current gate status for a provider."""
    with _lock:
        gate = _get_gate(provider)
        now = time.time()
        is_open = now >= gate.gate_closed_until
        remaining = max(0, gate.gate_closed_until - now)
        return {
            "provider": provider,
            "open": is_open,
            "remaining_s": round(remaining, 1),
            "consecutive_429s": gate.consecutive_429s,
            "total_429s": gate.total_429s,
            "total_successes": gate.total_successes,
            "total_blocked": gate.total_blocked,
        }


def get_all_gates() -> Dict[str, Dict]:
    """Get status of all tracked providers."""
    with _lock:
        return {
            provider: {
                "open": time.time() >= gate.gate_closed_until,
                "remaining_s": round(max(0, gate.gate_closed_until - time.time()), 1),
                "consecutive_429s": gate.consecutive_429s,
                "total_429s": gate.total_429s,
                "total_blocked": gate.total_blocked,
            }
            for provider, gate in _gates.items()
        }


def reset_gate(provider: str) -> None:
    """Manually reset a provider's gate (e.g. after fixing billing)."""
    with _lock:
        if provider in _gates:
            _gates[provider] = ProviderGate()
            logger.info("API_GATE RESET | %s", provider)


def reset_all_gates() -> None:
    """Reset all gates."""
    with _lock:
        _gates.clear()
        logger.info("API_GATE RESET ALL")
