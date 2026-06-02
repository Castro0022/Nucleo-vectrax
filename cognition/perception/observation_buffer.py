"""
Observation Buffer — circular in-memory buffer for cognitive signals.

Keeps the last *max_size* signals.  Optionally persists to
vault/cognition_buffer.jsonl for post-mortem analysis.
"""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from typing import List, Optional

from cognition.types import CognitiveSignal, SignalCategory

logger = logging.getLogger("vectrax.cognition.perception.buffer")

VAULT_DIR = os.environ.get(
    "VECTRAX_VAULT_DIR",
    os.path.join(os.path.expanduser("~"), "Vectrax", "vault"),
)
BUFFER_PATH = os.path.join(VAULT_DIR, "cognition_buffer.jsonl")

DEFAULT_MAX_SIZE = 1000


class ObservationBuffer:
    """
    Circular buffer for cognitive signals.

    Provides filtered views: ``recent(n)``, ``by_source(name)``,
    ``by_priority(min_p)``, ``by_category(cat)``.
    """

    def __init__(
        self,
        max_size: int = DEFAULT_MAX_SIZE,
        persist: bool = True,
    ) -> None:
        self._buffer: deque[CognitiveSignal] = deque(maxlen=max_size)
        self._persist = persist

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add(self, signal: CognitiveSignal) -> None:
        """Add a signal to the buffer (and optionally persist)."""
        self._buffer.append(signal)
        if self._persist:
            self._append_to_file(signal)

    def add_batch(self, signals: List[CognitiveSignal]) -> None:
        """Add multiple signals."""
        for s in signals:
            self.add(s)

    # ------------------------------------------------------------------
    # Read / query
    # ------------------------------------------------------------------

    def recent(self, n: int = 20) -> List[CognitiveSignal]:
        """Return the *n* most recent signals (newest first)."""
        items = list(self._buffer)
        return list(reversed(items[-n:]))

    def by_source(self, source: str) -> List[CognitiveSignal]:
        """Filter by connector/source name."""
        return [s for s in self._buffer if s.source == source]

    def by_priority(self, min_priority: float) -> List[CognitiveSignal]:
        """Return signals with priority >= *min_priority*."""
        return [s for s in self._buffer if s.priority >= min_priority]

    def by_category(self, category: SignalCategory) -> List[CognitiveSignal]:
        """Filter by category."""
        return [s for s in self._buffer if s.category == category]

    @property
    def size(self) -> int:
        return len(self._buffer)

    @property
    def max_size(self) -> int:
        return self._buffer.maxlen or DEFAULT_MAX_SIZE

    def clear(self) -> None:
        self._buffer.clear()

    def stats(self) -> dict:
        """Aggregate statistics (abstract only)."""
        if not self._buffer:
            return {"total": 0}
        cats: dict[str, int] = {}
        sources: dict[str, int] = {}
        total_priority = 0.0
        for s in self._buffer:
            cats[s.category.value] = cats.get(s.category.value, 0) + 1
            sources[s.source] = sources.get(s.source, 0) + 1
            total_priority += s.priority
        return {
            "total": len(self._buffer),
            "avg_priority": round(total_priority / len(self._buffer), 4),
            "categories": cats,
            "sources": sources,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _append_to_file(self, signal: CognitiveSignal) -> None:
        try:
            os.makedirs(VAULT_DIR, exist_ok=True)
            with open(BUFFER_PATH, "a", encoding="utf-8") as f:
                # Only abstract metadata — no raw content
                f.write(json.dumps({
                    "id": signal.id,
                    "source": signal.source,
                    "category": signal.category.value,
                    "priority": signal.priority,
                    "intent": signal.intent,
                    "ts": signal.timestamp,
                }, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.debug("Buffer persist failed: %s", exc)
