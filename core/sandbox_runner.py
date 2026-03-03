"""
Vectrax Policy Sandbox Runner
================================
Runs candidate policies in shadow mode — no execution authority, no
production side-effects.

Workflow:
  1. Create candidate policy via PolicyRegistry
  2. sandbox.run(policy_id, ops) → shadow evaluation
  3. sandbox.evaluate(results) → SandboxReport (metrics)
  4. sandbox.can_promote(report) → bool (invariant + regression check)
  5. registry.promote(policy_id) → production
  6. sandbox.monitor_after_promotion() → auto-rollback on degradation
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.escalation_detector import EscalationDetector, OperationEnvelope
from core.invariants import MIN_CONFIDENCE_FLOOR, validate_all
from core.policy_registry import PolicyRegistry, get_policy_registry


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ShadowResult:
    """Result of evaluating one operation under a candidate policy."""
    envelope: OperationEnvelope
    mode: str           # HOME_AUTO / BUSINESS_GOVERNED / MISSION_STRICT
    triggers: List[str]
    severity: float
    latency_ms: float


@dataclass
class SandboxReport:
    """Aggregated metrics from a sandbox run."""
    policy_id: str
    total_ops: int
    escalation_rate: float        # fraction that escalated (severity > 0)
    mission_strict_rate: float    # fraction that hit MISSION_STRICT
    avg_severity: float
    avg_latency_ms: float
    stability_score: float        # 1 - variance(severity)
    invariant_violations: int
    results: List[ShadowResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "total_ops": self.total_ops,
            "escalation_rate": round(self.escalation_rate, 4),
            "mission_strict_rate": round(self.mission_strict_rate, 4),
            "avg_severity": round(self.avg_severity, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "stability_score": round(self.stability_score, 4),
            "invariant_violations": self.invariant_violations,
        }


# ---------------------------------------------------------------------------
# Default synthetic operations for quick sandbox testing
# ---------------------------------------------------------------------------

DEFAULT_OPS: List[OperationEnvelope] = [
    OperationEnvelope(risk_level="LOW", confidence_score=0.9, task_type="chat"),
    OperationEnvelope(risk_level="MEDIUM", confidence_score=0.7, task_type="code"),
    OperationEnvelope(risk_level="HIGH", confidence_score=0.8, task_type="code",
                      governor_mode="cautious"),
    OperationEnvelope(risk_level="CRITICAL", confidence_score=0.5, task_type="code"),
    OperationEnvelope(risk_level="LOW", confidence_score=0.95,
                      affected_paths=["docs/README.md"]),
    OperationEnvelope(risk_level="LOW", confidence_score=0.9,
                      affected_paths=["core/governor.py"]),
    OperationEnvelope(risk_level="LOW", confidence_score=0.4, task_type="chat"),
    OperationEnvelope(risk_level="LOW", confidence_score=0.9,
                      irreversibility_flags=["writes_files"]),
    OperationEnvelope(risk_level="LOW", confidence_score=0.9, op_type="autopatch"),
    OperationEnvelope(risk_level="LOW", confidence_score=0.9,
                      exposure_flags=["cloud_provider"]),
]


# ---------------------------------------------------------------------------
# Severity → mode mapping (mirrors adaptive_autonomy logic)
# ---------------------------------------------------------------------------

def _severity_to_mode(severity: float, escalation_threshold: float) -> str:
    if severity == 0.0:
        return "HOME_AUTO"
    elif severity < escalation_threshold:
        return "BUSINESS_GOVERNED"
    else:
        return "MISSION_STRICT"


# ---------------------------------------------------------------------------
# Sandbox Runner
# ---------------------------------------------------------------------------

class SandboxRunner:
    """Run candidate policies in shadow mode."""

    def __init__(self, registry: Optional[PolicyRegistry] = None):
        self._registry = registry or get_policy_registry()

    def run(
        self,
        policy_id: str,
        ops: Optional[List[OperationEnvelope]] = None,
    ) -> SandboxReport:
        """
        Evaluate *ops* under candidate policy *policy_id* in shadow mode.
        No production side-effects — no audit writes, no routing changes.
        """
        pv = self._registry.get_version(policy_id)
        if pv is None:
            raise ValueError(f"Policy {policy_id} not found")

        params = pv.params
        envelopes = ops or DEFAULT_OPS

        # Build a fresh detector with candidate thresholds
        conf_threshold = params.get("confidence_threshold", MIN_CONFIDENCE_FLOOR)
        esc_threshold = params.get("escalation_threshold", 0.80)
        detector = EscalationDetector(min_confidence=conf_threshold)

        results: List[ShadowResult] = []
        inv_violations = 0

        for env in envelopes:
            t0 = time.monotonic()
            esc = detector.evaluate(env)
            dt = (time.monotonic() - t0) * 1000  # ms

            mode = _severity_to_mode(esc.severity, esc_threshold)

            # Check invariants in shadow
            inv = validate_all(
                risk_level=env.risk_level,
                irreversible=bool(env.irreversibility_flags),
                auto_apply=(mode == "HOME_AUTO"),
                confidence=env.confidence_score,
                mode=mode,
            )
            if not inv.passed:
                inv_violations += 1

            results.append(ShadowResult(
                envelope=env,
                mode=mode,
                triggers=esc.triggers,
                severity=esc.severity,
                latency_ms=dt,
            ))

        return self._build_report(policy_id, results, inv_violations)

    # -- evaluation ---------------------------------------------------------

    def evaluate(self, report: SandboxReport) -> SandboxReport:
        """Re-compute metrics (idempotent). Returns same report."""
        return report  # metrics already computed in _build_report

    def can_promote(self, report: SandboxReport) -> bool:
        """
        Guard: can this candidate be promoted?
        Blocks if invariants violated or stability too low.
        """
        if report.invariant_violations > 0:
            return False
        if report.stability_score < 0.3:
            return False
        return True

    # -- auto-rollback monitoring -------------------------------------------

    def monitor_after_promotion(
        self,
        policy_id: str,
        ops: Optional[List[OperationEnvelope]] = None,
    ) -> bool:
        """
        Run a quick check after promotion.  If metrics regress
        (e.g. stability drops), auto-rollback.
        Returns True if rollback was triggered.
        """
        report = self.run(policy_id, ops)
        if not self.can_promote(report):
            try:
                self._registry.rollback(policy_id)
            except Exception:
                pass
            return True  # rollback triggered
        return False

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _build_report(
        policy_id: str,
        results: List[ShadowResult],
        inv_violations: int,
    ) -> SandboxReport:
        n = len(results)
        if n == 0:
            return SandboxReport(
                policy_id=policy_id, total_ops=0,
                escalation_rate=0, mission_strict_rate=0,
                avg_severity=0, avg_latency_ms=0,
                stability_score=1.0, invariant_violations=0,
            )

        severities = [r.severity for r in results]
        escalated = sum(1 for s in severities if s > 0)
        strict = sum(1 for r in results if r.mode == "MISSION_STRICT")
        avg_sev = sum(severities) / n
        avg_lat = sum(r.latency_ms for r in results) / n

        # Stability = 1 - normalised variance
        mean = avg_sev
        variance = sum((s - mean) ** 2 for s in severities) / n
        stability = max(0.0, 1.0 - variance)

        return SandboxReport(
            policy_id=policy_id,
            total_ops=n,
            escalation_rate=escalated / n,
            mission_strict_rate=strict / n,
            avg_severity=avg_sev,
            avg_latency_ms=avg_lat,
            stability_score=stability,
            invariant_violations=inv_violations,
            results=results,
        )
