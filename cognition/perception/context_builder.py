"""
Context Builder — assembles a CognitiveContext from recent signals
and current system state.

Integrates data from:
  - ObservationBuffer (recent signals)
  - core/state_manager.py (governor mode, cycles, health)
  - ChangeDetector (delta report)
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import List, Optional

from cognition.types import CognitiveContext, CognitiveSignal, SignalCategory

logger = logging.getLogger("vectrax.cognition.perception.context_builder")


class ContextBuilder:
    """
    Builds a CognitiveContext snapshot from a list of classified signals
    and current system metadata.
    """

    def build(
        self,
        signals: List[CognitiveSignal],
        changes_detected: int = 0,
    ) -> CognitiveContext:
        """
        Build a context snapshot.

        Args:
            signals: classified signals from the current cycle.
            changes_detected: number of significant changes from ChangeDetector.
        """
        governor_mode, system_health = self._read_system_state()

        # Dominant category
        dominant = self._dominant_category(signals)

        # Average priority
        avg_p = 0.0
        if signals:
            avg_p = sum(s.priority for s in signals) / len(signals)

        return CognitiveContext(
            signals=signals,
            signal_count=len(signals),
            dominant_category=dominant,
            avg_priority=round(avg_p, 4),
            governor_mode=governor_mode,
            system_health=system_health,
            changes_detected=changes_detected,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _dominant_category(signals: List[CognitiveSignal]) -> Optional[SignalCategory]:
        """Find the most frequent non-NOISE category."""
        if not signals:
            return None
        cats = [s.category for s in signals if s.category != SignalCategory.NOISE]
        if not cats:
            return SignalCategory.NOISE
        counter = Counter(cats)
        return counter.most_common(1)[0][0]

    @staticmethod
    def _read_system_state() -> tuple[str, str]:
        """Read governor mode and health from state_manager (best-effort)."""
        try:
            from core import state_manager
            state = state_manager.load()
            mode = state.get("governor_mode", "observe")
            errors = state.get("errors_since_boot", 0)
            cycles = state.get("cycles", 0)
            if errors == 0:
                health = "healthy"
            elif errors <= 2:
                health = "degraded"
            else:
                health = "unhealthy"
            return mode, health
        except Exception:
            return "observe", "unknown"
