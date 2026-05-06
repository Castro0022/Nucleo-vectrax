"""
core/scale/cost_tracker.py — Per-engine cost & latency tracking.

Decorador @track_engine_cost("engine_name") + clase CostTracker.

Mide y persiste por motor:
  - latency_ms
  - success (bool)
  - error_type (clase de excepción si falla)
  - estimated_tokens_input  (best-effort, basado en len(args))
  - estimated_tokens_output (best-effort, basado en len(return))
  - timestamp

Storage: in-memory thread-safe ring buffer + agregados acumulativos.
NUNCA rompe la función decorada. Si el tracker falla, se ignora.

API:
    from core.scale.cost_tracker import track_engine_cost, get_global_tracker

    @track_engine_cost("intent_classifier")
    def classify(text):
        ...

    tracker = get_global_tracker()
    print(tracker.get_engine_stats("intent_classifier"))
    print(tracker.get_global_stats())
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger("vectrax.scale.cost_tracker")


# ---------------------------------------------------------------------------
# Record schema
# ---------------------------------------------------------------------------

@dataclass
class CostRecord:
    engine_name: str
    latency_ms: float
    success: bool
    error_type: Optional[str]
    estimated_tokens_input: int
    estimated_tokens_output: int
    timestamp: float


# ---------------------------------------------------------------------------
# Aggregates per engine
# ---------------------------------------------------------------------------

@dataclass
class EngineAggregate:
    calls: int = 0
    successes: int = 0
    failures: int = 0
    total_latency_ms: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    last_error: Optional[str] = None
    last_call_ts: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        avg_latency = (
            self.total_latency_ms / self.calls if self.calls > 0 else 0.0
        )
        success_rate = (
            self.successes / self.calls if self.calls > 0 else 0.0
        )
        return {
            "calls": self.calls,
            "successes": self.successes,
            "failures": self.failures,
            "success_rate": round(success_rate, 4),
            "avg_latency_ms": round(avg_latency, 2),
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "last_error": self.last_error,
            "last_call_ts": self.last_call_ts,
        }


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------

class CostTracker:
    """Thread-safe in-memory cost tracker.

    Mantiene un ring buffer (últimos N records) + agregados acumulativos
    por motor. Bajo carga alta, los records antiguos se descartan; los
    agregados sobreviven.
    """

    DEFAULT_RING_SIZE = 2048

    def __init__(self, ring_size: int = DEFAULT_RING_SIZE) -> None:
        self._records: Deque[CostRecord] = deque(maxlen=ring_size)
        self._aggregates: Dict[str, EngineAggregate] = defaultdict(EngineAggregate)
        self._lock = threading.Lock()

    def record(self, rec: CostRecord) -> None:
        """Persist a single CostRecord. Defensive: never raises."""
        try:
            with self._lock:
                self._records.append(rec)
                agg = self._aggregates[rec.engine_name]
                agg.calls += 1
                if rec.success:
                    agg.successes += 1
                else:
                    agg.failures += 1
                    agg.last_error = rec.error_type
                agg.total_latency_ms += rec.latency_ms
                agg.total_tokens_in += rec.estimated_tokens_input
                agg.total_tokens_out += rec.estimated_tokens_output
                agg.last_call_ts = rec.timestamp
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("CostTracker.record swallowed: %s", exc)

    def get_engine_stats(self, engine_name: str) -> Dict[str, Any]:
        with self._lock:
            agg = self._aggregates.get(engine_name)
            if agg is None:
                return {
                    "engine_name": engine_name,
                    "calls": 0, "successes": 0, "failures": 0,
                    "success_rate": 0.0, "avg_latency_ms": 0.0,
                    "total_tokens_in": 0, "total_tokens_out": 0,
                    "last_error": None, "last_call_ts": 0.0,
                }
            return {"engine_name": engine_name, **agg.to_dict()}

    def get_global_stats(self) -> Dict[str, Any]:
        with self._lock:
            engines = {n: a.to_dict() for n, a in self._aggregates.items()}
            total_calls = sum(a.calls for a in self._aggregates.values())
            total_failures = sum(a.failures for a in self._aggregates.values())
            return {
                "total_engines": len(engines),
                "total_calls": total_calls,
                "total_failures": total_failures,
                "global_success_rate": (
                    round(1 - (total_failures / total_calls), 4)
                    if total_calls > 0 else 1.0
                ),
                "engines": engines,
            }

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
            self._aggregates.clear()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_global_tracker: Optional[CostTracker] = None
_global_lock = threading.Lock()


def get_global_tracker() -> CostTracker:
    global _global_tracker
    with _global_lock:
        if _global_tracker is None:
            _global_tracker = CostTracker()
    return _global_tracker


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

def _estimate_tokens(value: Any) -> int:
    """Best-effort token estimate. Roughly 1 token per ~4 chars."""
    try:
        if value is None:
            return 0
        if isinstance(value, str):
            return max(1, len(value) // 4)
        if isinstance(value, (list, tuple)):
            return sum(_estimate_tokens(v) for v in value)
        if isinstance(value, dict):
            return sum(_estimate_tokens(v) for v in value.values())
        s = str(value)
        return max(1, len(s) // 4)
    except Exception:
        return 0


def track_engine_cost(engine_name: str) -> Callable:
    """Decorator: mide latencia/tokens/éxito de la función.

    Uso:
        @track_engine_cost("intent_classifier")
        def classify(text):
            ...

    Defensive: si el tracker falla, NO afecta a la función decorada.
    """

    def decorator(func: Callable) -> Callable:

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            t0 = time.time()
            error: Optional[str] = None
            success = False
            result: Any = None
            try:
                result = func(*args, **kwargs)
                success = True
                return result
            except Exception as exc:
                error = exc.__class__.__name__
                raise
            finally:
                try:
                    latency_ms = (time.time() - t0) * 1000.0
                    tokens_in = (
                        _estimate_tokens(args) + _estimate_tokens(kwargs)
                    )
                    tokens_out = _estimate_tokens(result) if success else 0
                    rec = CostRecord(
                        engine_name=engine_name,
                        latency_ms=latency_ms,
                        success=success,
                        error_type=error,
                        estimated_tokens_input=tokens_in,
                        estimated_tokens_output=tokens_out,
                        timestamp=time.time(),
                    )
                    get_global_tracker().record(rec)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("track_engine_cost finally swallowed: %s", exc)

        # Preserve a hint for introspection / tests
        wrapper.__vectrax_engine_name__ = engine_name  # type: ignore[attr-defined]
        return wrapper

    return decorator
