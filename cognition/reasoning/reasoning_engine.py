"""
Reasoning Engine — orchestrates the full strategic reasoning pipeline.

Pipeline:  risk → impact → scenarios → contradictions → constitution → result

This is the public API for Motor 2.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from cognition.types import (
    CognitiveContext,
    MemoryContext,
    ReasoningResult,
)
from cognition.reasoning.risk_evaluator import CognitiveRiskEvaluator
from cognition.reasoning.impact_analyzer import ImpactAnalyzer
from cognition.reasoning.scenario_engine import ScenarioEngine
from cognition.reasoning.contradiction_detector import ContradictionDetector
from cognition.reasoning.constitutional_check import ConstitutionalValidator

logger = logging.getLogger("vectrax.cognition.reasoning")


class ReasoningEngine:
    """
    Motor 2 — Strategic Reasoning.

    Usage::

        engine = ReasoningEngine()
        result = engine.reason(context, memory)

        if result.recommendation == "block":
            # Do not proceed
    """

    def __init__(self) -> None:
        self._risk = CognitiveRiskEvaluator()
        self._impact = ImpactAnalyzer()
        self._scenarios = ScenarioEngine()
        self._contradictions = ContradictionDetector()
        self._constitution = ConstitutionalValidator()

    def reason(
        self,
        context: CognitiveContext,
        memory: Optional[MemoryContext] = None,
        description: str = "",
    ) -> ReasoningResult:
        """
        Execute the full reasoning pipeline.

        Steps:
          1. Risk evaluation
          2. Impact analysis (short/medium/long)
          3. Scenario generation
          4. Contradiction detection
          5. Constitutional validation
          6. Recommendation derivation

        Returns ReasoningResult with recommendation:
          "proceed" — safe to generate proposal
          "review"  — creator should review before proceeding
          "block"   — automatically blocked (constitution / critical risk)
        """
        t0 = time.time()

        # 1. Risk
        risk_score, risk_level, risk_breakdown = self._risk.evaluate(context)

        # 2. Impact
        impact = self._impact.analyze(context, memory)

        # 3. Scenarios
        scenarios = self._scenarios.generate(
            context, impact, risk_score, memory,
        )

        # 4. Contradictions
        contradictions = self._contradictions.detect(description, context, memory)

        # 5. Constitutional check
        const_pass, const_reasons = self._constitution.validate(
            risk_score=risk_score,
        )

        # 6. Recommendation
        recommendation = self._derive_recommendation(
            risk_level=risk_level,
            risk_score=risk_score,
            constitutional_pass=const_pass,
            contradictions=contradictions,
            context=context,
        )

        elapsed = (time.time() - t0) * 1000

        details = (
            f"Reasoning completed in {elapsed:.1f}ms. "
            f"Risk: {risk_level} ({risk_score:.3f}), "
            f"Impact: {impact.overall:.3f}, "
            f"Scenarios: {len(scenarios)}, "
            f"Contradictions: {len(contradictions)}, "
            f"Constitution: {'PASS' if const_pass else 'FAIL'}"
        )

        result = ReasoningResult(
            risk_score=risk_score,
            risk_level=risk_level,
            impact=impact,
            scenarios=scenarios,
            contradictions=contradictions,
            constitutional_pass=const_pass,
            constitutional_reasons=const_reasons,
            recommendation=recommendation,
            details=details,
        )

        logger.info(
            "Reasoning: %s (risk=%s, impact=%.3f, rec=%s)",
            risk_level, risk_score, impact.overall, recommendation,
        )
        return result

    # ------------------------------------------------------------------
    # Recommendation logic
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_recommendation(
        risk_level: str,
        risk_score: float,
        constitutional_pass: bool,
        contradictions: list,
        context: CognitiveContext,
    ) -> str:
        """Derive final recommendation from all analysis results."""
        # Hard blocks
        if not constitutional_pass:
            return "block"
        if risk_level == "CRITICAL":
            return "block"

        # Soft review triggers
        if risk_level == "HIGH":
            return "review"
        if contradictions:
            return "review"
        if context.governor_mode in ("recover", "cautious"):
            return "review"

        # All clear
        if risk_level in ("LOW", "MEDIUM") and not contradictions:
            return "proceed"

        return "review"

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Status summary for CLI."""
        return {
            "components": [
                "risk_evaluator",
                "impact_analyzer",
                "scenario_engine",
                "contradiction_detector",
                "constitutional_validator",
            ],
            "ready": True,
        }
