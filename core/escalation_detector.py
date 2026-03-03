"""
Vectrax Escalation Detector
==============================
Context-aware detector that evaluates whether an operation should
escalate from AUTO to MISSION_STRICT autonomy mode.

Six escalation triggers:
  1. Critical/High risk score from RiskEngine
  2. Sacred core path modification
  3. Privileged role operations
  4. Irreversible actions
  5. Sensitive domain exposure
  6. Low confidence classification
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Default thresholds
# ---------------------------------------------------------------------------

DEFAULT_MIN_CONFIDENCE = 0.6


# ---------------------------------------------------------------------------
# Operation envelope — wraps all context for a single decision
# ---------------------------------------------------------------------------

@dataclass
class OperationEnvelope:
    """All context needed to evaluate escalation for one operation."""
    prompt: str = ""
    affected_paths: List[str] = field(default_factory=list)
    op_type: str = "llm_generate"
    role: str = "owner"
    irreversibility_flags: List[str] = field(default_factory=list)
    exposure_flags: List[str] = field(default_factory=list)
    confidence_score: float = 1.0
    risk_level: Optional[str] = None       # "LOW" / "MEDIUM" / "HIGH" / "CRITICAL"
    risk_score: Optional[float] = None
    governor_mode: Optional[str] = None
    task_type: str = "chat"
    classification_method: str = "rules"
    extra: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Escalation result
# ---------------------------------------------------------------------------

@dataclass
class EscalationResult:
    """Result of escalation evaluation."""
    should_escalate: bool
    triggers: List[str]
    severity: float                        # 0.0-1.0, higher = more severe

    def summary(self) -> str:
        if not self.should_escalate:
            return "AUTO — no escalation triggers"
        return f"MISSION_STRICT — {len(self.triggers)} trigger(s): {', '.join(self.triggers)}"


# ---------------------------------------------------------------------------
# Escalation detector
# ---------------------------------------------------------------------------

class EscalationDetector:
    """
    Evaluates an OperationEnvelope against 6 escalation triggers.
    Any single trigger is sufficient to escalate.
    """

    def __init__(self, min_confidence: Optional[float] = None):
        self._min_confidence = min_confidence or self._load_confidence_threshold()

    def evaluate(self, envelope: OperationEnvelope) -> EscalationResult:
        """Run all trigger checks and return an EscalationResult."""
        triggers: List[str] = []
        severity_acc: List[float] = []

        # --- Trigger 1: Critical / High risk score ---
        self._check_risk(envelope, triggers, severity_acc)

        # --- Trigger 2: Sacred core path modification ---
        self._check_sacred_paths(envelope, triggers, severity_acc)

        # --- Trigger 3: Privileged role operations ---
        self._check_privileged_role(envelope, triggers, severity_acc)

        # --- Trigger 4: Irreversible actions ---
        self._check_irreversibility(envelope, triggers, severity_acc)

        # --- Trigger 5: Sensitive domain exposure ---
        self._check_exposure(envelope, triggers, severity_acc)

        # --- Trigger 6: Low confidence classification ---
        self._check_confidence(envelope, triggers, severity_acc)

        severity = max(severity_acc) if severity_acc else 0.0

        return EscalationResult(
            should_escalate=len(triggers) > 0,
            triggers=triggers,
            severity=round(severity, 3),
        )

    # -- individual trigger checks ------------------------------------------

    def _check_risk(
        self,
        env: OperationEnvelope,
        triggers: List[str],
        severity: List[float],
    ) -> None:
        """Trigger 1: Critical risk or High risk outside act mode."""
        if env.risk_level == "CRITICAL":
            triggers.append("critical_risk")
            severity.append(1.0)
        elif env.risk_level == "HIGH" and env.governor_mode != "act":
            triggers.append("high_risk_non_act")
            severity.append(0.85)

    def _check_sacred_paths(
        self,
        env: OperationEnvelope,
        triggers: List[str],
        severity: List[float],
    ) -> None:
        """Trigger 2: Any affected path is in SACRED_CORE zone."""
        if not env.affected_paths:
            return
        try:
            from core.autonomy_policy import Zone, classify_path
            for path in env.affected_paths:
                if classify_path(path) == Zone.SACRED_CORE:
                    triggers.append(f"sacred_core:{path}")
                    severity.append(1.0)
                    return  # one is enough
        except Exception:
            pass

    def _check_privileged_role(
        self,
        env: OperationEnvelope,
        triggers: List[str],
        severity: List[float],
    ) -> None:
        """Trigger 3: Operation requires privileged permissions."""
        privileged_ops = {"autopatch", "workflow", "config_write", "core_admin"}
        if env.op_type in privileged_ops:
            triggers.append(f"privileged_op:{env.op_type}")
            severity.append(0.9)
            return

        try:
            from core.roles import Permission, has_permission
            # If the role lacks CORE_ADMIN but the op implies it, escalate
            requires_admin = env.op_type in ("autopatch", "core_admin")
            if requires_admin and not has_permission(env.role, Permission.CORE_ADMIN):
                triggers.append("insufficient_privilege")
                severity.append(0.9)
        except Exception:
            pass

    def _check_irreversibility(
        self,
        env: OperationEnvelope,
        triggers: List[str],
        severity: List[float],
    ) -> None:
        """Trigger 4: Irreversible action flags present."""
        irrev_flags = {"writes_files", "modifies_state", "external_call"}
        found = [f for f in env.irreversibility_flags if f in irrev_flags]
        if found:
            triggers.append(f"irreversible:{','.join(found)}")
            severity.append(0.8)

    def _check_exposure(
        self,
        env: OperationEnvelope,
        triggers: List[str],
        severity: List[float],
    ) -> None:
        """Trigger 5: Sensitive external domain exposure."""
        sensitive_flags = {"cloud_provider", "api_call"}
        found = [f for f in env.exposure_flags if f in sensitive_flags]
        if found:
            triggers.append(f"sensitive_exposure:{','.join(found)}")
            severity.append(0.75)

    def _check_confidence(
        self,
        env: OperationEnvelope,
        triggers: List[str],
        severity: List[float],
    ) -> None:
        """Trigger 6: Classification confidence below threshold."""
        if env.confidence_score < self._min_confidence:
            triggers.append(
                f"low_confidence:{env.confidence_score:.2f}<{self._min_confidence}"
            )
            severity.append(0.6)

    # -- threshold loading ---------------------------------------------------

    @staticmethod
    def _load_confidence_threshold() -> float:
        """Load from env > policy registry > default."""
        # Env override takes priority
        env_val = _env_float("VX_MIN_CONFIDENCE", 0.0)
        if env_val > 0:
            return env_val
        # Try policy registry
        try:
            from core.policy_registry import get_policy_registry
            active = get_policy_registry().get_active()
            if active:
                return active.params.get(
                    "confidence_threshold", DEFAULT_MIN_CONFIDENCE
                )
        except Exception:
            pass
        return DEFAULT_MIN_CONFIDENCE


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_global_detector: Optional[EscalationDetector] = None


def get_escalation_detector() -> EscalationDetector:
    global _global_detector
    if _global_detector is None:
        _global_detector = EscalationDetector()
    return _global_detector


def detect(envelope: OperationEnvelope) -> EscalationResult:
    """Shortcut: evaluate using the global detector."""
    return get_escalation_detector().evaluate(envelope)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default
