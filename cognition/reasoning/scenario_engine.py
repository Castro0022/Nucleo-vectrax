"""
Scenario Engine — generates alternative outcome scenarios.

For each proposed action, produces N scenarios with estimated
probability, impact, and risk.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from cognition.types import CognitiveContext, ImpactAssessment, MemoryContext, Scenario

logger = logging.getLogger("vectrax.cognition.reasoning.scenarios")

DEFAULT_SCENARIOS = 3


class ScenarioEngine:
    """Generates alternative scenarios for strategic evaluation."""

    def generate(
        self,
        context: CognitiveContext,
        impact: ImpactAssessment,
        risk_score: float,
        memory: Optional[MemoryContext] = None,
        n: int = DEFAULT_SCENARIOS,
    ) -> List[Scenario]:
        """
        Generate *n* scenarios based on current context and analysis.

        Always produces at least:
          - Best case (optimistic)
          - Expected case (most likely)
          - Worst case (pessimistic)
        """
        scenarios: List[Scenario] = []

        # --- Best case ---
        best_prob = max(0.1, 1.0 - risk_score)
        scenarios.append(Scenario(
            description="Best case: action succeeds with minimal side effects",
            probability=round(best_prob * 0.3, 4),
            impact=round(impact.short_term * 0.5, 4),
            risk=round(risk_score * 0.3, 4),
        ))

        # --- Expected case ---
        expected_prob = max(0.2, 1.0 - risk_score * 0.5)
        scenarios.append(Scenario(
            description="Expected case: action succeeds with moderate adjustments needed",
            probability=round(expected_prob * 0.5, 4),
            impact=round(impact.overall, 4),
            risk=round(risk_score * 0.7, 4),
        ))

        # --- Worst case ---
        worst_prob = min(risk_score, 0.8)
        scenarios.append(Scenario(
            description="Worst case: action fails or produces negative side effects",
            probability=round(worst_prob * 0.2, 4),
            impact=round(min(impact.short_term * 1.5, 1.0), 4),
            risk=round(min(risk_score * 1.3, 1.0), 4),
        ))

        # --- Additional scenarios from memory precedents ---
        if memory and memory.related_decisions and n > 3:
            for decision in memory.related_decisions[:n - 3]:
                status = decision.get("status", "unknown")
                scenarios.append(Scenario(
                    description=f"Precedent-based: similar past decision was '{status}'",
                    probability=round(0.15, 4),
                    impact=round(decision.get("risk_score", 0.3), 4),
                    risk=round(decision.get("risk_score", 0.3), 4),
                ))

        # Normalise probabilities to sum ≤ 1
        total_prob = sum(s.probability for s in scenarios)
        if total_prob > 1.0:
            for s in scenarios:
                s.probability = round(s.probability / total_prob, 4)

        return scenarios[:n]
