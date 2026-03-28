"""
Vectrax Market Data — Local Cache Service.

Thread-safe, TTL-based cache with per-symbol granularity.
Short TTL for spot prices, longer for OHLCV and snapshots.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("vectrax.market.cache")

# TTL defaults (seconds)
TTL_SPOT = 5          # Spot prices: 5 seconds
TTL_TICKER = 15       # 24h ticker: 15 seconds
TTL_OHLCV = 60        # Candlestick data: 60 seconds
TTL_QUOTE = 30        # Stock quotes: 30 seconds
TTL_SNAPSHOT = 30     # Market snapshot: 30 seconds
TTL_DEFAULT = 30


class CacheEntry:
    __slots__ = ("value", "expires_at", "source")

    def __init__(self, value: Any, ttl: float, source: str = ""):
        self.value = value
        self.expires_at = time.time() + ttl
        self.source = source

    @property
    def expired(self) -> bool:
        return time.time() > self.expires_at

    @property
    def age_seconds(self) -> float:
        return max(0, time.time() - (self.expires_at - TTL_DEFAULT))


class MarketCache:
    """Thread-safe in-memory cache for market data."""

    def __init__(self) -> None:
        self._data: Dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        """Get cached value if not expired. Returns None on miss."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None or entry.expired:
                self._misses += 1
                if entry and entry.expired:
                    del self._data[key]
                return None
            self._hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl: float = TTL_DEFAULT, source: str = "") -> None:
        """Store a value with TTL."""
        with self._lock:
            self._data[key] = CacheEntry(value, ttl, source)

    def invalidate(self, key: str) -> bool:
        """Remove a specific cache entry."""
        with self._lock:
            if key in self._data:
                del self._data[key]
                return True
            return False

    def invalidate_prefix(self, prefix: str) -> int:
        """Remove all entries matching a prefix."""
        with self._lock:
            keys = [k for k in self._data if k.startswith(prefix)]
            for k in keys:
                del self._data[k]
            return len(keys)

    def clear(self) -> None:
        """Clear all cached data."""
        with self._lock:
            self._data.clear()
            self._hits = 0
            self._misses = 0

    def cleanup(self) -> int:
        """Remove all expired entries. Returns count removed."""
        with self._lock:
            expired_keys = [k for k, v in self._data.items() if v.expired]
            for k in expired_keys:
                del self._data[k]
            return len(expired_keys)

    @property
    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "entries": len(self._data),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 3) if total > 0 else 0,
            }

    # ── Convenience keys ────────────────────────────────────────────

    @staticmethod
    def spot_key(symbol: str) -> str:
        return f"spot:{symbol.upper()}"

    @staticmethod
    def ticker_key(symbol: str) -> str:
        return f"ticker:{symbol.upper()}"

    @staticmethod
    def ohlcv_key(symbol: str, interval: str) -> str:
        return f"ohlcv:{symbol.upper()}:{interval}"

    @staticmethod
    def quote_key(symbol: str) -> str:
        return f"quote:{symbol.upper()}"


# ── Module-level singleton ──────────────────────────────────────────
_cache: Optional[MarketCache] = None


def get_market_cache() -> MarketCache:
    """Return the global MarketCache singleton."""
    global _cache
    if _cache is None:
        _cache = MarketCache()
    return _cache
