"""
Event Classifier — categorises and prioritises cognitive signals.

Uses heuristic rules to assign a SignalCategory and a priority score.
Integrates with the existing intent engine when applicable.
"""

from __future__ import annotations

import logging
from typing import List

from cognition.types import CognitiveSignal, SignalCategory, SignalSource

logger = logging.getLogger("vectrax.cognition.perception.classifier")

# Priority boosts per source type (more external = more important)
_SOURCE_PRIORITY: dict[SignalSource, float] = {
    SignalSource.API: 0.7,
    SignalSource.WEBHOOK: 0.8,
    SignalSource.CLOUD: 0.6,
    SignalSource.DATABASE: 0.5,
    SignalSource.SHELL: 0.4,
    SignalSource.FILESYSTEM: 0.3,
    SignalSource.INTERNAL: 0.2,
}

# Keywords that suggest categories (applied to summary)
_CATEGORY_KEYWORDS: dict[SignalCategory, list[str]] = {
    SignalCategory.CRITICAL: [
        "error", "crash", "fatal", "security", "breach", "critical",
        "vulnerability", "failure", "down",
    ],
    SignalCategory.ANOMALY: [
        "anomaly", "unexpected", "unusual", "spike", "threshold",
        "deviation", "outlier", "warning",
    ],
    SignalCategory.CHANGE: [
        "change", "modified", "created", "deleted", "updated",
        "added", "removed", "moved", "renamed",
    ],
    SignalCategory.PATTERN: [
        "pattern", "recurring", "trend", "repeated", "frequent",
        "correlation", "cluster",
    ],
}


class EventClassifier:
    """
    Assigns category and priority to cognitive signals.

    Priority is a float ∈ [0, 1] derived from:
      - source_type weight
      - category severity
      - metadata hints (severity, impact)
    """

    def classify(self, signal: CognitiveSignal) -> CognitiveSignal:
        """
        Classify a single signal in-place and return it.
        Sets ``signal.category`` and ``signal.priority``.
        """
        signal.category = self._infer_category(signal)
        signal.priority = self._compute_priority(signal)

        # Attempt intent inference (best-effort)
        if not signal.intent:
            signal.intent = self._infer_intent(signal)

        return signal

    def classify_batch(self, signals: List[CognitiveSignal]) -> List[CognitiveSignal]:
        """Classify a list of signals."""
        return [self.classify(s) for s in signals]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_category(signal: CognitiveSignal) -> SignalCategory:
        """Keyword-based category inference from summary."""
        text = signal.summary.lower()

        # Check severity metadata first
        severity = signal.metadata.get("severity", "").lower()
        if severity in ("critical", "error"):
            return SignalCategory.CRITICAL

        # Keyword scan (highest severity first)
        for cat in (SignalCategory.CRITICAL, SignalCategory.ANOMALY,
                    SignalCategory.CHANGE, SignalCategory.PATTERN):
            keywords = _CATEGORY_KEYWORDS.get(cat, [])
            if any(kw in text for kw in keywords):
                return cat

        return SignalCategory.NOISE

    @staticmethod
    def _compute_priority(signal: CognitiveSignal) -> float:
        """Compute priority ∈ [0, 1]."""
        base = _SOURCE_PRIORITY.get(signal.source_type, 0.2)

        # Category multiplier
        cat_mult = {
            SignalCategory.CRITICAL: 1.0,
            SignalCategory.ANOMALY: 0.8,
            SignalCategory.CHANGE: 0.6,
            SignalCategory.PATTERN: 0.5,
            SignalCategory.NOISE: 0.1,
        }.get(signal.category, 0.1)

        # Impact boost from metadata
        impact = signal.metadata.get("impact", "low")
        impact_boost = {"high": 0.3, "medium": 0.15, "low": 0.0}.get(impact, 0.0)

        priority = min(base * cat_mult + impact_boost, 1.0)
        return round(priority, 4)

    @staticmethod
    def _infer_intent(signal: CognitiveSignal) -> str:
        """Best-effort intent from summary keywords."""
        text = signal.summary.lower()
        intent_hints = {
            "SECURITY": ["security", "auth", "credential", "permission"],
            "BUG_FIX": ["fix", "bug", "error", "patch"],
            "FEATURE_ADD": ["add", "new", "create", "feature"],
            "REFACTOR": ["refactor", "rename", "restructure"],
            "CONFIG": ["config", "setting", "parameter"],
            "PERF": ["performance", "optimize", "cache"],
        }
        for intent, keywords in intent_hints.items():
            if any(kw in text for kw in keywords):
                return intent
        return "UNKNOWN"
