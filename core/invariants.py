"""
Vectrax Hard Invariants
=========================
Code-enforced invariants that can NEVER be overridden by config or policy.

1. MISSION_STRICT ⇒ verified_providers_only=true, fallback_allowed=false
2. HIGH/CRITICAL risk ⇒ never auto-apply irreversible actions
3. MIN_CONFIDENCE floor ≥ 0.60
4. SacredCore write ⇒ confirmation_required; autopatch blocked
5. validate_all() runs all checks for a given context
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Constants (hard-coded, not configurable)
# ---------------------------------------------------------------------------

MIN_CONFIDENCE_FLOOR = 0.60
IRREVERSIBLE_RISK_BLOCK = {"HIGH", "CRITICAL"}
AUTOPATCH_OP = "autopatch"

# Identity — INMUTABLE. Ley fundamental del sistema.
SYSTEM_NAME = "Vectrax"
CREATOR_NAME = "Mario Bravo Castro"


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class InvariantViolation:
    """A single invariant violation."""
    invariant: str
    message: str
    severity: str = "critical"  # "critical" or "warning"


@dataclass
class InvariantResult:
    """Result of running all invariant checks."""
    passed: bool
    violations: List[InvariantViolation] = field(default_factory=list)

    @property
    def confirmation_required(self) -> bool:
        return any(v.invariant == "sacred_core_write" for v in self.violations)

    @property
    def blocked(self) -> bool:
        return any(v.severity == "critical" for v in self.violations)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "blocked": self.blocked,
            "confirmation_required": self.confirmation_required,
            "violations": [
                {"invariant": v.invariant, "message": v.message, "severity": v.severity}
                for v in self.violations
            ],
        }


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_mission_strict(constraints: Dict[str, Any]) -> Optional[InvariantViolation]:
    """
    MISSION_STRICT mode MUST have verified_providers_only=true and
    fallback_allowed=false.  If constraints claim MISSION_STRICT but
    violate these, return a violation.
    """
    issues = []
    if constraints.get("verified_providers_only") is not True:
        issues.append("verified_providers_only must be true")
    if constraints.get("fallback_allowed") is not False:
        issues.append("fallback_allowed must be false")
    if issues:
        return InvariantViolation(
            invariant="mission_strict_constraints",
            message=f"MISSION_STRICT violated: {'; '.join(issues)}",
        )
    return None


def check_irreversible_risk(
    risk_level: Optional[str],
    irreversible: bool = False,
    auto_apply: bool = False,
) -> Optional[InvariantViolation]:
    """
    HIGH/CRITICAL risk operations MUST NOT auto-apply irreversible actions.
    """
    if risk_level in IRREVERSIBLE_RISK_BLOCK and irreversible and auto_apply:
        return InvariantViolation(
            invariant="irreversible_risk",
            message=f"Risk {risk_level}: cannot auto-apply irreversible action",
        )
    return None


def check_min_confidence(confidence: float) -> Optional[InvariantViolation]:
    """
    Classification confidence MUST be ≥ MIN_CONFIDENCE_FLOOR.
    Returns violation if a policy tries to set threshold below floor.
    """
    if confidence < MIN_CONFIDENCE_FLOOR:
        return InvariantViolation(
            invariant="min_confidence",
            message=(
                f"Confidence {confidence:.2f} below hard floor "
                f"{MIN_CONFIDENCE_FLOOR:.2f}"
            ),
        )
    return None


def check_identity_integrity() -> Optional[InvariantViolation]:
    """
    INVARIANTE DE IDENTIDAD — LEY INMUTABLE.

    Vectrax es Vectrax. Su creador es Mario Bravo Castro.
    Ningún módulo, config, policy, LLM, ni proceso externo puede
    alterar esto. Si la identidad fue corrompida, es una violación
    crítica que bloquea toda operación.
    """
    try:
        from core.operator.identity import IDENTITY
        issues = []
        if IDENTITY.name != "Vectrax Core":
            issues.append(f"name corrupted: '{IDENTITY.name}'")
        if IDENTITY.creator != CREATOR_NAME:
            issues.append(f"creator corrupted: '{IDENTITY.creator}'")
        if issues:
            return InvariantViolation(
                invariant="identity_integrity",
                message=(
                    f"IDENTITY VIOLATION: {'; '.join(issues)}. "
                    f"Vectrax is Vectrax. Creator is {CREATOR_NAME}. "
                    f"This is immutable law."
                ),
                severity="critical",
            )
    except Exception as exc:
        return InvariantViolation(
            invariant="identity_integrity",
            message=f"Cannot verify identity: {exc}",
            severity="critical",
        )
    return None


def check_sacred_core_write(
    path: str,
    op_type: str = "",
) -> Optional[InvariantViolation]:
    """
    Any write to SacredCore ⇒ confirmation_required.
    autopatch ⇒ blocked entirely for SacredCore.
    """
    try:
        from core.autonomy_policy import Zone, classify_path
        zone = classify_path(path)
    except Exception:
        return None

    if zone != Zone.SACRED_CORE:
        return None

    if op_type == AUTOPATCH_OP:
        return InvariantViolation(
            invariant="sacred_core_write",
            message=f"autopatch BLOCKED for SacredCore path: {path}",
            severity="critical",
        )

    return InvariantViolation(
        invariant="sacred_core_write",
        message=f"SacredCore write requires confirmation: {path}",
        severity="warning",
    )


# ---------------------------------------------------------------------------
# Aggregate validator
# ---------------------------------------------------------------------------

def validate_all(
    *,
    path: Optional[str] = None,
    op_type: str = "",
    risk_level: Optional[str] = None,
    irreversible: bool = False,
    auto_apply: bool = False,
    confidence: Optional[float] = None,
    mode: Optional[str] = None,
    constraints: Optional[Dict[str, Any]] = None,
) -> InvariantResult:
    """
    Run every invariant check against the given context.
    Returns InvariantResult with all violations collected.
    """
    violations: List[InvariantViolation] = []

    # 1. MISSION_STRICT constraints
    if mode == "MISSION_STRICT" and constraints is not None:
        v = check_mission_strict(constraints)
        if v:
            violations.append(v)

    # 2. Irreversible risk
    v = check_irreversible_risk(risk_level, irreversible, auto_apply)
    if v:
        violations.append(v)

    # 3. Min confidence
    if confidence is not None:
        v = check_min_confidence(confidence)
        if v:
            violations.append(v)

    # 4. Sacred core write
    if path is not None:
        v = check_sacred_core_write(path, op_type)
        if v:
            violations.append(v)

    # 5. Identity integrity — ALWAYS checked
    v = check_identity_integrity()
    if v:
        violations.append(v)

    return InvariantResult(
        passed=len(violations) == 0,
        violations=violations,
    )
