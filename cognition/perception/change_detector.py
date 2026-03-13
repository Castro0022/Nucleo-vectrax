"""
Change Detector — compares successive observation snapshots.

Identifies significant deltas (new sources, category shifts, priority
spikes) that warrant attention from the reasoning engine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from cognition.types import CognitiveSignal, SignalCategory

logger = logging.getLogger("vectrax.cognition.perception.change_detector")

# Minimum priority delta to count as a "spike"
PRIORITY_SPIKE_THRESHOLD = 0.3

# Minimum number of signals to even consider change detection
MIN_SIGNALS = 2


@dataclass
class ChangeReport:
    """Summary of changes between two observation windows."""
    new_sources: List[str] = field(default_factory=list)
    lost_sources: List[str] = field(default_factory=list)
    category_shifts: List[Dict[str, Any]] = field(default_factory=list)
    priority_spikes: List[Dict[str, Any]] = field(default_factory=list)
    total_changes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "new_sources": self.new_sources,
            "lost_sources": self.lost_sources,
            "category_shifts": self.category_shifts,
            "priority_spikes": self.priority_spikes,
            "total_changes": self.total_changes,
        }


class ChangeDetector:
    """
    Stateful detector that compares the current signal batch against
    the previous one to find meaningful changes.
    """

    def __init__(self) -> None:
        self._prev_sources: set[str] = set()
        self._prev_categories: Dict[str, str] = {}   # source → category
        self._prev_priorities: Dict[str, float] = {}  # source → priority

    def detect(self, signals: List[CognitiveSignal]) -> ChangeReport:
        """
        Compare *signals* against the previous snapshot.
        Returns a ChangeReport and updates internal state.
        """
        report = ChangeReport()

        if len(signals) < MIN_SIGNALS:
            self._update_state(signals)
            return report

        current_sources = {s.source for s in signals}
        current_categories = {s.source: s.category.value for s in signals}
        current_priorities = {s.source: s.priority for s in signals}

        # New / lost sources
        report.new_sources = sorted(current_sources - self._prev_sources)
        report.lost_sources = sorted(self._prev_sources - current_sources)

        # Category shifts
        for source in current_sources & self._prev_sources:
            prev_cat = self._prev_categories.get(source)
            curr_cat = current_categories.get(source)
            if prev_cat and curr_cat and prev_cat != curr_cat:
                report.category_shifts.append({
                    "source": source,
                    "from": prev_cat,
                    "to": curr_cat,
                })

        # Priority spikes
        for source in current_sources & self._prev_sources:
            prev_p = self._prev_priorities.get(source, 0.0)
            curr_p = current_priorities.get(source, 0.0)
            delta = curr_p - prev_p
            if abs(delta) >= PRIORITY_SPIKE_THRESHOLD:
                report.priority_spikes.append({
                    "source": source,
                    "from": round(prev_p, 4),
                    "to": round(curr_p, 4),
                    "delta": round(delta, 4),
                })

        report.total_changes = (
            len(report.new_sources)
            + len(report.lost_sources)
            + len(report.category_shifts)
            + len(report.priority_spikes)
        )

        self._update_state(signals)
        return report

    def _update_state(self, signals: List[CognitiveSignal]) -> None:
        self._prev_sources = {s.source for s in signals}
        self._prev_categories = {s.source: s.category.value for s in signals}
        self._prev_priorities = {s.source: s.priority for s in signals}

    def reset(self) -> None:
        """Clear internal comparison state."""
        self._prev_sources.clear()
        self._prev_categories.clear()
        self._prev_priorities.clear()
