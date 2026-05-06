"""
core/scale/rate_limiter.py — Token Bucket rate limiter por (user, tier).

Backend: in-memory por defecto. Interfaz preparada para Redis (clase
abstracta `Backend` + `InMemoryBackend`; `RedisBackend` queda como
extension cuando se necesite distribuir).

Límites iniciales (mensajes/día + burst máximo):
  FREE:     20/día,   burst 5
  PRO:     500/día,   burst 30
  BUSINESS: 5000/día, burst 200
  INTERNAL: ilimitado razonable

Token Bucket:
  - capacidad = burst (cuánto se acumula como pico)
  - refill rate = daily_limit / 86400 tokens/segundo
  - cada mensaje cuesta 1 token
  - allow() devuelve True si hay >= 1 token; consume() lo descuenta.

Concurrency: thread-safe vía lock por bucket.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from core.scale.pipeline_policy import Tier


# ---------------------------------------------------------------------------
# Límites por tier
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TierLimits:
    daily_limit: int
    burst: int


_LIMITS_BY_TIER: Dict[Tier, TierLimits] = {
    Tier.FREE: TierLimits(daily_limit=20, burst=5),
    Tier.PRO: TierLimits(daily_limit=500, burst=30),
    Tier.BUSINESS: TierLimits(daily_limit=5000, burst=200),
    Tier.INTERNAL: TierLimits(daily_limit=10**9, burst=10**6),
}


# ---------------------------------------------------------------------------
# Backend interface (in-memory + slot for Redis later)
# ---------------------------------------------------------------------------

class Backend:
    """Abstract storage backend for token buckets."""

    def get_bucket(self, key: str) -> Tuple[float, float]:
        """Return (tokens, last_refill_ts). If absent, return (None, None)."""
        raise NotImplementedError

    def set_bucket(self, key: str, tokens: float, last_refill_ts: float) -> None:
        raise NotImplementedError


class InMemoryBackend(Backend):
    """Thread-safe in-process dict. No persistence across restarts."""

    def __init__(self) -> None:
        self._store: Dict[str, Tuple[float, float]] = {}
        self._lock = threading.Lock()

    def get_bucket(self, key: str) -> Tuple[Optional[float], Optional[float]]:
        with self._lock:
            return self._store.get(key, (None, None))

    def set_bucket(self, key: str, tokens: float, last_refill_ts: float) -> None:
        with self._lock:
            self._store[key] = (tokens, last_refill_ts)


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Token Bucket por (user_id, tier).

    Usage:
        rl = RateLimiter()
        if not rl.allow(user_id, tier):
            return "Rate limit exceeded"
        rl.consume(user_id, tier)
        # ... process request ...
    """

    def __init__(self, backend: Optional[Backend] = None) -> None:
        self._backend = backend or InMemoryBackend()
        self._key_lock = threading.Lock()
        self._per_key_locks: Dict[str, threading.Lock] = {}

    def get_limits_for_tier(self, tier: Tier) -> TierLimits:
        """Return the (daily_limit, burst) for a given tier."""
        return _LIMITS_BY_TIER.get(tier, _LIMITS_BY_TIER[Tier.FREE])

    def _key(self, user_id: str, tier: Tier) -> str:
        return f"rl:{tier.value}:{user_id}"

    def _get_or_create_lock(self, key: str) -> threading.Lock:
        with self._key_lock:
            lock = self._per_key_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._per_key_locks[key] = lock
            return lock

    def _refill_and_get(
        self,
        key: str,
        tier: Tier,
    ) -> Tuple[float, threading.Lock]:
        """Compute current tokens after refill (no consume yet).
        Returns (current_tokens, lock_held_externally).
        Caller must hold the per-key lock.
        """
        limits = self.get_limits_for_tier(tier)
        capacity = float(limits.burst)
        refill_per_sec = limits.daily_limit / 86400.0  # tokens/s

        tokens, last_ts = self._backend.get_bucket(key)
        now = time.time()
        if tokens is None or last_ts is None:
            # Cold start: full bucket
            tokens = capacity
            last_ts = now
        else:
            elapsed = max(0.0, now - last_ts)
            tokens = min(capacity, tokens + elapsed * refill_per_sec)
            last_ts = now

        self._backend.set_bucket(key, tokens, last_ts)
        return tokens, None  # lock signature for symmetry

    def allow(self, user_id: str, tier: Tier) -> bool:
        """Return True if at least 1 token is available (no consumption)."""
        key = self._key(user_id, tier)
        lock = self._get_or_create_lock(key)
        with lock:
            tokens, _ = self._refill_and_get(key, tier)
            return tokens >= 1.0

    def consume(self, user_id: str, tier: Tier, tokens: int = 1) -> bool:
        """Consume `tokens` if available. Returns True on success.

        If insufficient tokens, returns False and DOES NOT consume.
        """
        if tokens <= 0:
            return True
        key = self._key(user_id, tier)
        lock = self._get_or_create_lock(key)
        with lock:
            available, _ = self._refill_and_get(key, tier)
            if available < tokens:
                return False
            new_tokens = available - tokens
            self._backend.set_bucket(key, new_tokens, time.time())
            return True

    def reset(self, user_id: str, tier: Tier) -> None:
        """Test/admin helper: clear bucket for a (user, tier) pair."""
        key = self._key(user_id, tier)
        lock = self._get_or_create_lock(key)
        with lock:
            limits = self.get_limits_for_tier(tier)
            self._backend.set_bucket(key, float(limits.burst), time.time())


# ---------------------------------------------------------------------------
# Module singleton (default in-memory)
# ---------------------------------------------------------------------------

_default: Optional[RateLimiter] = None
_default_lock = threading.Lock()


def get_default_rate_limiter() -> RateLimiter:
    global _default
    with _default_lock:
        if _default is None:
            _default = RateLimiter()
    return _default
