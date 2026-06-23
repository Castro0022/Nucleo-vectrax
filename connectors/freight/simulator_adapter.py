"""
connectors/freight/simulator_adapter.py — Simulator adapter for FreightFeedProvider.

Wraps scripts/freight_simulator.WorldState + generate_event inside the
FreightFeedProvider contract.  The simulator is the default provider until a
real freight API feed (DAT, Truckstop, etc.) is integrated.

The adapter maintains its own WorldState so time advances naturally across
consecutive stream_events() calls within the same process lifetime.  A new
process starts fresh (seed from env FREIGHT_SIMULATOR_SEED, default 42).

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List

from connectors.freight.base import FreightEvent, FreightFeedProvider

_DOMAIN = "freight_logistics"
_SOURCE = "simulator"


class SimulatorAdapter(FreightFeedProvider):
    """
    Generates synthetic freight events using the WorldState model.

    Thread-safety: each instance owns its WorldState; callers that share an
    adapter across threads must provide external locking.  The pipeline worker
    creates one adapter per cycle run, so no locking is needed in practice.
    """

    def __init__(self, seed: int | None = None) -> None:
        from scripts.freight_simulator import WorldState
        _seed = seed if seed is not None else int(
            os.environ.get("FREIGHT_SIMULATOR_SEED", "42")
        )
        self._ws = WorldState(seed=_seed)
        self._events_streamed = 0

    @property
    def provider_name(self) -> str:
        return _SOURCE

    def stream_events(self, n: int) -> List[FreightEvent]:
        from scripts.freight_simulator import generate_event
        events: List[FreightEvent] = []
        for i in range(max(0, n)):
            # Advance simulation day every 10 events (matches simulator's own rhythm)
            if i % 10 == 0:
                self._ws.advance_day()
            raw = generate_event(self._ws)
            events.append(FreightEvent(
                event_type=raw["event_type"],
                data=raw["data"],
                source=_SOURCE,
                ts=time.time(),
            ))
            self._events_streamed += 1
        return events

    def health_check(self) -> Dict[str, Any]:
        try:
            from scripts.freight_simulator import WorldState, generate_event
            ws = WorldState(seed=0)
            generate_event(ws)
            return {
                "provider": _SOURCE,
                "healthy": True,
                "latency_ms": 0.0,
                "detail": f"simulator ok | streamed_so_far={self._events_streamed}",
            }
        except Exception as exc:
            return {
                "provider": _SOURCE,
                "healthy": False,
                "latency_ms": None,
                "detail": str(exc),
            }


class RealFeedAdapter(FreightFeedProvider):
    """
    Stub for a real-time freight API (DAT, Truckstop, broker CRM, etc.).

    To activate:
      1. Implement stream_events() by calling the real API.
      2. Set FREIGHT_FEED_PROVIDER=real in the server .env.
      3. Set FREIGHT_API_KEY + FREIGHT_API_URL.

    The learning cycle requires NO changes.
    """

    @property
    def provider_name(self) -> str:
        return "real"

    def stream_events(self, n: int) -> List[FreightEvent]:
        raise NotImplementedError(
            "RealFeedAdapter.stream_events() is not implemented. "
            "Set FREIGHT_FEED_PROVIDER=simulator or implement this method "
            "with your freight data API."
        )

    def health_check(self) -> Dict[str, Any]:
        return {
            "provider": "real",
            "healthy": False,
            "latency_ms": None,
            "detail": "RealFeedAdapter not implemented. Use FREIGHT_FEED_PROVIDER=simulator.",
        }
