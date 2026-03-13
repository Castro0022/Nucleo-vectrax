"""
Vectrax Interface Layer — Risk Evaluator
==========================================
Evaluates the risk level of a simulated action by delegating to the
core RiskEngine and Governor policy.

Returns a simplified LOW / MEDIUM / HIGH classification for use by
the HumanConfirmation module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from core.audit_ledger import record as ledger_record
from core.governor import get_current_policy
from core.risk_engine import (
    OperationContext,
    RiskAssessment,
    RiskLevel,
    get_risk_engine,
)

from interface_layer.intent_gate import IntentType, StructuredIntent
from interface_layer.simulation_sandbox import SimulationReport


# ---------------------------------------------------------------------------
# Mapping from IntentType to core risk-engine op_type
# ---------------------------------------------------------------------------

_INTENT_TO_OP: dict[IntentType, str] = {
    IntentType.QUERY:     "read_only",
    IntentType.EXECUTE:   "workflow",
    IntentType.CONFIGURE: "autopatch",
    IntentType.REPORT:    "read_only",
    IntentType.UNKNOWN:   "read_only",
}


# ---------------------------------------------------------------------------
# Simplified risk level (excludes CRITICAL — maps to HIGH for this layer)
# ---------------------------------------------------------------------------

class SimpleRiskLevel(str, Enum):
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


@dataclass
class RiskVerdict:
    """Human-friendly risk evaluation result."""
    level: SimpleRiskLevel
    score: float                       # ∈ [0, 1]
    governor_mode: str
    requires_confirmation: bool
    explanation: str
    core_assessment: Optional[RiskAssessment] = None


# ---------------------------------------------------------------------------
# RiskEvaluator
# ---------------------------------------------------------------------------

class RiskEvaluator:
    """
    Evaluates the risk of a SimulationReport.

    Usage::

        evaluator = RiskEvaluator()
        verdict = evaluator.evaluate(simulation_report)
        if verdict.requires_confirmation:
            # hand off to HumanConfirmation
            ...
    """

    def evaluate(self, report: SimulationReport) -> RiskVerdict:
        """
        Assess the risk of *report* using the core RiskEngine and
        current Governor policy.
        """
        intent = report.intent
        policy = get_current_policy()
        governor_mode = policy.get("mode", "observe")

        # Build an OperationContext for the core risk engine
        op_type = _INTENT_TO_OP.get(intent.intent_type, "read_only")
        ctx = OperationContext(
            op_type=op_type,
            governor_mode=governor_mode,
        )

        # Attempt core risk assessment
        core_assessment: Optional[RiskAssessment] = None
        try:
            engine = get_risk_engine()
            core_assessment = engine.assess(ctx)
            score = core_assessment.risk_score
            core_level = core_assessment.risk_level
        except Exception:
            # Fallback: derive score from diff count and intent type
            score = self._fallback_score(report)
            core_level = self._level_from_score(score)

        # Map core RiskLevel → SimpleRiskLevel (CRITICAL → HIGH)
        simple_level = self._simplify(core_level)

        requires_confirmation = simple_level == SimpleRiskLevel.HIGH

        explanation = (
            f"Risk {simple_level.value} (score={score:.2f}) under "
            f"governor mode '{governor_mode}'. "
            f"Diffs: {len(report.diffs)}."
        )

        verdict = RiskVerdict(
            level=simple_level,
            score=score,
            governor_mode=governor_mode,
            requires_confirmation=requires_confirmation,
            explanation=explanation,
            core_assessment=core_assessment,
        )
        self._log(verdict, report)
        return verdict

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _simplify(level: RiskLevel) -> SimpleRiskLevel:
        mapping = {
            RiskLevel.LOW:      SimpleRiskLevel.LOW,
            RiskLevel.MEDIUM:   SimpleRiskLevel.MEDIUM,
            RiskLevel.HIGH:     SimpleRiskLevel.HIGH,
            RiskLevel.CRITICAL: SimpleRiskLevel.HIGH,
        }
        return mapping.get(level, SimpleRiskLevel.HIGH)

    @staticmethod
    def _fallback_score(report: SimulationReport) -> float:
        """Heuristic score when the core risk engine is unavailable."""
        base = 0.1
        if report.has_changes:
            base += 0.2 * min(len(report.diffs), 5) / 5
        if report.intent.intent_type == IntentType.EXECUTE:
            base += 0.3
        elif report.intent.intent_type == IntentType.CONFIGURE:
            base += 0.2
        return min(base, 1.0)

    @staticmethod
    def _level_from_score(score: float) -> RiskLevel:
        if score < 0.30:
            return RiskLevel.LOW
        if score < 0.60:
            return RiskLevel.MEDIUM
        if score < 0.85:
            return RiskLevel.HIGH
        return RiskLevel.CRITICAL

    @staticmethod
    def _log(verdict: RiskVerdict, report: SimulationReport) -> None:
        ledger_record(
            action=f"risk_evaluator:{verdict.level.value}",
            actor="interface_layer",
            diff_hash=report.diff_hash,
            decision="requires_confirmation" if verdict.requires_confirmation else "auto_approved",
            reason=verdict.explanation,
            metadata={
                "score": round(verdict.score, 4),
                "governor_mode": verdict.governor_mode,
            },
        )
