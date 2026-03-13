"""
Cognitive Risk Evaluator — wraps core.risk_engine.RiskEngine.

Does NOT duplicate risk logic; delegates to the existing probabilistic
engine and adds cognitive-context-level evaluation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from cognition.types import CognitiveContext

logger = logging.getLogger("vectrax.cognition.reasoning.risk")


class CognitiveRiskEvaluator:
    """
    Evaluates risk for a cognitive context or proposed action.

    Wraps ``core.risk_engine.RiskEngine`` — never duplicates its logic.
    """

    def __init__(self) -> None:
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            try:
                from core.risk_engine import RiskEngine
                self._engine = RiskEngine()
            except ImportError:
                logger.warning("RiskEngine not available")
        return self._engine

    def evaluate(
        self,
        context: CognitiveContext,
        op_type: str = "read_only",
        topic: str = "general",
    ) -> Tuple[float, str, Dict[str, Any]]:
        """
        Evaluate risk for a cognitive context.

        Returns (risk_score, risk_level, breakdown_dict).
        """
        engine = self._get_engine()
        if engine is None:
            return self._fallback_risk(context)

        try:
            from core.risk_engine import OperationContext
            ctx = OperationContext(
                op_type=op_type,
                topic=topic,
                governor_mode=context.governor_mode,
            )
            assessment = engine.assess(ctx)
            return (
                assessment.risk_score,
                assessment.risk_level.value,
                assessment.breakdown(),
            )
        except Exception as exc:
            logger.debug("Risk engine assessment failed: %s", exc)
            return self._fallback_risk(context)

    @staticmethod
    def _fallback_risk(context: CognitiveContext) -> Tuple[float, str, Dict[str, Any]]:
        """Heuristic fallback when RiskEngine is unavailable."""
        # Conservative estimate from context
        if context.system_health == "unhealthy":
            score = 0.8
        elif context.governor_mode == "recover":
            score = 0.7
        elif context.avg_priority > 0.7:
            score = 0.6
        elif context.governor_mode == "cautious":
            score = 0.4
        else:
            score = 0.2

        level = (
            "CRITICAL" if score >= 0.85 else
            "HIGH" if score >= 0.60 else
            "MEDIUM" if score >= 0.30 else
            "LOW"
        )
        return score, level, {"method": "fallback", "score": score}
