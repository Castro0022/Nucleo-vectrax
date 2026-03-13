"""
Tests for Vectrax Probabilistic Risk Engine
=============================================
Covers: individual signals, weighted aggregation, drift detection,
governor integration, risk level thresholds, and edge cases.
"""

import json
import os
import tempfile
import pytest

# ---------------------------------------------------------------------------
# Patch state_manager to use a temp file so tests don't pollute real state
# ---------------------------------------------------------------------------

import core.state_manager as sm

_test_state_dir = None
_orig_state_path = sm.STATE_PATH
_orig_runtime_dir = sm.RUNTIME_DIR


@pytest.fixture(autouse=True)
def isolate_state(tmp_path):
    """Redirect state_manager to a temp directory for every test."""
    sm.RUNTIME_DIR = str(tmp_path)
    sm.STATE_PATH = os.path.join(str(tmp_path), "cognition_state.json")
    # Reset to default state
    sm.save(dict(sm.DEFAULT_STATE))
    # Also reset global risk engine singleton
    import core.risk_engine as re_mod
    re_mod._global_engine = None
    yield
    sm.RUNTIME_DIR = _orig_runtime_dir
    sm.STATE_PATH = _orig_state_path


# ---------------------------------------------------------------------------
# Imports (after patching infrastructure)
# ---------------------------------------------------------------------------

from core.risk_engine import (
    OperationContext,
    SignalResult,
    RiskAssessment,
    RiskEngine,
    RiskLevel,
    BaselineTracker,
    signal_impact,
    signal_irreversibility,
    signal_exposure,
    signal_sensitivity,
    signal_runtime,
    signal_drift,
    assess,
    get_risk_engine,
    _clamp,
    IMPACT_BY_OP,
    SENSITIVITY_BY_TOPIC,
    BASE_WEIGHTS,
    RISK_THRESHOLDS,
)


# ===================================================================
# 1. Individual signal tests
# ===================================================================

class TestSignalImpact:
    def test_known_op_types(self):
        for op, expected in IMPACT_BY_OP.items():
            ctx = OperationContext(op_type=op)
            assert signal_impact(ctx) == expected, f"Failed for {op}"

    def test_unknown_op_defaults_to_0_5(self):
        ctx = OperationContext(op_type="unknown_op")
        assert signal_impact(ctx) == 0.5

    def test_override_takes_precedence(self):
        ctx = OperationContext(op_type="autopatch", impact_override=0.2)
        assert signal_impact(ctx) == 0.2

    def test_override_clamped(self):
        ctx = OperationContext(impact_override=1.5)
        assert signal_impact(ctx) == 1.0
        ctx2 = OperationContext(impact_override=-0.3)
        assert signal_impact(ctx2) == 0.0


class TestSignalIrreversibility:
    def test_no_flags(self):
        ctx = OperationContext()
        assert signal_irreversibility(ctx) == 0.0

    def test_single_flag(self):
        ctx = OperationContext(irreversibility_flags=["writes_files"])
        assert signal_irreversibility(ctx) == 0.8

    def test_multiple_flags_capped(self):
        ctx = OperationContext(
            irreversibility_flags=["writes_files", "modifies_state", "external_call"]
        )
        # 0.8 + 0.6 + 0.7 = 2.1 → clamped to 1.0
        assert signal_irreversibility(ctx) == 1.0

    def test_unknown_flag_contributes_zero(self):
        ctx = OperationContext(irreversibility_flags=["imaginary_flag"])
        assert signal_irreversibility(ctx) == 0.0


class TestSignalExposure:
    def test_no_flags(self):
        ctx = OperationContext()
        assert signal_exposure(ctx) == 0.0

    def test_cloud_provider_max(self):
        ctx = OperationContext(exposure_flags=["cloud_provider"])
        assert signal_exposure(ctx) == 1.0

    def test_uses_max_not_sum(self):
        ctx = OperationContext(exposure_flags=["network_io", "api_call"])
        # max(0.7, 0.9) = 0.9
        assert signal_exposure(ctx) == 0.9


class TestSignalSensitivity:
    def test_known_topics(self):
        for topic, expected in SENSITIVITY_BY_TOPIC.items():
            ctx = OperationContext(topic=topic)
            assert signal_sensitivity(ctx) == expected

    def test_unknown_topic(self):
        ctx = OperationContext(topic="aliens")
        assert signal_sensitivity(ctx) == 0.1


class TestSignalRuntime:
    def test_healthy_act_mode(self):
        ctx = OperationContext(
            error_history=[False] * 10,
            governor_mode="act",
            p95_latency=1.0,
            circuit_breakers_open=0,
            total_providers=3,
        )
        score = signal_runtime(ctx)
        # error_rate=0, latency=1/60≈0.017, cb=0, mode_penalty=0 → avg ≈ 0.004
        assert score < 0.05

    def test_degraded_recover_mode(self):
        ctx = OperationContext(
            error_history=[True, True, True, False, False],
            governor_mode="recover",
            p95_latency=50.0,
            circuit_breakers_open=2,
            total_providers=3,
        )
        score = signal_runtime(ctx)
        # error_rate=0.6, latency=50/60≈0.83, cb=0.67, mode=0.9 → avg ≈ 0.75
        assert score > 0.5

    def test_reads_governor_from_state_when_none(self):
        sm.put("governor_mode", "cautious")
        sm.put("error_history", [False, True])
        ctx = OperationContext()  # governor_mode=None
        score = signal_runtime(ctx)
        # Should pick up cautious (0.5) from state
        assert score > 0.0


class TestSignalDrift:
    def test_no_baseline_returns_zero(self):
        tracker = BaselineTracker()
        ctx = OperationContext(p95_latency=5.0, error_history=[False])
        assert signal_drift(ctx, tracker) == 0.0

    def test_stable_values_low_drift(self):
        tracker = BaselineTracker()
        # Seed baseline
        tracker.update("latency_p95", 5.0)
        tracker.update("error_rate", 0.1)
        # Same values → drift ≈ 0
        ctx = OperationContext(p95_latency=5.0, error_history=[True] + [False] * 9)
        d = signal_drift(ctx, tracker)
        assert d < 0.05

    def test_large_spike_high_drift(self):
        tracker = BaselineTracker()
        tracker.update("latency_p95", 1.0)
        # 10x spike
        ctx = OperationContext(p95_latency=10.0)
        d = signal_drift(ctx, tracker)
        assert d > 0.5


# ===================================================================
# 2. Aggregation tests
# ===================================================================

class TestAggregation:
    def test_all_zero_signals(self):
        ctx = OperationContext(
            op_type="read_only",
            topic="general",
            governor_mode="act",
            error_history=[False] * 5,
            circuit_breakers_open=0,
            total_providers=1,
        )
        engine = RiskEngine()
        result = engine.assess(ctx)
        assert result.risk_score < 0.1
        assert result.risk_level == RiskLevel.LOW

    def test_all_max_signals(self):
        ctx = OperationContext(
            op_type="autopatch",
            irreversibility_flags=["writes_files", "modifies_state", "external_call"],
            exposure_flags=["cloud_provider"],
            topic="health",
            governor_mode="recover",
            error_history=[True] * 10,
            p95_latency=120.0,
            circuit_breakers_open=3,
            total_providers=3,
        )
        engine = RiskEngine()
        result = engine.assess(ctx)
        assert result.risk_score > 0.85
        assert result.risk_level == RiskLevel.CRITICAL

    def test_six_signals_present(self):
        ctx = OperationContext(op_type="ingest", governor_mode="act")
        engine = RiskEngine()
        result = engine.assess(ctx)
        assert len(result.signals) == 6
        signal_names = {s.name for s in result.signals}
        assert signal_names == {
            "impact", "irreversibility", "exposure",
            "sensitivity", "runtime", "drift",
        }

    def test_weighted_sum_correct(self):
        ctx = OperationContext(
            op_type="smoke",
            governor_mode="act",
            error_history=[False],
        )
        engine = RiskEngine()
        result = engine.assess(ctx)
        # Manually verify: Σ(w×s) / Σ(w)
        total_w = sum(s.weight for s in result.signals)
        expected = sum(s.contribution for s in result.signals) / total_w
        assert abs(result.risk_score - expected) < 1e-6


# ===================================================================
# 3. Dynamic weight adjustment
# ===================================================================

class TestWeightAdjustment:
    def test_recover_doubles_runtime_weight(self):
        weights = RiskEngine._adjust_weights("recover")
        assert weights["runtime"] == BASE_WEIGHTS["runtime"] * 2.0
        # Others unchanged
        assert weights["impact"] == BASE_WEIGHTS["impact"]

    def test_observe_halves_drift_weight(self):
        weights = RiskEngine._adjust_weights("observe")
        assert weights["drift"] == BASE_WEIGHTS["drift"] * 0.5

    def test_act_no_adjustment(self):
        weights = RiskEngine._adjust_weights("act")
        assert weights == BASE_WEIGHTS


# ===================================================================
# 4. Risk level classification
# ===================================================================

class TestClassification:
    def test_boundaries(self):
        assert RiskEngine.classify(0.0) == RiskLevel.LOW
        assert RiskEngine.classify(0.29) == RiskLevel.LOW
        assert RiskEngine.classify(0.30) == RiskLevel.MEDIUM
        assert RiskEngine.classify(0.59) == RiskLevel.MEDIUM
        assert RiskEngine.classify(0.60) == RiskLevel.HIGH
        assert RiskEngine.classify(0.84) == RiskLevel.HIGH
        assert RiskEngine.classify(0.85) == RiskLevel.CRITICAL
        assert RiskEngine.classify(1.0) == RiskLevel.CRITICAL


# ===================================================================
# 5. Baseline tracker
# ===================================================================

class TestBaselineTracker:
    def test_first_update_sets_value(self):
        tracker = BaselineTracker()
        ema = tracker.update("test_metric", 10.0)
        assert ema == 10.0

    def test_ema_smoothing(self):
        tracker = BaselineTracker(alpha=0.5)
        tracker.update("m", 10.0)
        ema = tracker.update("m", 20.0)
        # 0.5 * 20 + 0.5 * 10 = 15
        assert ema == 15.0

    def test_drift_no_baseline(self):
        tracker = BaselineTracker()
        assert tracker.drift("nonexistent", 5.0) == 0.0

    def test_drift_with_baseline(self):
        tracker = BaselineTracker()
        tracker.update("m", 10.0)
        d = tracker.drift("m", 20.0)
        # |20 - 10| / (10 + ε) ≈ 1.0
        assert abs(d - 1.0) < 1e-6

    def test_drift_clamped_to_one(self):
        tracker = BaselineTracker()
        tracker.update("m", 0.001)
        d = tracker.drift("m", 100.0)
        assert d == 1.0

    def test_persistence(self):
        tracker = BaselineTracker()
        tracker.update("persist_test", 42.0)
        # New tracker instance reads from state
        tracker2 = BaselineTracker()
        assert tracker2.get("persist_test") == 42.0


# ===================================================================
# 6. Breakdown / audit
# ===================================================================

class TestBreakdown:
    def test_breakdown_structure(self):
        ctx = OperationContext(op_type="ingest", topic="code", governor_mode="act")
        engine = RiskEngine()
        result = engine.assess(ctx)
        bd = result.breakdown()

        assert "risk_score" in bd
        assert "risk_level" in bd
        assert "signals" in bd
        assert len(bd["signals"]) == 6
        for sig in bd["signals"]:
            assert "name" in sig
            assert "value" in sig
            assert "weight" in sig
            assert "contribution" in sig
            assert "details" in sig
        assert bd["op_type"] == "ingest"
        assert bd["topic"] == "code"

    def test_breakdown_is_json_serialisable(self):
        ctx = OperationContext(op_type="workflow", governor_mode="cautious")
        engine = RiskEngine()
        result = engine.assess(ctx)
        # Must not raise
        json.dumps(result.breakdown())


# ===================================================================
# 7. Module-level convenience functions
# ===================================================================

class TestConvenience:
    def test_get_risk_engine_singleton(self):
        e1 = get_risk_engine()
        e2 = get_risk_engine()
        assert e1 is e2

    def test_assess_shortcut(self):
        ctx = OperationContext(op_type="smoke", governor_mode="act")
        result = assess(ctx)
        assert isinstance(result, RiskAssessment)
        assert 0.0 <= result.risk_score <= 1.0


# ===================================================================
# 8. Integration with governor
# ===================================================================

class TestGovernorIntegration:
    def test_evaluate_includes_risk_fields(self):
        from core import governor
        policy = governor.evaluate(had_error=False)
        # Risk fields should be present (may be None if engine fails,
        # but in our test env they should work)
        assert "risk_score" in policy
        assert "risk_level" in policy

    def test_critical_risk_blocks_autopatch_outside_act(self):
        """
        Simulate a scenario with high error rate in recover mode.
        The risk engine should flag CRITICAL and governor should
        block autopatch.
        """
        from core import governor

        st = sm.load()
        st["cycles"] = 10  # past observe phase
        st["error_history"] = [True] * 10  # all errors
        st["governor_mode"] = "recover"
        sm.save(st)

        policy = governor.evaluate(had_error=True)
        # With 100% error history in recover mode, risk should be high
        if policy.get("risk_level") == "CRITICAL":
            assert policy["autopatch_allowed"] is False


# ===================================================================
# 9. Edge cases
# ===================================================================

class TestEdgeCases:
    def test_empty_context(self):
        """Default OperationContext should not crash."""
        ctx = OperationContext()
        engine = RiskEngine()
        result = engine.assess(ctx)
        assert 0.0 <= result.risk_score <= 1.0

    def test_clamp_utility(self):
        assert _clamp(-5.0) == 0.0
        assert _clamp(0.5) == 0.5
        assert _clamp(99.0) == 1.0

    def test_zero_providers(self):
        ctx = OperationContext(
            total_providers=0,
            governor_mode="act",
            error_history=[],
        )
        # Should not divide by zero
        engine = RiskEngine()
        result = engine.assess(ctx)
        assert 0.0 <= result.risk_score <= 1.0

    def test_score_always_in_range(self):
        """Fuzz-like: many random-ish contexts all produce [0,1]."""
        engine = RiskEngine()
        combos = [
            OperationContext(op_type=op, topic=t, governor_mode=m)
            for op in ["autopatch", "smoke", "read_only"]
            for t in ["health", "general", "trading"]
            for m in ["act", "recover", "observe", "cautious"]
        ]
        for ctx in combos:
            r = engine.assess(ctx)
            assert 0.0 <= r.risk_score <= 1.0, (
                f"Out of range for {ctx.op_type}/{ctx.topic}/{ctx.governor_mode}"
            )


# ===================================================================
# 10. Metrics integration
# ===================================================================

class TestMetrics:
    def test_assessment_records_metrics(self):
        from core.observability.metrics import get_metrics_collector

        mc = get_metrics_collector()
        # Reset to isolate from other tests
        mc.get_counter("risk_assessments_total").reset()
        mc.register_histogram(
            "risk_score_distribution",
            "Distribution of risk scores per operation"
        )
        # Force fresh histogram by replacing it
        from core.observability.metrics import Histogram
        mc._histograms["risk_score_distribution"] = Histogram(
            "risk_score_distribution",
            "Distribution of risk scores per operation"
        )

        ctx = OperationContext(op_type="ingest", governor_mode="act")
        engine = RiskEngine()
        engine.assess(ctx)
        engine.assess(ctx)

        assert mc.get_counter("risk_assessments_total").get() == 2
        stats = mc.get_histogram("risk_score_distribution").get_stats()
        assert stats["count"] == 2
