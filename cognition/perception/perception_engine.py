"""
Perception Engine — orchestrates the full perception cycle.

Pipeline:  intake → classify → buffer → detect changes → build context

This is the public API for Motor 1.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from cognition.types import CognitiveContext, CognitiveSignal, SignalSource
from cognition.perception.signal_intake import SignalIntake
from cognition.perception.event_classifier import EventClassifier
from cognition.perception.observation_buffer import ObservationBuffer
from cognition.perception.change_detector import ChangeDetector, ChangeReport
from cognition.perception.context_builder import ContextBuilder

logger = logging.getLogger("vectrax.cognition.perception")


class PerceptionEngine:
    """
    Motor 1 — World Perception.

    Usage::

        engine = PerceptionEngine()
        context = engine.perceive()           # full cycle
        events  = engine.recent_events(20)    # query buffer
    """

    def __init__(self, buffer_size: int = 1000, persist: bool = True) -> None:
        self._intake = SignalIntake()
        self._classifier = EventClassifier()
        self._buffer = ObservationBuffer(max_size=buffer_size, persist=persist)
        self._change_detector = ChangeDetector()
        self._context_builder = ContextBuilder()
        self._last_context: Optional[CognitiveContext] = None
        self._last_change_report: Optional[ChangeReport] = None

    # ------------------------------------------------------------------
    # Main cycle
    # ------------------------------------------------------------------

    def perceive(
        self,
        connector_names: Optional[List[str]] = None,
        extra_signals: Optional[List[CognitiveSignal]] = None,
    ) -> CognitiveContext:
        """
        Execute one full perception cycle:

          1. Poll UCE connectors for new signals
          2. Merge any extra (internal) signals
          3. Classify all signals
          4. Store in observation buffer
          5. Run change detection
          6. Build and return CognitiveContext
        """
        t0 = time.time()

        # 1. Intake from connectors
        signals = self._intake.poll_connectors(connector_names)

        # 2. Merge extras
        if extra_signals:
            signals.extend(extra_signals)

        # 3. Classify
        signals = self._classifier.classify_batch(signals)

        # 4. Buffer
        self._buffer.add_batch(signals)

        # 5. Change detection
        change_report = self._change_detector.detect(signals)
        self._last_change_report = change_report

        # 6. Build context
        context = self._context_builder.build(
            signals=signals,
            changes_detected=change_report.total_changes,
        )
        self._last_context = context

        elapsed = (time.time() - t0) * 1000
        logger.info(
            "Perception cycle: %d signals, %d changes, %.1fms",
            len(signals), change_report.total_changes, elapsed,
        )
        return context

    # ------------------------------------------------------------------
    # Convenience queries
    # ------------------------------------------------------------------

    def recent_events(self, n: int = 20) -> List[CognitiveSignal]:
        """Return the *n* most recent signals from the buffer."""
        return self._buffer.recent(n)

    def buffer_stats(self) -> dict:
        """Aggregate buffer statistics."""
        return self._buffer.stats()

    @property
    def last_context(self) -> Optional[CognitiveContext]:
        return self._last_context

    @property
    def last_change_report(self) -> Optional[ChangeReport]:
        return self._last_change_report

    def inject_signal(
        self,
        raw_data: Dict[str, Any],
        source: str = "internal",
        source_type: SignalSource = SignalSource.INTERNAL,
    ) -> CognitiveSignal:
        """
        Inject a single internal signal (e.g. from daemon events).
        Classifies and stores it in the buffer.
        """
        signal = self._intake.ingest_raw(raw_data, source, source_type)
        signal = self._classifier.classify(signal)
        self._buffer.add(signal)
        return signal

    def status(self) -> Dict[str, Any]:
        """Status summary for CLI / inspection."""
        buf_stats = self._buffer.stats()
        return {
            "buffer_size": self._buffer.size,
            "buffer_max": self._buffer.max_size,
            "buffer_stats": buf_stats,
            "last_context": self._last_context.to_dict() if self._last_context else None,
            "last_changes": self._last_change_report.to_dict() if self._last_change_report else None,
        }
