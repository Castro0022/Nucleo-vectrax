"""
connectors/etoro/convergence_learner.py — Trading Convergence Learner.

Architecture
------------
Observes the statistical gap between observed market performance and the
configured entry thresholds in auto_executor.  Generates structured proposals
for threshold adjustments — never applies them.  Creator reviews and applies
via `/vx market learner status` or Telegram approval flow.

This is the trading-domain equivalent of
core/nucleus/convergence_learner.py (PresenciaPura), which observes
operational patterns and proposes convergence-level changes.

Observation cycle (runs every 24h inside pipeline_worker):
  1. Load current outcomes from etoro_signals.jsonl
  2. Load current patterns from etoro_patterns.json
  3. Load current thresholds from auto_executor._load_config()
  4. Detect drift conditions (see DetectionRule below)
  5. Generate proposals for drifted thresholds (with evidence)
  6. Write proposals to etoro_learner_proposals.jsonl + ledger
  7. Return structured summary — never mutate config

Detection rules
---------------
  WR_GAP:       observed_wr < min_paper_win_rate - GAP_TRIGGER_PP
                → market is below threshold; surface evidence for review
  SAMPLE_GAP:   best_pattern.n < min_paper_signals
                → not enough data yet; no action needed
  EXPECTANCY:   observed_expectancy > 0 but all patterns MEDIUM/LOW
                → positive-EV signal from aggregate; worth reviewing
  DORMANCY:     no new outcomes for > DORMANCY_DAYS
                → market connection may be stalled

Gap values are tuned to avoid noise:
  GAP_TRIGGER_PP = 5   (proposal only when gap > 5pp, not on minor fluctuations)
  DORMANCY_DAYS  = 3

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.etoro.convergence_learner")

# ── Paths (same as auto_executor / signal_recorder) ───────────────────
_HOME = os.path.expanduser("~")
_SIGNALS_FILE  = os.path.join(_HOME, ".vectrax", "etoro_signals.jsonl")
_PATTERNS_FILE = os.path.join(_HOME, ".vectrax", "etoro_patterns.json")
_PROPOSALS_FILE = os.path.join(_HOME, ".vectrax", "etoro_learner_proposals.jsonl")

# ── Detection thresholds ──────────────────────────────────────────────
GAP_TRIGGER_PP = 5.0    # minimum gap (pp) to surface a WR proposal
DORMANCY_DAYS  = 3      # days without new outcomes before DORMANT alert
MIN_RESOLVED   = 20     # min resolved outcomes to consider WR statistically meaningful


class DriftKind(str, Enum):
    WR_GAP      = "wr_gap"          # observed WR lags configured threshold
    SAMPLE_GAP  = "sample_gap"      # patterns not reaching N threshold
    EXPECTANCY  = "expectancy"      # aggregate EV positive despite LOW patterns
    DORMANCY    = "dormancy"        # no new outcomes recently


@dataclass
class Proposal:
    proposal_id: str
    drift_kind: str
    evidence: Dict[str, Any]
    current_threshold: Any
    suggested_review: str           # human-readable guidance — NOT a new value
    confidence: float               # 0–1 based on sample size
    generated_at: float
    applied: bool = False
    applied_at: float = 0.0
    applied_by: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_observation_cycle() -> Dict[str, Any]:
    """
    Run one observation cycle.  Returns a machine-readable summary.
    Never modifies auto_executor config.
    """
    t0 = time.time()
    summary: Dict[str, Any] = {
        "success": False,
        "proposals_generated": 0,
        "drift_kinds": [],
        "elapsed_s": 0.0,
    }

    try:
        signals  = _load_signals()
        patterns = _load_patterns()
        config   = _load_config()

        # etoro_signals.jsonl schema: status=win|loss|neutral|expired, outcome_timestamp
        # (field is 'status', not 'outcome'; timestamp is 'outcome_timestamp')
        resolved = [s for s in signals if s.get("status") in ("win", "loss", "neutral", "expired")]
        wins     = [s for s in resolved if s.get("status") == "win"]
        losses   = [s for s in resolved if s.get("status") == "loss"]

        tradeable_resolved = [s for s in resolved if s.get("status") in ("win", "loss")]
        observed_wr = (len(wins) / len(tradeable_resolved) * 100) \
                      if tradeable_resolved else 0.0
        best_n      = max((p.get("n", 0) for p in patterns), default=0)
        total_w     = sum(p.get("total_wins", 0) for p in patterns)
        total_l     = sum(p.get("total_losses", 0) for p in patterns)
        agg_wr      = (total_w / (total_w + total_l) * 100) if (total_w + total_l) > 0 else 0.0
        last_ts     = max((s.get("outcome_timestamp", 0) for s in resolved), default=0)
        days_since  = (time.time() - last_ts) / 86400 if last_ts else float("inf")

        proposals: List[Proposal] = []

        # ── Rule 1: WR_GAP ──────────────────────────────────────────────
        threshold_wr = config.get("min_paper_win_rate", 60.0)
        if len(resolved) >= MIN_RESOLVED:
            tradeable = tradeable_resolved
            gap = threshold_wr - observed_wr
            if gap > GAP_TRIGGER_PP:
                confidence = min(1.0, len(tradeable) / 100)
                proposals.append(Proposal(
                    proposal_id=f"LRN-{uuid.uuid4().hex[:8].upper()}",
                    drift_kind=DriftKind.WR_GAP,
                    evidence={
                        "observed_wr_pct": round(observed_wr, 1),
                        "configured_min_wr_pct": threshold_wr,
                        "gap_pp": round(gap, 1),
                        "resolved_outcomes": len(tradeable),
                        "wins": len(wins), "losses": len(losses),
                    },
                    current_threshold=threshold_wr,
                    suggested_review=(
                        f"Observed WR ({observed_wr:.1f}%) is {gap:.1f}pp below "
                        f"min_paper_win_rate ({threshold_wr}%). "
                        f"Consider: (a) waiting for market conditions to improve, "
                        f"(b) reviewing signal quality thresholds in entry_validator, "
                        f"(c) adjusting min_paper_win_rate to {max(50.0, threshold_wr - gap/2):.0f}% "
                        f"for PAPER mode only if market regime has changed."
                    ),
                    confidence=confidence,
                    generated_at=time.time(),
                ))

        # ── Rule 2: SAMPLE_GAP ──────────────────────────────────────────
        min_signals = config.get("min_paper_signals", 30)
        if best_n < min_signals and best_n > 0:
            proposals.append(Proposal(
                proposal_id=f"LRN-{uuid.uuid4().hex[:8].upper()}",
                drift_kind=DriftKind.SAMPLE_GAP,
                evidence={
                    "best_pattern_n": best_n,
                    "min_paper_signals": min_signals,
                    "gap": min_signals - best_n,
                    "total_patterns": len(patterns),
                },
                current_threshold=min_signals,
                suggested_review=(
                    f"Best pattern has N={best_n} resolved signals; threshold is {min_signals}. "
                    f"No action needed — continue accumulating data. "
                    f"At current rate, threshold is reachable with ~{min_signals - best_n} more signals."
                ),
                confidence=0.95,   # high confidence — factual count
                generated_at=time.time(),
            ))

        # ── Rule 3: EXPECTANCY ──────────────────────────────────────────
        agg_expectancy = (agg_wr / 100) - ((1 - agg_wr / 100))  # simplified Kelly proxy
        usable = [p for p in patterns if p.get("quality_tier") not in ("LOW",)]
        if agg_expectancy > 0 and len(usable) == 0 and len(patterns) >= 5:
            proposals.append(Proposal(
                proposal_id=f"LRN-{uuid.uuid4().hex[:8].upper()}",
                drift_kind=DriftKind.EXPECTANCY,
                evidence={
                    "aggregate_wr_pct": round(agg_wr, 1),
                    "aggregate_expectancy": round(agg_expectancy, 3),
                    "total_patterns": len(patterns),
                    "usable_patterns": len(usable),
                },
                current_threshold=config.get("min_paper_win_rate"),
                suggested_review=(
                    f"Aggregate pattern EV={agg_expectancy:.3f} is positive "
                    f"but all {len(patterns)} patterns are LOW quality (N too small). "
                    f"Portfolio-level signal may be emerging. No individual pattern "
                    f"is ready. Consider monitoring for first MEDIUM-tier pattern."
                ),
                confidence=min(1.0, len(patterns) / 30),
                generated_at=time.time(),
            ))

        # ── Rule 4: DORMANCY ────────────────────────────────────────────
        if days_since > DORMANCY_DAYS:
            proposals.append(Proposal(
                proposal_id=f"LRN-{uuid.uuid4().hex[:8].upper()}",
                drift_kind=DriftKind.DORMANCY,
                evidence={
                    "days_since_last_outcome": round(days_since, 1),
                    "dormancy_threshold_days": DORMANCY_DAYS,
                    "total_resolved": len(resolved),
                },
                current_threshold=None,
                suggested_review=(
                    f"No new resolved outcomes in {days_since:.1f} days. "
                    f"Verify eToro API connectivity and that the market learning "
                    f"cycle is running (check worker logs for 'Market learn' entries)."
                ),
                confidence=1.0,
                generated_at=time.time(),
            ))

        # ── Persist proposals ────────────────────────────────────────────
        for p in proposals:
            _append_proposal(p)

        summary.update({
            "success": True,
            "proposals_generated": len(proposals),
            "drift_kinds": [p.drift_kind for p in proposals],
            "observed_wr_pct": round(observed_wr, 1),
            "best_pattern_n": best_n,
            "resolved_total": len(resolved),
            "patterns_total": len(patterns),
            "days_since_outcome": round(days_since, 1) if days_since != float("inf") else None,
        })

        logger.info(
            "trading.convergence_learner | proposals=%d | kinds=%s | wr=%.1f%% | best_n=%d",
            len(proposals), [p.drift_kind for p in proposals], observed_wr, best_n,
        )

    except Exception as exc:
        summary["detail"] = str(exc)[:200]
        logger.error("convergence_learner FAILED: %s", exc)

    summary["elapsed_s"] = round(time.time() - t0, 3)
    _record_ledger(summary)
    return summary


def get_pending_proposals() -> List[Dict[str, Any]]:
    """Return all unapplied proposals from disk."""
    proposals = []
    if not os.path.exists(_PROPOSALS_FILE):
        return proposals
    try:
        with open(_PROPOSALS_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    p = json.loads(line)
                    if not p.get("applied"):
                        proposals.append(p)
    except Exception as exc:
        logger.debug("get_pending_proposals error: %s", exc)
    return proposals


def approve_proposal(proposal_id: str, approved_by: str = "creator") -> bool:
    """
    Mark a proposal as approved/applied.  Does NOT apply the change —
    the creator must update auto_executor config separately.
    Returns True if proposal was found and marked.
    """
    if not os.path.exists(_PROPOSALS_FILE):
        return False
    lines = []
    found = False
    try:
        with open(_PROPOSALS_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                p = json.loads(line)
                if p.get("proposal_id") == proposal_id and not p.get("applied"):
                    p["applied"] = True
                    p["applied_at"] = time.time()
                    p["applied_by"] = approved_by
                    found = True
                lines.append(json.dumps(p, ensure_ascii=False))
        with open(_PROPOSALS_FILE, "w") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as exc:
        logger.error("approve_proposal error: %s", exc)
    return found


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_signals() -> List[Dict]:
    if not os.path.exists(_SIGNALS_FILE):
        return []
    signals = []
    try:
        with open(_SIGNALS_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    signals.append(json.loads(line))
    except Exception:
        pass
    return signals


def _load_patterns() -> List[Dict]:
    if not os.path.exists(_PATTERNS_FILE):
        return []
    try:
        with open(_PATTERNS_FILE) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return list(data.values())
    except Exception:
        pass
    return []


def _load_config() -> Dict:
    try:
        from connectors.etoro.auto_executor import _load_config
        return _load_config()
    except Exception:
        return {}


def _append_proposal(p: Proposal) -> None:
    try:
        os.makedirs(os.path.dirname(_PROPOSALS_FILE), exist_ok=True)
        with open(_PROPOSALS_FILE, "a") as f:
            f.write(json.dumps(p.to_dict(), ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        logger.debug("_append_proposal error: %s", exc)


def _record_ledger(summary: Dict[str, Any]) -> None:
    try:
        from core.operator import ledger_bridge as ledger
        ledger.record_event(
            action="trading_convergence_learner",
            category=ledger.EventCategory.LEARNING,
            risk_zone=ledger.RiskZone.GREEN,
            reason=(
                f"proposals={summary.get('proposals_generated')} "
                f"wr={summary.get('observed_wr_pct')}% "
                f"drifts={summary.get('drift_kinds')}"
            ),
            details=summary,
        )
    except Exception:
        pass
