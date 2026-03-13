"""
Impact Analyzer — evaluates impact across three time horizons.

Uses decision history from Motor 3 as precedent, plus heuristic
analysis of the current context.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from cognition.types import CognitiveContext, ImpactAssessment, MemoryContext

logger = logging.getLogger("vectrax.cognition.reasoning.impact")

# Weights per signal category for impact estimation
_CATEGORY_IMPACT = {
    "CRITICAL": 0.9,
    "ANOMALY": 0.6,
    "CHANGE": 0.4,
    "PATTERN": 0.3,
    "NOISE": 0.05,
}


class ImpactAnalyzer:
    """
    Evaluates expected impact of the current context/proposal
    across short (immediate), medium (~7d), and long (30d+) horizons.
    """

    def analyze(
        self,
        context: CognitiveContext,
        memory: Optional[MemoryContext] = None,
    ) -> ImpactAssessment:
        """Compute impact assessment."""
        short = self._short_term(context)
        medium = self._medium_term(context, memory)
        long_ = self._long_term(context, memory)

        details_parts = []
        if short > 0.6:
            details_parts.append("high immediate impact")
        if medium > 0.5:
            details_parts.append("significant medium-term effects")
        if long_ > 0.4:
            details_parts.append("long-term structural impact")
        if not details_parts:
            details_parts.append("low overall impact")

        return ImpactAssessment(
            short_term=round(short, 4),
            medium_term=round(medium, 4),
            long_term=round(long_, 4),
            details="; ".join(details_parts),
        )

    # ------------------------------------------------------------------
    # Horizons
    # ------------------------------------------------------------------

    @staticmethod
    def _short_term(context: CognitiveContext) -> float:
        """Immediate impact from current signals."""
        if not context.signals:
            return 0.0
        # Weighted average of signal priorities
        total = sum(s.priority for s in context.signals)
        avg = total / len(context.signals)
        # Boost for critical signals
        critical_count = sum(
            1 for s in context.signals if s.category.value == "CRITICAL"
        )
        boost = min(critical_count * 0.15, 0.3)
        return min(avg + boost, 1.0)

    @staticmethod
    def _medium_term(
        context: CognitiveContext,
        memory: Optional[MemoryContext] = None,
    ) -> float:
        """7-day horizon — considers patterns and change velocity."""
        base = context.avg_priority * 0.6

        # Boost if many changes detected
        change_boost = min(context.changes_detected * 0.05, 0.2)

        # Precedent from decision history
        precedent_factor = 0.0
        if memory and memory.related_decisions:
            # Check past failure rate
            total = len(memory.related_decisions)
            failures = sum(
                1 for d in memory.related_decisions
                if d.get("status") in ("failed", "rejected")
            )
            if total > 0:
                precedent_factor = failures / total * 0.2

        return min(base + change_boost + precedent_factor, 1.0)

    @staticmethod
    def _long_term(
        context: CognitiveContext,
        memory: Optional[MemoryContext] = None,
    ) -> float:
        """30+ day horizon — considers systemic patterns."""
        base = 0.1

        # System health influence
        if context.system_health == "unhealthy":
            base = 0.5
        elif context.system_health == "degraded":
            base = 0.3

        # Unresolved errors amplify long-term risk
        if memory and memory.related_errors:
            unresolved = sum(
                1 for e in memory.related_errors if not e.get("resolved")
            )
            base += min(unresolved * 0.1, 0.3)

        return min(base, 1.0)
