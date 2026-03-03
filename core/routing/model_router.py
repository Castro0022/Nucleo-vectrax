"""
Vectrax Intelligence Router (ModelRouter)
==========================================
Selects the best provider/model per task automatically.
Silent operation — user only sees the final answer.

Routing policy:
  1. Classify task from prompt
  2. Filter registry by capability match
  3. Score candidates: capability × 0.4 + cost × 0.2 + latency × 0.2 + priority × 0.2
  4. Consult Governor/RiskEngine
  5. Select primary + fallback
  6. Log decision to audit ledger (invisible unless VX_DEBUG=1)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .capabilities import (
    Capability,
    ModelProfile,
    get_available_models,
    models_with_capability,
    refresh_availability,
)
from .task_classifier import (
    TaskClassification,
    TaskType,
    TASK_CAPABILITY_MAP,
    classify_task,
)
from .circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------

W_CAPABILITY = 0.40
W_COST = 0.20
W_LATENCY = 0.20
W_PRIORITY = 0.20

# Normalisation bounds
MAX_COST = 0.01        # USD/1k tokens — anything above is penalised heavily
MAX_LATENCY = 5000.0   # ms — anything above gets score 0
MAX_PRIORITY = 100      # priority range 1..100


# ---------------------------------------------------------------------------
# Routing decision
# ---------------------------------------------------------------------------

@dataclass
class RoutingDecision:
    """Result of the ModelRouter's route() call."""
    primary: ModelProfile
    fallback: Optional[ModelProfile]
    task_classification: TaskClassification
    reason: str
    score: float
    risk_check_passed: bool = True
    governor_mode: Optional[str] = None
    autonomy_mode: Optional[str] = None           # "AUTO" or "MISSION_STRICT"
    autonomy_constraints: Optional[Dict] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        """Audit-friendly dict (for ledger metadata)."""
        d = {
            "primary_provider": self.primary.provider,
            "primary_model": self.primary.model,
            "task_type": self.task_classification.task_type.value,
            "task_confidence": self.task_classification.confidence,
            "reason": self.reason,
            "score": round(self.score, 4),
            "risk_check_passed": self.risk_check_passed,
            "governor_mode": self.governor_mode,
            "autonomy_mode": self.autonomy_mode,
        }
        if self.fallback:
            d["fallback_provider"] = self.fallback.provider
            d["fallback_model"] = self.fallback.model
        return d


# ---------------------------------------------------------------------------
# ModelRouter
# ---------------------------------------------------------------------------

class ModelRouter:
    """
    Intelligence Router — selects best provider/model silently.

    Usage::

        router = ModelRouter()
        decision = router.route("Write a Python function to sort a list")
        print(decision.primary.provider, decision.primary.model)
    """

    def __init__(
        self,
        circuit_breaker: Optional[CircuitBreaker] = None,
        daily_budget_cap: Optional[int] = None,
    ):
        self._cb = circuit_breaker or CircuitBreaker()
        self._daily_budget_cap = daily_budget_cap or _env_int("VX_DAILY_BUDGET_CAP", 0)

    # -- main entry ---------------------------------------------------------

    def route(
        self,
        prompt: str,
        context: Optional[str] = None,
    ) -> RoutingDecision:
        """
        Route a prompt to the best provider/model.

        This is the single entry point. It:
          1. Classifies the task
          2. Scores candidate models
          3. Checks risk/governor constraints
          4. Logs to audit ledger
          5. Returns the decision

        Args:
            prompt: User's prompt text.
            context: Optional additional context.

        Returns:
            RoutingDecision with primary and optional fallback.
        """
        # Refresh cloud model availability
        refresh_availability()

        # Step 1: Classify
        classification = classify_task(prompt, context)
        required_cap = TASK_CAPABILITY_MAP.get(
            classification.task_type, Capability.REASONING
        )

        # Step 2: Adaptive autonomy evaluation
        governor_mode = self._get_governor_mode()
        policy_decision = self._evaluate_autonomy(
            classification, governor_mode
        )
        is_strict = (
            policy_decision is not None
            and policy_decision.mode.value == "MISSION_STRICT"
        )
        is_governed = (
            policy_decision is not None
            and policy_decision.mode.value == "BUSINESS_GOVERNED"
        )

        # Step 3: Get candidates (strict → local only)
        local_only = governor_mode == "recover" or (
            is_strict and policy_decision.constraints.get("local_only", False)
        )
        candidates = self._get_candidates(required_cap, local_only)

        # If no candidates with exact capability, broaden to all available
        if not candidates:
            candidates = get_available_models(local_only=local_only)

        # Still nothing — hard fallback to any local model
        if not candidates:
            candidates = get_available_models(local_only=True)

        if not candidates:
            raise RuntimeError("No models available for routing")

        # Step 4: Score and rank
        scored = self._score_candidates(
            candidates, required_cap, classification
        )

        # Step 5: Select primary and fallback
        primary = scored[0]
        fallback = scored[1] if len(scored) > 1 else None

        # In MISSION_STRICT: disable non-deterministic fallback
        if is_strict and not policy_decision.constraints.get(
            "fallback_allowed", True
        ):
            fallback = None

        # Step 6: Risk check
        risk_ok, risk_reason = self._check_risk(classification, governor_mode)

        reason_parts = [
            f"task={classification.task_type.value}",
            f"cap={required_cap.value}",
            f"score={primary[1]:.3f}",
        ]
        if local_only:
            reason_parts.append("local_only(recover)")
        if not risk_ok:
            reason_parts.append(f"RISK_BLOCKED:{risk_reason}")

        autonomy_mode = None
        autonomy_constraints = None
        if policy_decision is not None:
            autonomy_mode = policy_decision.mode.value
            autonomy_constraints = policy_decision.constraints
            if is_strict:
                reason_parts.append(
                    f"MISSION_STRICT({len(policy_decision.triggers)} triggers)"
                )
            elif is_governed:
                reason_parts.append("BUSINESS_GOVERNED")

        decision = RoutingDecision(
            primary=primary[0],
            fallback=fallback[0] if fallback else None,
            task_classification=classification,
            reason=" | ".join(reason_parts),
            score=primary[1],
            risk_check_passed=risk_ok,
            governor_mode=governor_mode,
            autonomy_mode=autonomy_mode,
            autonomy_constraints=autonomy_constraints,
        )

        # Step 6: Audit log
        self._audit_log(decision)

        # Debug output
        if os.environ.get("VX_DEBUG") == "1":
            logger.info(f"[ModelRouter] {decision.reason}")

        return decision

    # -- candidate selection ------------------------------------------------

    def _get_candidates(
        self, cap: Capability, local_only: bool
    ) -> Dict[str, ModelProfile]:
        """Get candidate models filtered by capability and circuit breaker."""
        raw = models_with_capability(cap, local_only=local_only)

        # Filter out providers with open circuits
        filtered = {}
        for key, profile in raw.items():
            if self._cb.can_execute(profile.provider):
                filtered[key] = profile
            else:
                logger.debug(
                    f"Skipping {key} — circuit breaker open for {profile.provider}"
                )
        return filtered

    # -- scoring ------------------------------------------------------------

    def _score_candidates(
        self,
        candidates: Dict[str, ModelProfile],
        required_cap: Capability,
        classification: TaskClassification,
    ) -> List[tuple]:
        """
        Score each candidate model and return sorted list of (profile, score).
        """
        scored = []

        for key, profile in candidates.items():
            # Capability match: 1.0 if has required cap, 0.3 otherwise
            cap_score = 1.0 if required_cap in profile.capabilities else 0.3
            # Bonus for having multiple relevant capabilities
            cap_bonus = min(0.2, len(profile.capabilities) * 0.03)
            cap_score = min(1.0, cap_score + cap_bonus)

            # Cost score: lower is better (inverted normalised)
            if profile.cost_per_1k_tokens <= 0:
                cost_score = 1.0  # free (local)
            else:
                cost_score = max(0.0, 1.0 - profile.cost_per_1k_tokens / MAX_COST)

            # Latency score: lower is better
            latency_score = max(
                0.0, 1.0 - profile.avg_latency_ms / MAX_LATENCY
            )

            # Priority score: lower priority number = higher score
            priority_score = max(
                0.0, 1.0 - profile.priority / MAX_PRIORITY
            )

            total = (
                cap_score * W_CAPABILITY
                + cost_score * W_COST
                + latency_score * W_LATENCY
                + priority_score * W_PRIORITY
            )

            scored.append((profile, round(total, 4)))

        # Sort by score descending, then by priority ascending for tiebreak
        scored.sort(key=lambda x: (-x[1], x[0].priority))
        return scored

    # -- adaptive autonomy hook ---------------------------------------------

    def _evaluate_autonomy(self, classification, governor_mode):
        """Consult AdaptiveAutonomyPolicy. Returns PolicyDecision or None."""
        try:
            from core.adaptive_autonomy import get_adaptive_policy
            from core.escalation_detector import OperationEnvelope

            envelope = OperationEnvelope(
                prompt="",  # no need to pass full prompt to policy
                confidence_score=classification.confidence,
                task_type=classification.task_type.value,
                classification_method=classification.method,
                governor_mode=governor_mode,
            )
            return get_adaptive_policy().evaluate(envelope)
        except Exception as e:
            logger.debug(f"Autonomy evaluation failed (non-blocking): {e}")
            return None

    # -- risk/governor integration ------------------------------------------

    def _get_governor_mode(self) -> str:
        """Read current governor mode from state (best-effort)."""
        try:
            from core import state_manager
            return state_manager.get("governor_mode", "observe")
        except Exception:
            return "observe"

    def _check_risk(
        self,
        classification: TaskClassification,
        governor_mode: str,
    ) -> tuple:
        """
        Consult RiskEngine. Returns (passed: bool, reason: str).
        Routing is blocked only when risk is CRITICAL and mode != act.
        """
        try:
            from core.risk_engine import (
                OperationContext,
                RiskLevel,
                get_risk_engine,
            )

            ctx = OperationContext(
                op_type="llm_generate",
                topic=classification.task_type.value,
                governor_mode=governor_mode,
            )
            assessment = get_risk_engine().assess(ctx)

            if (
                assessment.risk_level == RiskLevel.CRITICAL
                and governor_mode != "act"
            ):
                return False, f"CRITICAL risk ({assessment.risk_score:.2f})"

            return True, ""
        except Exception as e:
            logger.debug(f"Risk check failed (non-blocking): {e}")
            return True, ""  # fail-open: don't block routing on risk engine errors

    # -- audit logging ------------------------------------------------------

    def _audit_log(self, decision: RoutingDecision) -> None:
        """Record routing decision to the audit ledger (always)."""
        try:
            from core.audit_ledger import record

            record(
                action="model_route",
                actor="model_router",
                decision="approved" if decision.risk_check_passed else "blocked",
                reason=decision.reason,
                metadata=decision.to_dict(),
            )
        except Exception as e:
            logger.debug(f"Audit log failed (non-blocking): {e}")

    # -- health / status ----------------------------------------------------

    def get_status(self) -> Dict:
        """Return router health status for CLI display."""
        refresh_availability()
        available = get_available_models()
        local = get_available_models(local_only=True)

        cb_stats = self._cb.get_stats()

        return {
            "total_models": len(available),
            "local_models": len(local),
            "cloud_models": len(available) - len(local),
            "circuit_breakers": cb_stats,
            "governor_mode": self._get_governor_mode(),
            "daily_budget_cap": self._daily_budget_cap,
            "models": {
                k: {
                    "provider": v.provider,
                    "model": v.model,
                    "capabilities": sorted(c.value for c in v.capabilities),
                    "is_local": v.is_local,
                    "priority": v.priority,
                }
                for k, v in available.items()
            },
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_global_router: Optional[ModelRouter] = None


def get_model_router() -> ModelRouter:
    """Return the global ModelRouter singleton."""
    global _global_router
    if _global_router is None:
        _global_router = ModelRouter()
    return _global_router


def route(prompt: str, context: Optional[str] = None) -> RoutingDecision:
    """Convenience: route using the global router."""
    return get_model_router().route(prompt, context)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default
