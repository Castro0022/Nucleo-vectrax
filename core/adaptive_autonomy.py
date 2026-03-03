"""
Vectrax Adaptive Autonomy Policy
===================================
Automatically selects between AUTO and MISSION_STRICT modes based on
context and risk — no manual flag required.

AUTO mode (default for low-risk):
  - Automatic routing and fallback allowed
  - Provider switching allowed
  - Heuristic classification allowed
  - No user confirmation required for routing

MISSION_STRICT mode (auto-escalated on any trigger):
  - Fail closed
  - Non-deterministic fallback disabled
  - Explicit confirmation required for irreversible actions
  - Critical audit event logged
  - Only verified (local) providers used
  - LLM classification fallback disabled
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from core.escalation_detector import (
    EscalationDetector,
    EscalationResult,
    OperationEnvelope,
    get_escalation_detector,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Autonomy mode
# ---------------------------------------------------------------------------

class AutonomyMode(str, Enum):
    HOME_AUTO = "HOME_AUTO"
    BUSINESS_GOVERNED = "BUSINESS_GOVERNED"
    MISSION_STRICT = "MISSION_STRICT"

    # Backward compat alias
    @classmethod
    def _missing_(cls, value):
        if value == "AUTO":
            return cls.HOME_AUTO
        return None

# Aliases for backward compatibility
AutonomyMode.AUTO = AutonomyMode.HOME_AUTO


# ---------------------------------------------------------------------------
# Constraints per mode
# ---------------------------------------------------------------------------

HOME_AUTO_CONSTRAINTS: Dict[str, Any] = {
    "fallback_allowed": True,
    "provider_switching": True,
    "llm_classifier": True,
    "confirmation_required": False,
    "verified_providers_only": False,
    "local_only": False,
    "audit_level": "standard",
}

# Backward compat alias
AUTO_CONSTRAINTS = HOME_AUTO_CONSTRAINTS

BUSINESS_GOVERNED_CONSTRAINTS: Dict[str, Any] = {
    "fallback_allowed": True,
    "provider_switching": True,
    "llm_classifier": True,
    "confirmation_required": False,  # only for sacred_core / irreversible
    "verified_providers_only": False,
    "local_only": False,
    "audit_level": "enhanced",
}

MISSION_STRICT_CONSTRAINTS: Dict[str, Any] = {
    "fallback_allowed": False,
    "provider_switching": False,
    "llm_classifier": False,
    "confirmation_required": True,
    "verified_providers_only": True,
    "local_only": True,
    "audit_level": "critical",
}


# ---------------------------------------------------------------------------
# Policy decision
# ---------------------------------------------------------------------------

@dataclass
class PolicyDecision:
    """Result of adaptive autonomy evaluation."""
    mode: AutonomyMode
    constraints: Dict[str, Any]
    triggers: List[str]
    severity: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode.value,
            "constraints": self.constraints,
            "triggers": self.triggers,
            "severity": self.severity,
        }


# ---------------------------------------------------------------------------
# Adaptive Autonomy Policy
# ---------------------------------------------------------------------------

class AdaptiveAutonomyPolicy:
    """
    Global policy that derives autonomy mode from context.

    Usage::

        policy = AdaptiveAutonomyPolicy()
        envelope = OperationEnvelope(risk_level="CRITICAL")
        decision = policy.evaluate(envelope)
        assert decision.mode == AutonomyMode.MISSION_STRICT
    """

    def __init__(self, detector: Optional[EscalationDetector] = None):
        self._detector = detector or get_escalation_detector()
        self._escalation_threshold = self._load_escalation_threshold()

    @staticmethod
    def _load_escalation_threshold() -> float:
        """Load escalation threshold from active policy (fallback 0.80)."""
        try:
            from core.policy_registry import get_policy_registry
            active = get_policy_registry().get_active()
            if active:
                return active.params.get("escalation_threshold", 0.80)
        except Exception:
            pass
        return 0.80

    def evaluate(self, envelope: OperationEnvelope) -> PolicyDecision:
        """
        Evaluate the envelope and return HOME_AUTO, BUSINESS_GOVERNED,
        or MISSION_STRICT.

        The mode is derived entirely from context:
          - severity == 0      → HOME_AUTO
          - severity < threshold → BUSINESS_GOVERNED
          - severity >= threshold → MISSION_STRICT
        """
        result: EscalationResult = self._detector.evaluate(envelope)

        mode, constraints = self._map_mode(result)

        if mode == AutonomyMode.MISSION_STRICT:
            self._log_escalation(envelope, result)

        decision = PolicyDecision(
            mode=mode,
            constraints=constraints,
            triggers=result.triggers,
            severity=result.severity,
        )

        # Metrics logging (best-effort)
        self._record_metrics(decision)

        return decision

    def _map_mode(self, result: EscalationResult):
        """Map escalation severity to mode + constraints."""
        if result.severity == 0.0:
            return AutonomyMode.HOME_AUTO, dict(HOME_AUTO_CONSTRAINTS)
        elif result.severity < self._escalation_threshold:
            return AutonomyMode.BUSINESS_GOVERNED, dict(BUSINESS_GOVERNED_CONSTRAINTS)
        else:
            return AutonomyMode.MISSION_STRICT, dict(MISSION_STRICT_CONSTRAINTS)

    # -- audit logging for escalations --------------------------------------

    def _log_escalation(
        self,
        envelope: OperationEnvelope,
        result: EscalationResult,
    ) -> None:
        """Log a critical audit event when escalating to MISSION_STRICT."""
        try:
            from core.audit_ledger import record

            record(
                action="autonomy_escalation",
                actor="adaptive_autonomy",
                decision="escalated",
                reason=result.summary(),
                metadata={
                    "mode": AutonomyMode.MISSION_STRICT.value,
                    "triggers": result.triggers,
                    "severity": result.severity,
                    "task_type": envelope.task_type,
                    "op_type": envelope.op_type,
                    "risk_level": envelope.risk_level,
                    "confidence": envelope.confidence_score,
                    "governor_mode": envelope.governor_mode,
                    "affected_paths": envelope.affected_paths[:5],
                },
            )
        except Exception as e:
            logger.debug(f"Escalation audit log failed (non-blocking): {e}")

    # -- metrics (best-effort) ----------------------------------------------

    def _record_metrics(self, decision: PolicyDecision) -> None:
        """Record policy decision to observability metrics."""
        try:
            from core.observability.metrics import get_metrics_collector

            mc = get_metrics_collector()
            ctr = mc.get_counter("autonomy_decisions_total")
            if ctr:
                ctr.inc()
            if decision.mode == AutonomyMode.MISSION_STRICT:
                strict_ctr = mc.get_counter("autonomy_escalations_total")
                if strict_ctr:
                    strict_ctr.inc()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_global_policy: Optional[AdaptiveAutonomyPolicy] = None


def get_adaptive_policy() -> AdaptiveAutonomyPolicy:
    """Return the global AdaptiveAutonomyPolicy singleton."""
    global _global_policy
    if _global_policy is None:
        _global_policy = AdaptiveAutonomyPolicy()
    return _global_policy


def evaluate(envelope: OperationEnvelope) -> PolicyDecision:
    """Convenience: evaluate using the global policy."""
    return get_adaptive_policy().evaluate(envelope)
