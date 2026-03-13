"""
Vectrax Policy Router
=======================
Sits between the pipeline output and any autonomous action.

Receives pipeline scores + derived signals and returns a routing decision:
  AUTO_EXECUTE       — all thresholds met, safe to proceed automatically.
  REQUIERE_REVISION  — conservative default, human review needed.
  BLOCKED            — hard safety rule violated, action forbidden.

Autonomy Thresholds (initial):
  risk_score         <= 0.02
  precedent_confidence >= 0.75
  polarity_state     == ATRACCION
  tension            <= 0.35

Safety (hard rules):
  - NEVER modify Kybalion core operator weights automatically.
  - NEVER touch secrets/credentials.
  - NEVER delete user data.
  - Only whitelisted action types.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from core.autonomy_policy import Zone, classify_path, is_hard_limited
from core.causal_ledger import query_precedent


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RoutingAction(str, Enum):
    AUTO_EXECUTE = "AUTO_EXECUTE"
    REQUIERE_REVISION = "REQUIERE_REVISION"
    BLOCKED = "BLOCKED"


class PolarityState(str, Enum):
    ATRACCION = "ATRACCION"
    REPULSION = "REPULSION"
    NEUTRO = "NEUTRO"


# ---------------------------------------------------------------------------
# Whitelisted action types
# ---------------------------------------------------------------------------

WHITELISTED_ACTIONS = frozenset({
    "create_module",
    "add_tests",
    "refactor_small",
    "docs",
})


# ---------------------------------------------------------------------------
# Default thresholds
# ---------------------------------------------------------------------------

DEFAULT_ROUTER_THRESHOLDS = {
    "max_risk_score": 0.02,
    "min_precedent_confidence": 0.75,
    "max_tension": 0.35,
    "required_polarity": PolarityState.ATRACCION.value,
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RoutingInput:
    """All signals needed for a routing decision."""
    global_score: float
    risk_score: float
    polarity_state: str          # ATRACCION | REPULSION | NEUTRO
    tension: float
    precedent_confidence: float
    action_type: str
    affected_paths: List[str] = field(default_factory=list)


@dataclass
class RoutingDecision:
    """Result of the policy routing evaluation."""
    action: RoutingAction
    reason: str
    routing_input: RoutingInput
    checks: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "checks": self.checks,
            "action_type": self.routing_input.action_type,
            "risk_score": self.routing_input.risk_score,
            "polarity_state": self.routing_input.polarity_state,
            "tension": self.routing_input.tension,
            "precedent_confidence": self.routing_input.precedent_confidence,
        }


# ---------------------------------------------------------------------------
# Signal derivation helpers
# ---------------------------------------------------------------------------

def derive_polarity(
    converged: bool,
    convergence_score: float,
    expected_impact: float,
) -> PolarityState:
    """
    Derive polarity_state from pipeline outputs.

    ATRACCION  — converged AND positive impact AND convergence_score > 0.6
    REPULSION  — negative impact OR not converged
    NEUTRO     — everything else
    """
    if converged and expected_impact > 0.0 and convergence_score > 0.6:
        return PolarityState.ATRACCION
    if expected_impact < 0.0 or not converged:
        return PolarityState.REPULSION
    return PolarityState.NEUTRO


def derive_tension(dimension_scores: List[float]) -> float:
    """
    Derive tension from structural analysis dimension scores.

    Tension = normalised standard deviation of dimension scores.
    Range: [0, 1]. High variance = high tension.
    """
    if len(dimension_scores) < 2:
        return 0.0

    stdev = statistics.stdev(dimension_scores)
    # Normalise: stdev of [0,1] values has theoretical max ~0.5
    return min(stdev / 0.5, 1.0)


# ---------------------------------------------------------------------------
# Policy Router
# ---------------------------------------------------------------------------

class PolicyRouter:
    """
    Routes pipeline decisions through safety and autonomy checks.

    Usage::

        router = PolicyRouter()
        ri = RoutingInput(
            global_score=0.85,
            risk_score=0.01,
            polarity_state="ATRACCION",
            tension=0.10,
            precedent_confidence=0.90,
            action_type="create_module",
            affected_paths=["core/daily_report.py"],
        )
        decision = router.evaluate(ri)
    """

    def __init__(self, thresholds: Optional[Dict[str, Any]] = None):
        self.thresholds = thresholds or dict(DEFAULT_ROUTER_THRESHOLDS)

    def evaluate(self, ri: RoutingInput) -> RoutingDecision:
        """
        Evaluate a RoutingInput and return a RoutingDecision.

        Order of checks (first failure wins):
          1. Action type whitelist
          2. Hard-limited paths (secrets, credentials, vault)
          3. SACRED_CORE zone paths
          4. Polarity check
          5. Tension check
          6. Risk score check
          7. Precedent confidence check
          8. Governor mode check (best-effort)
        """
        checks: Dict[str, Any] = {}

        # --- 1. Action type whitelist ---
        if ri.action_type not in WHITELISTED_ACTIONS:
            checks["action_whitelist"] = False
            return RoutingDecision(
                action=RoutingAction.BLOCKED,
                reason=f"BLOCKED: action_type '{ri.action_type}' no está en whitelist {sorted(WHITELISTED_ACTIONS)}",
                routing_input=ri,
                checks=checks,
            )
        checks["action_whitelist"] = True

        # --- 2. Hard-limited paths ---
        hard_limited = [p for p in ri.affected_paths if is_hard_limited(p)]
        if hard_limited:
            checks["hard_limits"] = hard_limited
            return RoutingDecision(
                action=RoutingAction.BLOCKED,
                reason=f"BLOCKED: archivos protegidos detectados: {hard_limited[:3]}",
                routing_input=ri,
                checks=checks,
            )
        checks["hard_limits"] = []

        # --- 3. SACRED_CORE zone ---
        sacred = [p for p in ri.affected_paths if classify_path(p) == Zone.SACRED_CORE]
        if sacred:
            checks["sacred_core"] = sacred
            return RoutingDecision(
                action=RoutingAction.BLOCKED,
                reason=f"BLOCKED: archivos en SACRED_CORE: {sacred[:3]}",
                routing_input=ri,
                checks=checks,
            )
        checks["sacred_core"] = []

        # --- 4. Polarity check ---
        required_polarity = self.thresholds.get("required_polarity", PolarityState.ATRACCION.value)
        polarity_ok = ri.polarity_state == required_polarity
        checks["polarity"] = {"value": ri.polarity_state, "required": required_polarity, "pass": polarity_ok}
        if not polarity_ok:
            return RoutingDecision(
                action=RoutingAction.REQUIERE_REVISION,
                reason=f"polarity_state={ri.polarity_state} != {required_polarity} — revisión requerida",
                routing_input=ri,
                checks=checks,
            )

        # --- 5. Tension check ---
        max_tension = self.thresholds.get("max_tension", 0.35)
        tension_ok = ri.tension <= max_tension
        checks["tension"] = {"value": ri.tension, "max": max_tension, "pass": tension_ok}
        if not tension_ok:
            return RoutingDecision(
                action=RoutingAction.REQUIERE_REVISION,
                reason=f"tension={ri.tension:.4f} > umbral {max_tension} — revisión requerida",
                routing_input=ri,
                checks=checks,
            )

        # --- 6. Risk score check ---
        max_risk = self.thresholds.get("max_risk_score", 0.02)
        risk_ok = ri.risk_score <= max_risk
        checks["risk_score"] = {"value": ri.risk_score, "max": max_risk, "pass": risk_ok}
        if not risk_ok:
            return RoutingDecision(
                action=RoutingAction.REQUIERE_REVISION,
                reason=f"risk_score={ri.risk_score:.4f} > umbral {max_risk} — revisión requerida",
                routing_input=ri,
                checks=checks,
            )

        # --- 7. Precedent confidence check ---
        min_prec = self.thresholds.get("min_precedent_confidence", 0.75)
        prec_ok = ri.precedent_confidence >= min_prec
        checks["precedent_confidence"] = {"value": ri.precedent_confidence, "min": min_prec, "pass": prec_ok}
        if not prec_ok:
            return RoutingDecision(
                action=RoutingAction.REQUIERE_REVISION,
                reason=f"precedent_confidence={ri.precedent_confidence:.4f} < umbral {min_prec} — revisión requerida",
                routing_input=ri,
                checks=checks,
            )

        # --- 8. Governor mode check (best-effort) ---
        try:
            from core.governor import get_current_policy
            gov = get_current_policy()
            gov_mode = gov.get("mode", "observe")
            checks["governor_mode"] = gov_mode
            if gov_mode == "recover":
                return RoutingDecision(
                    action=RoutingAction.REQUIERE_REVISION,
                    reason=f"Governor en modo '{gov_mode}' — no se permite auto-ejecución",
                    routing_input=ri,
                    checks=checks,
                )
        except Exception:
            checks["governor_mode"] = "unavailable"

        # --- All checks passed ---
        return RoutingDecision(
            action=RoutingAction.AUTO_EXECUTE,
            reason=(
                f"AUTO_EXECUTE: todos los umbrales cumplidos — "
                f"risk={ri.risk_score:.4f}, precedent={ri.precedent_confidence:.4f}, "
                f"polarity={ri.polarity_state}, tension={ri.tension:.4f}"
            ),
            routing_input=ri,
            checks=checks,
        )


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_global_router: Optional[PolicyRouter] = None


def get_policy_router() -> PolicyRouter:
    """Return the global singleton PolicyRouter."""
    global _global_router
    if _global_router is None:
        _global_router = PolicyRouter()
    return _global_router


def route(ri: RoutingInput) -> RoutingDecision:
    """Convenience: evaluate using the global router."""
    return get_policy_router().evaluate(ri)
