"""
connectors/freight/base.py — Abstract FreightFeedProvider interface.

Architecture
------------
Every freight data source (simulator, DAT, Truckstop, internal broker CRM,
proprietary feed) implements this interface.  The FreightLearningCycle only
knows about FreightFeedProvider; it never imports a concrete adapter.

Selection is driven by the FREIGHT_FEED_PROVIDER environment variable
(default: "simulator").  Adding a new source requires:
  1. Create a class that extends FreightFeedProvider.
  2. Register it in connectors/freight/__init__.get_provider().
  3. Set FREIGHT_FEED_PROVIDER=<name> in the server .env.

No code in the learning cycle changes.

FreightEvent schema
-------------------
Matches the connectors/freight/freight_logistics.json domain template:
  {
    "event_type": str,           # quote_request | load_booking | delivery_complete
                                 # delay_reported | rate_change | capacity_update
                                 # weather_event | empty_miles
    "data": dict,                # fields defined in the domain template
    "source": str,               # provider identifier (for ledger traceability)
    "ts": float,                 # unix timestamp
  }

Creador: Mario Bravo Castro
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Canonical event type
# ---------------------------------------------------------------------------

class FreightEvent:
    """Immutable freight event ready for ingest."""

    __slots__ = ("event_type", "data", "source", "ts")

    def __init__(
        self,
        event_type: str,
        data: Dict[str, Any],
        source: str,
        ts: float,
    ) -> None:
        self.event_type = event_type
        self.data = dict(data)
        self.source = source
        self.ts = ts

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "data": self.data,
            "source": self.source,
            "ts": self.ts,
        }


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class FreightFeedProvider(ABC):
    """
    Contract every freight data source must fulfill.

    A provider is stateless with respect to the caller: each call to
    stream_events() returns a fresh, independent batch.  Internal state
    (e.g. WorldState in the simulator) is the provider's concern.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Short identifier used in logs and ledger entries (e.g. 'simulator')."""

    @abstractmethod
    def stream_events(self, n: int) -> List[FreightEvent]:
        """
        Generate or fetch `n` freight events.

        Args:
            n: Requested batch size.  Providers may return fewer events
               if the underlying source is exhausted or rate-limited.

        Returns:
            List of FreightEvent objects, length ≤ n.
        """

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """
        Verify the provider is operational.

        Returns:
            {
                "provider": str,
                "healthy": bool,
                "latency_ms": float | None,
                "detail": str,
            }
        """
