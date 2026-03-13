"""
Vectrax Probabilistic Risk Engine
===================================
Calculates risk_score ∈ [0,1] per operation using 6 traceable signals:

  1. Impact          — magnitude of the operation's effect on the system.
  2. Irreversibility — whether the operation can be undone.
  3. External exposure — interaction with external systems.
  4. Sensitivity     — data/topic sensitivity classification.
  5. Runtime health  — current system health from metrics & governor.
  6. Drift vs baseline — deviation from historical averages (EMA).

Aggregation: Bayesian-weighted sum with dynamic weight adjustment
based on governor mode.

Integration points:
  - Governor calls ``assess()`` before allowing operations.
  - Results exported via ``get_metrics_collector()``.
  - Baselines persisted in ``cognition_state.json``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from core import state_manager

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Impact scores by operation type
IMPACT_BY_OP: Dict[str, float] = {
    "autopatch":    0.9,
    "workflow":     0.7,
    "llm_generate": 0.5,
    "ingest":       0.3,
    "smoke":        0.1,
    "read_only":    0.0,
}

# Irreversibility scores by flag
IRREVERSIBILITY_FLAGS: Dict[str, float] = {
    "writes_files":   0.8,
    "modifies_state": 0.6,
    "external_call":  0.7,
}

# External exposure scores
EXPOSURE_FLAGS: Dict[str, float] = {
    "cloud_provider": 1.0,
    "api_call":       0.9,
    "network_io":     0.7,
}

# Sensitivity by topic
SENSITIVITY_BY_TOPIC: Dict[str, float] = {
    "health":       0.9,
    "trading":      0.8,
    "relationship": 0.6,
    "code":         0.3,
    "general":      0.1,
}

# Governor mode penalty for runtime health signal
GOVERNOR_MODE_PENALTY: Dict[str, float] = {
    "recover":  0.9,
    "cautious": 0.5,
    "observe":  0.3,
    "act":      0.0,
}

# Base weights for aggregation (sum = 1.0)
BASE_WEIGHTS: Dict[str, float] = {
    "impact":          0.25,
    "irreversibility": 0.20,
    "exposure":        0.15,
    "sensitivity":     0.15,
    "runtime":         0.15,
    "drift":           0.10,
}

# Risk level thresholds
RISK_THRESHOLDS = {
    "LOW":      0.30,
    "MEDIUM":   0.60,
    "HIGH":     0.85,
    # CRITICAL = >= 0.85
}

# Baseline EMA smoothing factor
EMA_ALPHA = 0.1
EMA_EPSILON = 1e-6

# Governor error window (mirrors governor.py)
ERROR_WINDOW = 10
TIMEOUT_THRESHOLD = 60.0  # seconds — matches config default


# ---------------------------------------------------------------------------
# Enums & data classes
# ---------------------------------------------------------------------------

class RiskLevel(str, Enum):
    LOW      = "LOW"
    MEDIUM   = "MEDIUM"
    HIGH     = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class OperationContext:
    """Describes the operation to be risk-assessed."""
    op_type: str = "read_only"
    irreversibility_flags: List[str] = field(default_factory=list)
    exposure_flags: List[str] = field(default_factory=list)
    topic: str = "general"
    # Runtime snapshot (caller provides or engine reads from state)
    error_history: Optional[List[bool]] = None
    p95_latency: Optional[float] = None
    circuit_breakers_open: int = 0
    total_providers: int = 1
    governor_mode: Optional[str] = None
    # Optional overrides
    impact_override: Optional[float] = None


@dataclass
class SignalResult:
    """Result of a single risk signal evaluation."""
    name: str
    value: float          # ∈ [0, 1]
    weight: float         # effective weight after dynamic adjustment
    details: str = ""

    @property
    def contribution(self) -> float:
        return self.value * self.weight


@dataclass
class RiskAssessment:
    """Final risk assessment for an operation."""
    risk_score: float
    risk_level: RiskLevel
    signals: List[SignalResult]
    timestamp: float
    op_context: OperationContext

    def breakdown(self) -> Dict[str, Any]:
        """Return audit-friendly dict with full signal breakdown."""
        return {
            "risk_score": round(self.risk_score, 4),
            "risk_level": self.risk_level.value,
            "timestamp": self.timestamp,
            "signals": [
                {
                    "name": s.name,
                    "value": round(s.value, 4),
                    "weight": round(s.weight, 4),
                    "contribution": round(s.contribution, 4),
                    "details": s.details,
                }
                for s in self.signals
            ],
            "op_type": self.op_context.op_type,
            "topic": self.op_context.topic,
        }


# ---------------------------------------------------------------------------
# Baseline Tracker (EMA persistence)
# ---------------------------------------------------------------------------

class BaselineTracker:
    """Maintains exponential moving averages for drift detection."""

    STATE_KEY = "risk_baselines"

    def __init__(self, alpha: float = EMA_ALPHA):
        self.alpha = alpha

    def _load(self) -> Dict[str, float]:
        st = state_manager.load()
        return st.get(self.STATE_KEY, {})

    def _save(self, baselines: Dict[str, float]) -> None:
        state_manager.put(self.STATE_KEY, baselines)

    def get(self, metric: str) -> Optional[float]:
        return self._load().get(metric)

    def update(self, metric: str, current: float) -> float:
        """Update EMA for *metric* with *current* value. Returns new EMA."""
        baselines = self._load()
        prev = baselines.get(metric)
        if prev is None:
            ema = current
        else:
            ema = self.alpha * current + (1 - self.alpha) * prev
        baselines[metric] = ema
        self._save(baselines)
        return ema

    def drift(self, metric: str, current: float) -> float:
        """Return normalised drift ∈ [0,1] for *metric*."""
        ema = self.get(metric)
        if ema is None:
            return 0.0  # no baseline yet → no drift
        deviation = abs(current - ema) / (abs(ema) + EMA_EPSILON)
        return min(deviation, 1.0)


# ---------------------------------------------------------------------------
# Signal evaluators
# ---------------------------------------------------------------------------

def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def signal_impact(ctx: OperationContext) -> float:
    if ctx.impact_override is not None:
        return _clamp(ctx.impact_override)
    return IMPACT_BY_OP.get(ctx.op_type, 0.5)


def signal_irreversibility(ctx: OperationContext) -> float:
    if not ctx.irreversibility_flags:
        return 0.0
    total = sum(
        IRREVERSIBILITY_FLAGS.get(f, 0.0) for f in ctx.irreversibility_flags
    )
    return _clamp(total)


def signal_exposure(ctx: OperationContext) -> float:
    if not ctx.exposure_flags:
        return 0.0
    return _clamp(max(
        EXPOSURE_FLAGS.get(f, 0.0) for f in ctx.exposure_flags
    ))


def signal_sensitivity(ctx: OperationContext) -> float:
    return SENSITIVITY_BY_TOPIC.get(ctx.topic, 0.1)


def signal_runtime(ctx: OperationContext) -> float:
    """Combine runtime health indicators into a single [0,1] score."""
    components: List[float] = []

    # Error rate in recent window
    history = ctx.error_history
    if history is None:
        st = state_manager.load()
        history = st.get("error_history", [])
    if history:
        error_rate = sum(1 for e in history if e) / len(history)
    else:
        error_rate = 0.0
    components.append(error_rate)

    # Latency pressure
    if ctx.p95_latency is not None:
        latency_ratio = ctx.p95_latency / TIMEOUT_THRESHOLD
        components.append(_clamp(latency_ratio))

    # Circuit breaker pressure
    if ctx.total_providers > 0:
        cb_ratio = ctx.circuit_breakers_open / ctx.total_providers
        components.append(_clamp(cb_ratio))

    # Governor mode penalty
    mode = ctx.governor_mode
    if mode is None:
        st = state_manager.load()
        mode = st.get("governor_mode", "observe")
    components.append(GOVERNOR_MODE_PENALTY.get(mode, 0.0))

    if not components:
        return 0.0
    return _clamp(sum(components) / len(components))


def signal_drift(ctx: OperationContext, tracker: BaselineTracker) -> float:
    """Compute drift signal from latency and error_rate baselines."""
    drifts: List[float] = []

    # Latency drift
    if ctx.p95_latency is not None:
        d = tracker.drift("latency_p95", ctx.p95_latency)
        drifts.append(d)

    # Error rate drift
    history = ctx.error_history
    if history is None:
        st = state_manager.load()
        history = st.get("error_history", [])
    if history:
        error_rate = sum(1 for e in history if e) / len(history)
        d = tracker.drift("error_rate", error_rate)
        drifts.append(d)

    if not drifts:
        return 0.0
    return _clamp(sum(drifts) / len(drifts))


# ---------------------------------------------------------------------------
# Risk Engine
# ---------------------------------------------------------------------------

class RiskEngine:
    """
    Probabilistic risk engine.

    Usage::

        engine = RiskEngine()
        ctx = OperationContext(op_type="autopatch", topic="trading",
                               irreversibility_flags=["writes_files"])
        assessment = engine.assess(ctx)
        print(assessment.risk_score, assessment.risk_level)
    """

    def __init__(self):
        self.baseline_tracker = BaselineTracker()

    # -- weight adjustment --------------------------------------------------

    @staticmethod
    def _adjust_weights(governor_mode: str) -> Dict[str, float]:
        """Return dynamically adjusted weights based on governor mode."""
        weights = dict(BASE_WEIGHTS)
        if governor_mode == "recover":
            weights["runtime"] *= 2.0
        elif governor_mode == "observe":
            weights["drift"] *= 0.5
        return weights

    # -- risk level classification ------------------------------------------

    @staticmethod
    def classify(score: float) -> RiskLevel:
        if score < RISK_THRESHOLDS["LOW"]:
            return RiskLevel.LOW
        if score < RISK_THRESHOLDS["MEDIUM"]:
            return RiskLevel.MEDIUM
        if score < RISK_THRESHOLDS["HIGH"]:
            return RiskLevel.HIGH
        return RiskLevel.CRITICAL

    # -- main assessment ----------------------------------------------------

    def assess(self, ctx: OperationContext) -> RiskAssessment:
        """
        Evaluate all 6 signals and return a ``RiskAssessment``.

        The assessment is fully traceable — every signal's value, weight
        and contribution is recorded for audit.
        """
        # Resolve governor mode for weight adjustment
        mode = ctx.governor_mode
        if mode is None:
            st = state_manager.load()
            mode = st.get("governor_mode", "observe")

        weights = self._adjust_weights(mode)

        # Evaluate each signal
        signals: List[SignalResult] = []

        s_impact = signal_impact(ctx)
        signals.append(SignalResult(
            name="impact",
            value=s_impact,
            weight=weights["impact"],
            details=f"op_type={ctx.op_type}"
            + (f" (override={ctx.impact_override})" if ctx.impact_override is not None else ""),
        ))

        s_irrev = signal_irreversibility(ctx)
        signals.append(SignalResult(
            name="irreversibility",
            value=s_irrev,
            weight=weights["irreversibility"],
            details=f"flags={ctx.irreversibility_flags}",
        ))

        s_exp = signal_exposure(ctx)
        signals.append(SignalResult(
            name="exposure",
            value=s_exp,
            weight=weights["exposure"],
            details=f"flags={ctx.exposure_flags}",
        ))

        s_sens = signal_sensitivity(ctx)
        signals.append(SignalResult(
            name="sensitivity",
            value=s_sens,
            weight=weights["sensitivity"],
            details=f"topic={ctx.topic}",
        ))

        s_runtime = signal_runtime(ctx)
        signals.append(SignalResult(
            name="runtime",
            value=s_runtime,
            weight=weights["runtime"],
            details=f"governor_mode={mode}",
        ))

        s_drift = signal_drift(ctx, self.baseline_tracker)
        signals.append(SignalResult(
            name="drift",
            value=s_drift,
            weight=weights["drift"],
            details="latency+error_rate EMA drift",
        ))

        # Weighted aggregation
        total_weight = sum(s.weight for s in signals)
        if total_weight == 0:
            risk_score = 0.0
        else:
            risk_score = sum(s.contribution for s in signals) / total_weight

        risk_score = _clamp(risk_score)

        # Update baselines after assessment
        if ctx.p95_latency is not None:
            self.baseline_tracker.update("latency_p95", ctx.p95_latency)

        history = ctx.error_history
        if history is None:
            st = state_manager.load()
            history = st.get("error_history", [])
        if history:
            error_rate = sum(1 for e in history if e) / len(history)
            self.baseline_tracker.update("error_rate", error_rate)

        assessment = RiskAssessment(
            risk_score=risk_score,
            risk_level=self.classify(risk_score),
            signals=signals,
            timestamp=time.time(),
            op_context=ctx,
        )

        # Record in metrics (best-effort — don't fail if metrics unavailable)
        try:
            from core.observability.metrics import get_metrics_collector
            mc = get_metrics_collector()
            hist = mc.get_histogram("risk_score_distribution")
            if hist:
                hist.observe(risk_score)
            ctr = mc.get_counter("risk_assessments_total")
            if ctr:
                ctr.inc()
        except Exception:
            pass

        return assessment


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_global_engine: Optional[RiskEngine] = None


def get_risk_engine() -> RiskEngine:
    """Return the global singleton ``RiskEngine``."""
    global _global_engine
    if _global_engine is None:
        _global_engine = RiskEngine()
    return _global_engine


def assess(ctx: OperationContext) -> RiskAssessment:
    """Shortcut: assess an operation using the global engine."""
    return get_risk_engine().assess(ctx)
