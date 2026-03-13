"""
Vectrax Shadow Mode
====================
Observe-only mode that records risk_score and confidence_score per
operation **without** modifying system behaviour.

Activated for a fixed window of N operations (default 1000).
After the window completes, a consolidated report is written to
``reports/shadow_report.json``.

Shadow Mode does NOT:
  - Block any operation (HIGH does not block).
  - Change thresholds.
  - Alter governor decisions.

Shadow Mode DOES:
  - Calculate risk_score + confidence_score every operation.
  - Log full signal breakdown + baseline snapshot.
  - Accept post-operation outcome (error/retry/fallback/rollback/ok).
  - Auto-generate histograms, level percentages, and correlation stats.
  - Write a final consolidated report at window end.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from core import state_manager
from core.risk_engine import (
    OperationContext,
    RiskAssessment,
    RiskEngine,
    RiskLevel,
    BaselineTracker,
    get_risk_engine,
    _clamp,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_WINDOW = 1000
SHADOW_LOG_FILENAME = "shadow_log.jsonl"
SHADOW_REPORT_FILENAME = "shadow_report.json"

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")
RUNTIME_DIR = os.path.expanduser("~/.vectrax")

# Outcome labels recognised by record_outcome
VALID_OUTCOMES = {"ok", "error", "retry", "fallback", "rollback"}

# Histogram bucket edges for risk_score and confidence_score
HIST_BUCKETS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


# ---------------------------------------------------------------------------
# Confidence score
# ---------------------------------------------------------------------------

def compute_confidence(assessment: RiskAssessment) -> float:
    """
    Confidence ∈ [0,1] measures how *certain* the engine is about its
    risk_score.  High confidence = signals agree; low = conflicting signals.

    Method: 1 − normalised standard-deviation of signal values.
    When all signals are identical → std=0 → confidence=1.
    Maximum spread (half at 0, half at 1) → std≈0.5 → confidence≈0.
    """
    values = [s.value for s in assessment.signals]
    if len(values) < 2:
        return 1.0
    std = statistics.pstdev(values)  # population std
    # Max possible pstdev for [0,1] values ≈ 0.5
    return _clamp(1.0 - (std / 0.5))


# ---------------------------------------------------------------------------
# Shadow record data
# ---------------------------------------------------------------------------

@dataclass
class ShadowRecord:
    """One recorded operation in shadow mode."""
    op_id: int
    timestamp: float
    risk_score: float
    confidence_score: float
    risk_level: str
    signal_breakdown: List[Dict[str, Any]]
    baseline_snapshot: Dict[str, float]
    op_type: str
    topic: str
    governor_mode: str
    # Filled later by record_outcome()
    outcome: Optional[str] = None
    outcome_ts: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Shadow Mode controller
# ---------------------------------------------------------------------------

class ShadowMode:
    """
    Activates shadow observation for *window* operations.

    Usage::

        shadow = get_shadow_mode()
        shadow.activate(window=1000)

        # On each operation:
        rec = shadow.record_operation(ctx)
        # ... operation executes ...
        shadow.record_outcome(rec.op_id, "ok")  # or "error", etc.

        # After window completes, report auto-generates.
    """

    STATE_KEY = "shadow_mode"

    def __init__(self):
        self._records: List[ShadowRecord] = []
        self._load_state()

    # -- state persistence --------------------------------------------------

    def _load_state(self) -> None:
        st = state_manager.load()
        sm = st.get(self.STATE_KEY, {})
        self.active: bool = sm.get("active", False)
        self.window: int = sm.get("window", DEFAULT_WINDOW)
        self.ops_recorded: int = sm.get("ops_recorded", 0)
        self.activated_at: Optional[float] = sm.get("activated_at")

    def _save_state(self) -> None:
        state_manager.put(self.STATE_KEY, {
            "active": self.active,
            "window": self.window,
            "ops_recorded": self.ops_recorded,
            "activated_at": self.activated_at,
        })

    # -- activation ---------------------------------------------------------

    def activate(self, window: int = DEFAULT_WINDOW) -> None:
        """Start shadow observation for *window* operations."""
        self.active = True
        self.window = window
        self.ops_recorded = 0
        self.activated_at = time.time()
        self._records = []
        self._save_state()
        # Truncate previous log
        self._log_path().open("w").close() if self._log_path().exists() else None
        os.makedirs(RUNTIME_DIR, exist_ok=True)
        open(self._log_path_str(), "w").close()

    def deactivate(self) -> None:
        """Manually stop shadow mode."""
        self.active = False
        self._save_state()

    @property
    def remaining(self) -> int:
        return max(0, self.window - self.ops_recorded)

    @property
    def is_complete(self) -> bool:
        return self.ops_recorded >= self.window

    # -- paths --------------------------------------------------------------

    @staticmethod
    def _log_path_str() -> str:
        return os.path.join(RUNTIME_DIR, SHADOW_LOG_FILENAME)

    def _log_path(self):
        import pathlib
        return pathlib.Path(self._log_path_str())

    @staticmethod
    def _report_path() -> str:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        return os.path.join(REPORTS_DIR, SHADOW_REPORT_FILENAME)

    # -- per-operation recording --------------------------------------------

    def record_operation(
        self,
        ctx: OperationContext,
        engine: Optional[RiskEngine] = None,
    ) -> Optional[ShadowRecord]:
        """
        Assess and record one operation.  Returns the ShadowRecord
        (or None if shadow mode is not active).

        **Does not modify behaviour.**
        """
        if not self.active:
            return None

        if self.is_complete:
            return None

        eng = engine or get_risk_engine()
        assessment = eng.assess(ctx)
        confidence = compute_confidence(assessment)

        # Capture baseline snapshot
        baselines = eng.baseline_tracker._load()

        self.ops_recorded += 1
        rec = ShadowRecord(
            op_id=self.ops_recorded,
            timestamp=time.time(),
            risk_score=assessment.risk_score,
            confidence_score=confidence,
            risk_level=assessment.risk_level.value,
            signal_breakdown=[
                {
                    "name": s.name,
                    "value": round(s.value, 4),
                    "weight": round(s.weight, 4),
                    "contribution": round(s.contribution, 4),
                    "details": s.details,
                }
                for s in assessment.signals
            ],
            baseline_snapshot={k: round(v, 6) for k, v in baselines.items()},
            op_type=ctx.op_type,
            topic=ctx.topic,
            governor_mode=ctx.governor_mode or "unknown",
        )

        self._records.append(rec)
        self._append_log(rec)
        self._save_state()

        # Record confidence in metrics (best-effort)
        try:
            from core.observability.metrics import get_metrics_collector
            mc = get_metrics_collector()
            h = mc.get_histogram("confidence_score_distribution")
            if h:
                h.observe(confidence)
        except Exception:
            pass

        # Auto-generate report when window completes
        if self.is_complete:
            self._generate_report()
            self.active = False
            self._save_state()

        return rec

    def record_outcome(self, op_id: int, outcome: str) -> bool:
        """
        Attach the real post-operation outcome to a recorded operation.

        Args:
            op_id:   operation id returned in ShadowRecord.op_id
            outcome: one of "ok", "error", "retry", "fallback", "rollback"

        Returns True if matched, False if op_id not found in memory.
        """
        outcome = outcome.lower()
        if outcome not in VALID_OUTCOMES:
            outcome = "ok"

        for rec in self._records:
            if rec.op_id == op_id:
                rec.outcome = outcome
                rec.outcome_ts = time.time()
                self._patch_log_line(op_id, outcome)
                return True
        return False

    # -- log I/O ------------------------------------------------------------

    def _append_log(self, rec: ShadowRecord) -> None:
        os.makedirs(RUNTIME_DIR, exist_ok=True)
        with open(self._log_path_str(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")

    def _patch_log_line(self, op_id: int, outcome: str) -> None:
        """Best-effort: re-read log, patch the matching line, rewrite."""
        path = self._log_path_str()
        if not os.path.isfile(path):
            return
        lines = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                    if obj.get("op_id") == op_id:
                        obj["outcome"] = outcome
                        obj["outcome_ts"] = time.time()
                    lines.append(json.dumps(obj, ensure_ascii=False) + "\n")
                except json.JSONDecodeError:
                    lines.append(line)
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)

    def load_log(self) -> List[Dict[str, Any]]:
        """Read all records from the JSONL log (for report generation)."""
        path = self._log_path_str()
        if not os.path.isfile(path):
            return []
        records = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records

    # -- statistics & report ------------------------------------------------

    def compute_stats(self, records: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """
        Compute consolidated statistics from shadow records.

        Returns dict with:
          - histograms (risk_score, confidence_score)
          - level percentages
          - correlation: HIGH/CRITICAL vs real failures
          - summary scalars (mean, std, rates)
        """
        if records is None:
            records = self._records_as_dicts()
        if not records:
            return {"error": "no records"}

        n = len(records)
        risk_scores = [r["risk_score"] for r in records]
        conf_scores = [r["confidence_score"] for r in records]
        levels = [r["risk_level"] for r in records]
        outcomes = [r.get("outcome", "ok") for r in records]

        # --- Histograms ---
        risk_hist = self._histogram(risk_scores)
        conf_hist = self._histogram(conf_scores)

        # --- Level percentages ---
        level_counts = {lv: 0 for lv in ("LOW", "MEDIUM", "HIGH", "CRITICAL")}
        for lv in levels:
            level_counts[lv] = level_counts.get(lv, 0) + 1
        level_pct = {k: round(v / n * 100, 2) for k, v in level_counts.items()}

        # --- Correlation: HIGH/CRITICAL vs real failures ---
        failure_outcomes = {"error", "retry", "fallback", "rollback"}
        high_critical_indices = [
            i for i, lv in enumerate(levels) if lv in ("HIGH", "CRITICAL")
        ]
        hc_count = len(high_critical_indices)
        hc_failures = sum(
            1 for i in high_critical_indices if outcomes[i] in failure_outcomes
        )

        total_failures = sum(1 for o in outcomes if o in failure_outcomes)
        total_non_hc = n - hc_count
        non_hc_failures = total_failures - hc_failures

        # Correlation metrics
        if hc_count > 0:
            hc_failure_rate = round(hc_failures / hc_count, 4)
        else:
            hc_failure_rate = 0.0

        if total_non_hc > 0:
            non_hc_failure_rate = round(non_hc_failures / total_non_hc, 4)
        else:
            non_hc_failure_rate = 0.0

        # False positive rate: HIGH/CRITICAL flagged but outcome was "ok"
        if hc_count > 0:
            false_positives = hc_count - hc_failures
            false_positive_rate = round(false_positives / hc_count, 4)
        else:
            false_positive_rate = 0.0

        # --- Summary scalars ---
        mean_risk = round(statistics.mean(risk_scores), 4)
        std_risk = round(statistics.pstdev(risk_scores), 4) if n > 1 else 0.0
        mean_conf = round(statistics.mean(conf_scores), 4)
        critical_rate = round(level_counts["CRITICAL"] / n, 4)
        high_rate = round(level_counts["HIGH"] / n, 4)

        return {
            "total_operations": n,
            "risk_score_histogram": risk_hist,
            "confidence_score_histogram": conf_hist,
            "level_percentages": level_pct,
            "correlation": {
                "high_critical_count": hc_count,
                "high_critical_failures": hc_failures,
                "high_critical_failure_rate": hc_failure_rate,
                "non_hc_failure_rate": non_hc_failure_rate,
            },
            "summary": {
                "mean_risk": mean_risk,
                "std_risk": std_risk,
                "mean_confidence": mean_conf,
                "CRITICAL_rate": critical_rate,
                "HIGH_rate": high_rate,
                "false_positive_rate": false_positive_rate,
            },
        }

    @staticmethod
    def _histogram(values: List[float]) -> Dict[str, int]:
        """Bucket values into 0.0–0.1, 0.1–0.2, … 0.9–1.0."""
        buckets = {f"{b:.1f}-{b + 0.1:.1f}": 0 for b in HIST_BUCKETS[:-1]}
        for v in values:
            idx = min(int(v * 10), 9)
            key = f"{idx / 10:.1f}-{(idx + 1) / 10:.1f}"
            buckets[key] = buckets.get(key, 0) + 1
        return buckets

    def _records_as_dicts(self) -> List[Dict[str, Any]]:
        """Convert in-memory records to dicts. Falls back to log file."""
        if self._records:
            return [r.to_dict() for r in self._records]
        return self.load_log()

    def _generate_report(self) -> str:
        """Generate and persist the consolidated report. Returns file path."""
        records = self._records_as_dicts()
        if not records:
            records = self.load_log()

        stats = self.compute_stats(records)
        stats["generated_at"] = time.time()
        stats["window"] = self.window
        stats["activated_at"] = self.activated_at

        path = self._report_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
            f.write("\n")

        return path

    def generate_report(self) -> str:
        """Public API — generate report on demand (before window ends)."""
        return self._generate_report()

    # -- status -------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        return {
            "active": self.active,
            "window": self.window,
            "ops_recorded": self.ops_recorded,
            "remaining": self.remaining,
            "is_complete": self.is_complete,
            "activated_at": self.activated_at,
        }


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_global_shadow: Optional[ShadowMode] = None


def get_shadow_mode() -> ShadowMode:
    """Return the global ShadowMode instance."""
    global _global_shadow
    if _global_shadow is None:
        _global_shadow = ShadowMode()
    return _global_shadow
