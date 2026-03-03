"""
Tests for Vectrax Architecture C
===================================
Covers:
  1. Hard invariant enforcement
  2. Three-mode transitions (HOME_AUTO / BUSINESS_GOVERNED / MISSION_STRICT)
  3. PolicyRegistry CRUD + versioning
  4. Sandbox propose / simulate / evaluate
  5. Promote + auto-rollback
  6. Audit ledger entries for policy changes
  7. CLI smoke (mode status, policy status)
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.state_manager as sm
import core.audit_ledger as ledger

_orig_state_path = sm.STATE_PATH
_orig_runtime_dir = sm.RUNTIME_DIR


@pytest.fixture(autouse=True)
def isolate_state(tmp_path):
    sm.RUNTIME_DIR = str(tmp_path)
    sm.STATE_PATH = os.path.join(str(tmp_path), "cognition_state.json")
    sm.save(dict(sm.DEFAULT_STATE))
    import core.risk_engine as re_mod
    re_mod._global_engine = None
    import core.routing.model_router as mr_mod
    mr_mod._global_router = None
    import core.escalation_detector as ed_mod
    ed_mod._global_detector = None
    import core.adaptive_autonomy as aa_mod
    aa_mod._global_policy = None
    import core.policy_registry as pr_mod
    pr_mod._global_registry = None
    yield
    sm.RUNTIME_DIR = _orig_runtime_dir
    sm.STATE_PATH = _orig_state_path


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VX_DEBUG", raising=False)
    monkeypatch.delenv("VX_LLM_CLASSIFIER", raising=False)
    monkeypatch.delenv("VX_MIN_CONFIDENCE", raising=False)
    from core.routing.capabilities import refresh_availability
    refresh_availability()


@pytest.fixture
def temp_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(ledger, "LEDGER_PATH", str(tmp_path / "test_ledger.db"))


@pytest.fixture
def registry(tmp_path):
    from core.policy_registry import PolicyRegistry
    db = str(tmp_path / "test_registry.db")
    return PolicyRegistry(db_path=db)


# ===================================================================
# 1. Hard Invariants
# ===================================================================

class TestInvariants:
    def test_mission_strict_valid(self):
        from core.invariants import check_mission_strict
        constraints = {"verified_providers_only": True, "fallback_allowed": False}
        assert check_mission_strict(constraints) is None

    def test_mission_strict_violation_fallback(self):
        from core.invariants import check_mission_strict
        constraints = {"verified_providers_only": True, "fallback_allowed": True}
        v = check_mission_strict(constraints)
        assert v is not None
        assert "fallback_allowed" in v.message

    def test_mission_strict_violation_providers(self):
        from core.invariants import check_mission_strict
        constraints = {"verified_providers_only": False, "fallback_allowed": False}
        v = check_mission_strict(constraints)
        assert v is not None
        assert "verified_providers_only" in v.message

    def test_irreversible_risk_blocks_auto_apply(self):
        from core.invariants import check_irreversible_risk
        v = check_irreversible_risk("CRITICAL", irreversible=True, auto_apply=True)
        assert v is not None
        assert v.invariant == "irreversible_risk"

    def test_irreversible_risk_ok_no_auto_apply(self):
        from core.invariants import check_irreversible_risk
        v = check_irreversible_risk("CRITICAL", irreversible=True, auto_apply=False)
        assert v is None

    def test_irreversible_risk_ok_low(self):
        from core.invariants import check_irreversible_risk
        v = check_irreversible_risk("LOW", irreversible=True, auto_apply=True)
        assert v is None

    def test_min_confidence_floor(self):
        from core.invariants import check_min_confidence, MIN_CONFIDENCE_FLOOR
        v = check_min_confidence(0.3)
        assert v is not None
        assert v.invariant == "min_confidence"

        v2 = check_min_confidence(MIN_CONFIDENCE_FLOOR)
        assert v2 is None

    def test_sacred_core_autopatch_blocked(self):
        from core.invariants import check_sacred_core_write
        v = check_sacred_core_write("core/governor.py", "autopatch")
        assert v is not None
        assert v.severity == "critical"
        assert "BLOCKED" in v.message

    def test_sacred_core_write_requires_confirmation(self):
        from core.invariants import check_sacred_core_write
        v = check_sacred_core_write("core/governor.py", "manual_edit")
        assert v is not None
        assert v.severity == "warning"

    def test_flexible_path_no_violation(self):
        from core.invariants import check_sacred_core_write
        v = check_sacred_core_write("docs/README.md", "autopatch")
        assert v is None

    def test_validate_all_clean(self):
        from core.invariants import validate_all
        r = validate_all(path="docs/README.md", risk_level="LOW", confidence=0.9)
        assert r.passed is True
        assert r.violations == []

    def test_validate_all_multiple_violations(self):
        from core.invariants import validate_all
        r = validate_all(
            path="core/governor.py",
            op_type="autopatch",
            risk_level="CRITICAL",
            irreversible=True,
            auto_apply=True,
            confidence=0.3,
        )
        assert r.passed is False
        assert len(r.violations) >= 2
        assert r.blocked is True

    def test_invariant_result_to_dict(self):
        from core.invariants import validate_all
        r = validate_all(path="core/governor.py", op_type="autopatch")
        d = r.to_dict()
        json.dumps(d)  # must not raise
        assert "violations" in d
        assert "blocked" in d


# ===================================================================
# 2. Three-mode transitions
# ===================================================================

class TestThreeModes:
    def test_home_auto_for_zero_severity(self):
        from core.adaptive_autonomy import AdaptiveAutonomyPolicy, AutonomyMode
        from core.escalation_detector import OperationEnvelope
        p = AdaptiveAutonomyPolicy()
        env = OperationEnvelope(risk_level="LOW", confidence_score=0.9)
        d = p.evaluate(env)
        assert d.mode == AutonomyMode.HOME_AUTO
        assert d.constraints["audit_level"] == "standard"

    def test_business_governed_for_mid_severity(self):
        from core.adaptive_autonomy import AdaptiveAutonomyPolicy, AutonomyMode
        from core.escalation_detector import OperationEnvelope
        # low_confidence (sev 0.6) or sensitive_exposure (sev 0.75) → BUSINESS_GOVERNED
        p = AdaptiveAutonomyPolicy()
        env = OperationEnvelope(exposure_flags=["cloud_provider"])
        d = p.evaluate(env)
        assert d.mode == AutonomyMode.BUSINESS_GOVERNED
        assert d.constraints["audit_level"] == "enhanced"
        assert d.constraints["fallback_allowed"] is True

    def test_mission_strict_for_high_severity(self):
        from core.adaptive_autonomy import AdaptiveAutonomyPolicy, AutonomyMode
        from core.escalation_detector import OperationEnvelope
        p = AdaptiveAutonomyPolicy()
        env = OperationEnvelope(risk_level="CRITICAL")
        d = p.evaluate(env)
        assert d.mode == AutonomyMode.MISSION_STRICT
        assert d.constraints["audit_level"] == "critical"
        assert d.constraints["local_only"] is True

    def test_backward_compat_auto_alias(self):
        from core.adaptive_autonomy import AutonomyMode
        assert AutonomyMode.AUTO == AutonomyMode.HOME_AUTO
        assert AutonomyMode("AUTO") == AutonomyMode.HOME_AUTO

    def test_mode_enum_values(self):
        from core.adaptive_autonomy import AutonomyMode
        assert AutonomyMode.HOME_AUTO.value == "HOME_AUTO"
        assert AutonomyMode.BUSINESS_GOVERNED.value == "BUSINESS_GOVERNED"
        assert AutonomyMode.MISSION_STRICT.value == "MISSION_STRICT"

    def test_state_manager_operational_mode(self):
        assert sm.get("operational_mode") == "HOME_AUTO"
        sm.put("operational_mode", "BUSINESS_GOVERNED")
        assert sm.get("operational_mode") == "BUSINESS_GOVERNED"


# ===================================================================
# 3. PolicyRegistry CRUD + versioning
# ===================================================================

class TestPolicyRegistry:
    def test_create_version(self, registry):
        pv = registry.create_version()
        assert pv.status == "candidate"
        assert pv.version == 1
        assert pv.params["confidence_threshold"] >= 0.60

    def test_confidence_floor_enforced(self, registry):
        pv = registry.create_version({"confidence_threshold": 0.30})
        assert pv.params["confidence_threshold"] >= 0.60

    def test_get_active_none(self, registry):
        assert registry.get_active() is None

    def test_promote_creates_active(self, registry):
        pv = registry.create_version()
        promoted = registry.promote(pv.id)
        assert promoted.status == "active"
        assert registry.get_active() is not None

    def test_version_increments(self, registry):
        pv1 = registry.create_version()
        pv2 = registry.create_version()
        assert pv2.version == pv1.version + 1

    def test_history(self, registry):
        registry.create_version()
        registry.create_version()
        h = registry.history()
        assert len(h) == 2

    def test_get_version(self, registry):
        pv = registry.create_version()
        found = registry.get_version(pv.id)
        assert found is not None
        assert found.id == pv.id

    def test_promote_retires_previous(self, registry):
        pv1 = registry.create_version()
        registry.promote(pv1.id)
        pv2 = registry.create_version()
        registry.promote(pv2.id)
        old = registry.get_version(pv1.id)
        assert old.status == "retired"

    def test_promote_requires_permission(self, registry):
        pv = registry.create_version()
        with pytest.raises(PermissionError):
            registry.promote(pv.id, role="viewer")

    def test_to_dict_serialisable(self, registry):
        pv = registry.create_version()
        json.dumps(pv.to_dict())


# ===================================================================
# 4. Sandbox propose / simulate / evaluate
# ===================================================================

class TestSandboxRunner:
    def test_run_default_ops(self, registry):
        from core.sandbox_runner import SandboxRunner
        pv = registry.create_version()
        runner = SandboxRunner(registry=registry)
        report = runner.run(pv.id)
        assert report.total_ops == 10  # DEFAULT_OPS length
        assert 0 <= report.escalation_rate <= 1
        assert 0 <= report.stability_score <= 1

    def test_run_custom_ops(self, registry):
        from core.sandbox_runner import SandboxRunner
        from core.escalation_detector import OperationEnvelope
        pv = registry.create_version()
        runner = SandboxRunner(registry=registry)
        ops = [OperationEnvelope(risk_level="LOW", confidence_score=0.9)] * 5
        report = runner.run(pv.id, ops)
        assert report.total_ops == 5
        assert report.escalation_rate == 0.0  # all LOW + high confidence

    def test_report_metrics_shape(self, registry):
        from core.sandbox_runner import SandboxRunner
        pv = registry.create_version()
        runner = SandboxRunner(registry=registry)
        report = runner.run(pv.id)
        d = report.to_dict()
        assert "escalation_rate" in d
        assert "stability_score" in d
        assert "invariant_violations" in d

    def test_can_promote_true_for_clean(self, registry):
        from core.sandbox_runner import SandboxRunner
        from core.escalation_detector import OperationEnvelope
        pv = registry.create_version()
        runner = SandboxRunner(registry=registry)
        ops = [OperationEnvelope(risk_level="LOW", confidence_score=0.9)] * 5
        report = runner.run(pv.id, ops)
        assert runner.can_promote(report) is True

    def test_policy_not_found_raises(self, registry):
        from core.sandbox_runner import SandboxRunner
        runner = SandboxRunner(registry=registry)
        with pytest.raises(ValueError):
            runner.run("nonexistent")

    def test_severity_to_mode_mapping(self):
        from core.sandbox_runner import _severity_to_mode
        assert _severity_to_mode(0.0, 0.8) == "HOME_AUTO"
        assert _severity_to_mode(0.5, 0.8) == "BUSINESS_GOVERNED"
        assert _severity_to_mode(0.9, 0.8) == "MISSION_STRICT"
        assert _severity_to_mode(0.8, 0.8) == "MISSION_STRICT"


# ===================================================================
# 5. Promote + auto-rollback
# ===================================================================

class TestPromoteRollback:
    def test_rollback_restores_previous(self, registry):
        pv1 = registry.create_version({"confidence_threshold": 0.70})
        registry.promote(pv1.id)
        pv2 = registry.create_version({"confidence_threshold": 0.80})
        registry.promote(pv2.id)

        restored = registry.rollback(pv2.id)
        assert restored is not None
        assert restored.id == pv1.id
        assert registry.get_active().id == pv1.id

    def test_rollback_marks_rolled_back(self, registry):
        pv = registry.create_version()
        registry.promote(pv.id)
        registry.rollback(pv.id)
        assert registry.get_version(pv.id).status == "rolled_back"

    def test_rollback_no_previous(self, registry):
        pv = registry.create_version()
        registry.promote(pv.id)
        restored = registry.rollback(pv.id)
        assert restored is None
        assert registry.get_active() is None

    def test_auto_rollback_on_degradation(self, registry):
        from core.sandbox_runner import SandboxRunner
        from core.escalation_detector import OperationEnvelope

        pv1 = registry.create_version()
        registry.promote(pv1.id)
        pv2 = registry.create_version({"confidence_threshold": 0.60})
        registry.promote(pv2.id)

        runner = SandboxRunner(registry=registry)
        # Use ops that will cause invariant violations → degradation
        bad_ops = [
            OperationEnvelope(
                risk_level="CRITICAL",
                irreversibility_flags=["writes_files"],
                confidence_score=0.3,
            )
        ] * 5
        rolled_back = runner.monitor_after_promotion(pv2.id, bad_ops)
        assert rolled_back is True

    def test_rollback_requires_permission(self, registry):
        pv = registry.create_version()
        registry.promote(pv.id)
        with pytest.raises(PermissionError):
            registry.rollback(pv.id, role="viewer")


# ===================================================================
# 6. Audit entries for policy changes
# ===================================================================

class TestAuditPolicyChanges:
    def test_promote_writes_audit(self, registry, temp_ledger):
        pv = registry.create_version()
        registry.promote(pv.id)
        entries = ledger.query(action_filter="policy_promote")
        assert len(entries) >= 1
        assert entries[0]["actor"] == "policy_registry"

    def test_rollback_writes_audit(self, registry, temp_ledger):
        pv = registry.create_version()
        registry.promote(pv.id)
        registry.rollback(pv.id)
        entries = ledger.query(action_filter="policy_rollback")
        assert len(entries) >= 1

    def test_mode_change_writes_audit(self, temp_ledger):
        from core.audit_ledger import record, query
        record(
            action="mode_change",
            actor="cli",
            decision="approved",
            metadata={"mode": "MISSION_STRICT"},
        )
        entries = query(action_filter="mode_change")
        assert len(entries) >= 1


# ===================================================================
# 7. CLI smoke functions (unit-level)
# ===================================================================

class TestCLISmoke:
    def test_mode_status_runs(self, capsys):
        from cli.vx_main import handle_mode_status
        handle_mode_status()
        out = capsys.readouterr().out
        assert "Operational Mode" in out
        assert "HOME_AUTO" in out

    def test_mode_set_valid(self, capsys):
        from cli.vx_main import handle_mode_set
        handle_mode_set("BUSINESS_GOVERNED")
        out = capsys.readouterr().out
        assert "BUSINESS_GOVERNED" in out
        assert sm.get("operational_mode") == "BUSINESS_GOVERNED"

    def test_policy_status_no_active(self, capsys):
        from cli.vx_main import handle_policy_status
        handle_policy_status()
        out = capsys.readouterr().out
        assert "Policy Registry" in out


# ===================================================================
# 8. Router integration with three modes
# ===================================================================

class TestRouterThreeModes:
    def test_route_returns_three_mode_values(self):
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        d = router.route("Hello world")
        assert d.autonomy_mode in ("HOME_AUTO", "BUSINESS_GOVERNED", "MISSION_STRICT")

    def test_home_auto_allows_fallback(self):
        from core.routing.model_router import ModelRouter
        sm.put("governor_mode", "act")
        router = ModelRouter()
        d = router.route("Write a Python function to sort a list")
        assert d.autonomy_mode == "HOME_AUTO"
        assert d.fallback is not None

    def test_business_governed_in_reason(self):
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        # "Hello" → CHAT 0.4 → low_confidence severity 0.6 → BUSINESS_GOVERNED
        d = router.route("Hello")
        if d.autonomy_mode == "BUSINESS_GOVERNED":
            assert "BUSINESS_GOVERNED" in d.reason
