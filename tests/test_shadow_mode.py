"""
Tests for Vectrax Shadow Mode
===============================
Covers: activation, record_operation, record_outcome, confidence_score,
stats generation, auto-report at window end, no behaviour modification.
"""

import json
import os
import pytest

import core.state_manager as sm
import core.shadow_mode as shadow_mod

_orig_state_path = sm.STATE_PATH
_orig_runtime_dir = sm.RUNTIME_DIR


@pytest.fixture(autouse=True)
def isolate(tmp_path):
    """Isolate state, shadow log, and report paths per test."""
    sm.RUNTIME_DIR = str(tmp_path)
    sm.STATE_PATH = os.path.join(str(tmp_path), "cognition_state.json")
    sm.save(dict(sm.DEFAULT_STATE))

    # Redirect shadow mode paths
    shadow_mod.RUNTIME_DIR = str(tmp_path)
    shadow_mod.REPORTS_DIR = str(tmp_path / "reports")

    # Reset singletons
    import core.risk_engine as re_mod
    re_mod._global_engine = None
    shadow_mod._global_shadow = None

    yield

    sm.RUNTIME_DIR = _orig_runtime_dir
    sm.STATE_PATH = _orig_state_path


from core.risk_engine import OperationContext, RiskAssessment, RiskEngine, SignalResult
from core.shadow_mode import (
    ShadowMode,
    ShadowRecord,
    compute_confidence,
    get_shadow_mode,
    DEFAULT_WINDOW,
)


# ===================================================================
# Helpers
# ===================================================================

def _ctx(op="ingest", topic="general", mode="act"):
    return OperationContext(op_type=op, topic=topic, governor_mode=mode)


def _run_n_ops(shadow, n, op="ingest", topic="general", mode="act", outcome="ok"):
    """Record n operations and their outcomes."""
    for _ in range(n):
        rec = shadow.record_operation(_ctx(op, topic, mode))
        if rec:
            shadow.record_outcome(rec.op_id, outcome)


# ===================================================================
# 1. Activation / deactivation
# ===================================================================

class TestActivation:
    def test_inactive_by_default(self):
        s = ShadowMode()
        assert s.active is False

    def test_activate(self):
        s = ShadowMode()
        s.activate(window=500)
        assert s.active is True
        assert s.window == 500
        assert s.ops_recorded == 0

    def test_deactivate(self):
        s = ShadowMode()
        s.activate()
        s.deactivate()
        assert s.active is False

    def test_state_persists(self):
        s = ShadowMode()
        s.activate(window=200)
        s2 = ShadowMode()  # new instance reads state
        assert s2.active is True
        assert s2.window == 200

    def test_status(self):
        s = ShadowMode()
        s.activate(window=100)
        st = s.status()
        assert st["active"] is True
        assert st["window"] == 100
        assert st["remaining"] == 100


# ===================================================================
# 2. record_operation
# ===================================================================

class TestRecordOperation:
    def test_returns_none_when_inactive(self):
        s = ShadowMode()
        rec = s.record_operation(_ctx())
        assert rec is None

    def test_records_when_active(self):
        s = ShadowMode()
        s.activate(window=10)
        rec = s.record_operation(_ctx())
        assert rec is not None
        assert rec.op_id == 1
        assert 0.0 <= rec.risk_score <= 1.0
        assert 0.0 <= rec.confidence_score <= 1.0

    def test_increments_ops_recorded(self):
        s = ShadowMode()
        s.activate(window=10)
        s.record_operation(_ctx())
        s.record_operation(_ctx())
        assert s.ops_recorded == 2
        assert s.remaining == 8

    def test_stops_at_window(self):
        s = ShadowMode()
        s.activate(window=3)
        s.record_operation(_ctx())
        s.record_operation(_ctx())
        s.record_operation(_ctx())
        # Window complete — next record returns None
        rec = s.record_operation(_ctx())
        assert rec is None
        assert s.ops_recorded == 3

    def test_record_has_signal_breakdown(self):
        s = ShadowMode()
        s.activate(window=5)
        rec = s.record_operation(_ctx())
        assert len(rec.signal_breakdown) == 6
        names = {sig["name"] for sig in rec.signal_breakdown}
        assert "impact" in names
        assert "drift" in names

    def test_record_has_baseline_snapshot(self):
        s = ShadowMode()
        s.activate(window=5)
        rec = s.record_operation(_ctx())
        assert isinstance(rec.baseline_snapshot, dict)

    def test_jsonl_log_written(self):
        s = ShadowMode()
        s.activate(window=5)
        s.record_operation(_ctx())
        s.record_operation(_ctx())
        records = s.load_log()
        assert len(records) == 2
        assert records[0]["op_id"] == 1


# ===================================================================
# 3. record_outcome
# ===================================================================

class TestRecordOutcome:
    def test_attach_outcome(self):
        s = ShadowMode()
        s.activate(window=10)
        rec = s.record_operation(_ctx())
        assert s.record_outcome(rec.op_id, "error") is True
        assert rec.outcome == "error"

    def test_unknown_op_id(self):
        s = ShadowMode()
        s.activate(window=10)
        assert s.record_outcome(999, "ok") is False

    def test_invalid_outcome_defaults_ok(self):
        s = ShadowMode()
        s.activate(window=10)
        rec = s.record_operation(_ctx())
        s.record_outcome(rec.op_id, "BANANA")
        assert rec.outcome == "ok"

    def test_outcome_persisted_in_log(self):
        s = ShadowMode()
        s.activate(window=10)
        rec = s.record_operation(_ctx())
        s.record_outcome(rec.op_id, "retry")
        log = s.load_log()
        assert log[0]["outcome"] == "retry"


# ===================================================================
# 4. Confidence score
# ===================================================================

class TestConfidence:
    def test_identical_signals_high_confidence(self):
        signals = [
            SignalResult(name=f"s{i}", value=0.5, weight=1.0) for i in range(6)
        ]
        assessment = RiskAssessment(
            risk_score=0.5,
            risk_level="MEDIUM",
            signals=signals,
            timestamp=0,
            op_context=_ctx(),
        )
        c = compute_confidence(assessment)
        assert c == 1.0

    def test_spread_signals_low_confidence(self):
        signals = [
            SignalResult(name="a", value=0.0, weight=1.0),
            SignalResult(name="b", value=1.0, weight=1.0),
            SignalResult(name="c", value=0.0, weight=1.0),
            SignalResult(name="d", value=1.0, weight=1.0),
            SignalResult(name="e", value=0.0, weight=1.0),
            SignalResult(name="f", value=1.0, weight=1.0),
        ]
        assessment = RiskAssessment(
            risk_score=0.5,
            risk_level="MEDIUM",
            signals=signals,
            timestamp=0,
            op_context=_ctx(),
        )
        c = compute_confidence(assessment)
        assert c == 0.0  # max disagreement

    def test_confidence_in_range(self):
        s = ShadowMode()
        s.activate(window=5)
        rec = s.record_operation(_ctx())
        assert 0.0 <= rec.confidence_score <= 1.0


# ===================================================================
# 5. Statistics computation
# ===================================================================

class TestStats:
    def _make_shadow_with_records(self, n=20):
        s = ShadowMode()
        s.activate(window=n + 10)  # extra room
        for i in range(n):
            topic = "trading" if i % 5 == 0 else "general"
            outcome = "error" if i % 7 == 0 else "ok"
            rec = s.record_operation(_ctx(topic=topic))
            if rec:
                s.record_outcome(rec.op_id, outcome)
        return s

    def test_stats_structure(self):
        s = self._make_shadow_with_records(20)
        stats = s.compute_stats()
        assert "total_operations" in stats
        assert stats["total_operations"] == 20
        assert "risk_score_histogram" in stats
        assert "confidence_score_histogram" in stats
        assert "level_percentages" in stats
        assert "correlation" in stats
        assert "summary" in stats

    def test_level_percentages_sum_100(self):
        s = self._make_shadow_with_records(50)
        stats = s.compute_stats()
        total = sum(stats["level_percentages"].values())
        assert abs(total - 100.0) < 0.1

    def test_histogram_counts_match(self):
        s = self._make_shadow_with_records(30)
        stats = s.compute_stats()
        risk_total = sum(stats["risk_score_histogram"].values())
        assert risk_total == 30
        conf_total = sum(stats["confidence_score_histogram"].values())
        assert conf_total == 30

    def test_summary_fields(self):
        s = self._make_shadow_with_records(10)
        stats = s.compute_stats()
        summary = stats["summary"]
        assert "mean_risk" in summary
        assert "std_risk" in summary
        assert "mean_confidence" in summary
        assert "CRITICAL_rate" in summary
        assert "HIGH_rate" in summary
        assert "false_positive_rate" in summary

    def test_correlation_fields(self):
        s = self._make_shadow_with_records(10)
        stats = s.compute_stats()
        corr = stats["correlation"]
        assert "high_critical_count" in corr
        assert "high_critical_failures" in corr
        assert "high_critical_failure_rate" in corr
        assert "non_hc_failure_rate" in corr

    def test_empty_records(self):
        s = ShadowMode()
        stats = s.compute_stats()
        assert stats == {"error": "no records"}


# ===================================================================
# 6. Auto-report at window end
# ===================================================================

class TestAutoReport:
    def test_report_generated_at_window(self, tmp_path):
        s = ShadowMode()
        s.activate(window=5)
        _run_n_ops(s, 5)
        # Shadow should now be inactive
        assert s.active is False
        assert s.is_complete is True
        # Report file should exist
        report_path = os.path.join(str(tmp_path / "reports"), "shadow_report.json")
        assert os.path.isfile(report_path)
        with open(report_path) as f:
            report = json.load(f)
        assert report["total_operations"] == 5
        assert "summary" in report
        assert report["window"] == 5

    def test_manual_report_before_window(self, tmp_path):
        s = ShadowMode()
        s.activate(window=100)
        _run_n_ops(s, 10)
        path = s.generate_report()
        assert os.path.isfile(path)
        with open(path) as f:
            report = json.load(f)
        assert report["total_operations"] == 10


# ===================================================================
# 7. Shadow does NOT modify behaviour
# ===================================================================

class TestNoEnforcement:
    def test_high_risk_not_blocked(self):
        """
        An autopatch+trading+recover context would score HIGH/CRITICAL
        in the risk engine.  Shadow mode records it but doesn't block.
        """
        s = ShadowMode()
        s.activate(window=10)
        ctx = OperationContext(
            op_type="autopatch",
            topic="trading",
            governor_mode="recover",
            error_history=[True] * 10,
            irreversibility_flags=["writes_files"],
            exposure_flags=["api_call"],
        )
        rec = s.record_operation(ctx)
        # The record exists — shadow did not block it
        assert rec is not None
        assert rec.risk_level in ("HIGH", "CRITICAL")
        # Shadow mode is still active (did not abort anything)
        assert s.active is True


# ===================================================================
# 8. Singleton
# ===================================================================

class TestSingleton:
    def test_get_shadow_mode(self):
        s1 = get_shadow_mode()
        s2 = get_shadow_mode()
        assert s1 is s2


# ===================================================================
# 9. Edge cases
# ===================================================================

class TestEdgeCases:
    def test_window_of_one(self):
        s = ShadowMode()
        s.activate(window=1)
        rec = s.record_operation(_ctx())
        assert rec is not None
        assert s.is_complete is True
        assert s.active is False

    def test_reactivate_clears_previous(self):
        s = ShadowMode()
        s.activate(window=5)
        _run_n_ops(s, 3)
        assert s.ops_recorded == 3
        s.activate(window=10)  # re-activate
        assert s.ops_recorded == 0
        assert s.window == 10
        assert s.active is True

    def test_report_serialisable(self, tmp_path):
        s = ShadowMode()
        s.activate(window=3)
        _run_n_ops(s, 3, outcome="error")
        path = os.path.join(str(tmp_path / "reports"), "shadow_report.json")
        with open(path) as f:
            data = json.load(f)
        # Must not raise
        json.dumps(data)
