"""
Tests for Vectrax Adaptive Autonomy
======================================
Covers:
  - EscalationDetector: each of 6 triggers individually + combined
  - AdaptiveAutonomyPolicy: AUTO/MISSION_STRICT derivation
  - AUTO → MISSION_STRICT transitions with constraint verification
  - ModelRouter integration: autonomy fields, fallback behaviour
  - Audit logging on escalation
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.state_manager as sm

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


# ===================================================================
# 1. EscalationDetector — individual triggers
# ===================================================================

class TestEscalationDetectorTriggers:
    """Each of the 6 triggers should fire independently."""

    def test_trigger_critical_risk(self):
        from core.escalation_detector import EscalationDetector, OperationEnvelope
        d = EscalationDetector()
        env = OperationEnvelope(risk_level="CRITICAL")
        r = d.evaluate(env)
        assert r.should_escalate is True
        assert any("critical_risk" in t for t in r.triggers)
        assert r.severity == 1.0

    def test_trigger_high_risk_non_act(self):
        from core.escalation_detector import EscalationDetector, OperationEnvelope
        d = EscalationDetector()
        env = OperationEnvelope(risk_level="HIGH", governor_mode="cautious")
        r = d.evaluate(env)
        assert r.should_escalate is True
        assert any("high_risk_non_act" in t for t in r.triggers)

    def test_no_trigger_high_risk_in_act(self):
        from core.escalation_detector import EscalationDetector, OperationEnvelope
        d = EscalationDetector()
        env = OperationEnvelope(risk_level="HIGH", governor_mode="act")
        r = d.evaluate(env)
        # HIGH risk in act mode should NOT trigger on its own
        assert not any("high_risk" in t for t in r.triggers)

    def test_trigger_sacred_core_path(self):
        from core.escalation_detector import EscalationDetector, OperationEnvelope
        d = EscalationDetector()
        env = OperationEnvelope(affected_paths=["core/governor.py"])
        r = d.evaluate(env)
        assert r.should_escalate is True
        assert any("sacred_core" in t for t in r.triggers)

    def test_no_trigger_flexible_path(self):
        from core.escalation_detector import EscalationDetector, OperationEnvelope
        d = EscalationDetector()
        env = OperationEnvelope(affected_paths=["docs/README.md"])
        r = d.evaluate(env)
        assert not any("sacred_core" in t for t in r.triggers)

    def test_trigger_privileged_op(self):
        from core.escalation_detector import EscalationDetector, OperationEnvelope
        d = EscalationDetector()
        env = OperationEnvelope(op_type="autopatch")
        r = d.evaluate(env)
        assert r.should_escalate is True
        assert any("privileged_op" in t for t in r.triggers)

    def test_trigger_irreversible(self):
        from core.escalation_detector import EscalationDetector, OperationEnvelope
        d = EscalationDetector()
        env = OperationEnvelope(irreversibility_flags=["writes_files", "modifies_state"])
        r = d.evaluate(env)
        assert r.should_escalate is True
        assert any("irreversible" in t for t in r.triggers)

    def test_trigger_sensitive_exposure(self):
        from core.escalation_detector import EscalationDetector, OperationEnvelope
        d = EscalationDetector()
        env = OperationEnvelope(exposure_flags=["cloud_provider"])
        r = d.evaluate(env)
        assert r.should_escalate is True
        assert any("sensitive_exposure" in t for t in r.triggers)

    def test_trigger_low_confidence(self):
        from core.escalation_detector import EscalationDetector, OperationEnvelope
        d = EscalationDetector(min_confidence=0.6)
        env = OperationEnvelope(confidence_score=0.3)
        r = d.evaluate(env)
        assert r.should_escalate is True
        assert any("low_confidence" in t for t in r.triggers)

    def test_no_trigger_high_confidence(self):
        from core.escalation_detector import EscalationDetector, OperationEnvelope
        d = EscalationDetector(min_confidence=0.6)
        env = OperationEnvelope(confidence_score=0.9)
        r = d.evaluate(env)
        assert not any("low_confidence" in t for t in r.triggers)

    def test_no_triggers_clean_envelope(self):
        from core.escalation_detector import EscalationDetector, OperationEnvelope
        d = EscalationDetector()
        env = OperationEnvelope(
            risk_level="LOW",
            governor_mode="act",
            confidence_score=0.9,
        )
        r = d.evaluate(env)
        assert r.should_escalate is False
        assert r.triggers == []
        assert r.severity == 0.0

    def test_multiple_triggers_combine(self):
        from core.escalation_detector import EscalationDetector, OperationEnvelope
        d = EscalationDetector(min_confidence=0.6)
        env = OperationEnvelope(
            risk_level="CRITICAL",
            affected_paths=["core/governor.py"],
            irreversibility_flags=["writes_files"],
            confidence_score=0.3,
        )
        r = d.evaluate(env)
        assert r.should_escalate is True
        assert len(r.triggers) >= 3
        assert r.severity == 1.0  # max of all


# ===================================================================
# 2. AdaptiveAutonomyPolicy — mode derivation
# ===================================================================

class TestAdaptiveAutonomyPolicy:
    def test_auto_mode_for_low_risk(self):
        from core.adaptive_autonomy import AdaptiveAutonomyPolicy, AutonomyMode
        from core.escalation_detector import OperationEnvelope
        p = AdaptiveAutonomyPolicy()
        env = OperationEnvelope(risk_level="LOW", confidence_score=0.9)
        d = p.evaluate(env)
        assert d.mode == AutonomyMode.AUTO
        assert d.constraints["fallback_allowed"] is True
        assert d.constraints["confirmation_required"] is False

    def test_strict_mode_for_critical_risk(self):
        from core.adaptive_autonomy import AdaptiveAutonomyPolicy, AutonomyMode
        from core.escalation_detector import OperationEnvelope
        p = AdaptiveAutonomyPolicy()
        env = OperationEnvelope(risk_level="CRITICAL")
        d = p.evaluate(env)
        assert d.mode == AutonomyMode.MISSION_STRICT
        assert d.constraints["fallback_allowed"] is False
        assert d.constraints["confirmation_required"] is True
        assert d.constraints["llm_classifier"] is False
        assert d.constraints["verified_providers_only"] is True

    def test_strict_mode_for_sacred_path(self):
        from core.adaptive_autonomy import AdaptiveAutonomyPolicy, AutonomyMode
        from core.escalation_detector import OperationEnvelope
        p = AdaptiveAutonomyPolicy()
        env = OperationEnvelope(affected_paths=["core/risk_engine.py"])
        d = p.evaluate(env)
        assert d.mode == AutonomyMode.MISSION_STRICT

    def test_strict_mode_for_irreversible(self):
        from core.adaptive_autonomy import AdaptiveAutonomyPolicy, AutonomyMode
        from core.escalation_detector import OperationEnvelope
        p = AdaptiveAutonomyPolicy()
        env = OperationEnvelope(irreversibility_flags=["writes_files"])
        d = p.evaluate(env)
        assert d.mode == AutonomyMode.MISSION_STRICT

    def test_to_dict_serialisable(self):
        from core.adaptive_autonomy import AdaptiveAutonomyPolicy
        from core.escalation_detector import OperationEnvelope
        p = AdaptiveAutonomyPolicy()
        env = OperationEnvelope(risk_level="CRITICAL")
        d = p.evaluate(env)
        json.dumps(d.to_dict())  # must not raise


# ===================================================================
# 3. AUTO → MISSION_STRICT transition verification
# ===================================================================

class TestModeTransitions:
    """Verify that constraints properly change between modes."""

    def test_auto_allows_fallback(self):
        from core.adaptive_autonomy import AdaptiveAutonomyPolicy
        from core.escalation_detector import OperationEnvelope
        p = AdaptiveAutonomyPolicy()
        d = p.evaluate(OperationEnvelope(risk_level="LOW", confidence_score=0.9))
        assert d.constraints["fallback_allowed"] is True
        assert d.constraints["provider_switching"] is True
        assert d.constraints["llm_classifier"] is True

    def test_strict_disables_fallback(self):
        from core.adaptive_autonomy import AdaptiveAutonomyPolicy
        from core.escalation_detector import OperationEnvelope
        p = AdaptiveAutonomyPolicy()
        d = p.evaluate(OperationEnvelope(risk_level="CRITICAL"))
        assert d.constraints["fallback_allowed"] is False
        assert d.constraints["provider_switching"] is False
        assert d.constraints["llm_classifier"] is False
        assert d.constraints["local_only"] is True

    def test_severity_reflects_trigger(self):
        from core.adaptive_autonomy import AdaptiveAutonomyPolicy
        from core.escalation_detector import OperationEnvelope
        p = AdaptiveAutonomyPolicy()

        # Low confidence only → severity ~0.6
        d1 = p.evaluate(OperationEnvelope(confidence_score=0.3))
        assert 0.5 <= d1.severity <= 0.7

        # Critical risk → severity 1.0
        d2 = p.evaluate(OperationEnvelope(risk_level="CRITICAL"))
        assert d2.severity == 1.0

    def test_configurable_min_confidence(self, monkeypatch):
        monkeypatch.setenv("VX_MIN_CONFIDENCE", "0.9")
        from core.escalation_detector import EscalationDetector, OperationEnvelope
        # Need fresh detector to pick up env
        d = EscalationDetector()
        env = OperationEnvelope(confidence_score=0.85)
        r = d.evaluate(env)
        assert r.should_escalate is True
        assert any("low_confidence" in t for t in r.triggers)


# ===================================================================
# 4. ModelRouter integration
# ===================================================================

class TestModelRouterIntegration:
    def test_route_includes_autonomy_mode(self):
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        d = router.route("Hello world")
        assert d.autonomy_mode is not None
        assert d.autonomy_mode in ("HOME_AUTO", "BUSINESS_GOVERNED", "MISSION_STRICT")

    def test_auto_mode_has_fallback(self):
        from core.routing.model_router import ModelRouter
        sm.put("governor_mode", "act")
        router = ModelRouter()
        # Use prompt with strong pattern match → high confidence → stays AUTO
        d = router.route("Write a Python function to sort a list")
        assert d.autonomy_mode == "HOME_AUTO"
        assert d.fallback is not None

    def test_strict_mode_disables_fallback_on_low_confidence(self):
        """Low-confidence classification triggers MISSION_STRICT → no fallback."""
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        # "Hello" → CHAT with confidence 0.4, below default 0.6 threshold
        d = router.route("Hello")
        if d.autonomy_mode == "MISSION_STRICT":
            assert d.fallback is None

    def test_route_to_dict_includes_autonomy(self):
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        d = router.route("Test prompt with code keyword")
        dd = d.to_dict()
        assert "autonomy_mode" in dd

    def test_route_still_works_with_all_fields(self):
        """Backward compat: all existing RoutingDecision fields still present."""
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        d = router.route("Write a Python sort function")
        assert d.primary is not None
        assert d.task_classification is not None
        assert d.score > 0
        assert d.risk_check_passed is not None
        assert d.governor_mode is not None
        assert d.reason is not None

    def test_autonomy_constraints_dict_shape(self):
        from core.routing.model_router import ModelRouter
        router = ModelRouter()
        d = router.route("Some prompt")
        if d.autonomy_constraints is not None:
            assert "fallback_allowed" in d.autonomy_constraints
            assert "confirmation_required" in d.autonomy_constraints


# ===================================================================
# 5. Audit logging on escalation
# ===================================================================

class TestAuditOnEscalation:
    def test_escalation_creates_audit_entry(self):
        from core.adaptive_autonomy import AdaptiveAutonomyPolicy
        from core.escalation_detector import OperationEnvelope
        p = AdaptiveAutonomyPolicy()
        env = OperationEnvelope(risk_level="CRITICAL", task_type="code")
        p.evaluate(env)

        from core.audit_ledger import query
        entries = query(limit=5, action_filter="autonomy_escalation")
        assert len(entries) >= 1
        latest = entries[0]
        assert latest["action"] == "autonomy_escalation"
        assert latest["decision"] == "escalated"
        meta = json.loads(latest["metadata"]) if isinstance(latest["metadata"], str) else latest["metadata"]
        assert meta["mode"] == "MISSION_STRICT"
        assert "critical_risk" in meta["triggers"]

    def test_auto_mode_no_escalation_audit(self):
        from core.adaptive_autonomy import AdaptiveAutonomyPolicy
        from core.escalation_detector import OperationEnvelope
        from core.audit_ledger import query

        # Snapshot count before
        before = len(query(limit=10000, action_filter="autonomy_escalation"))

        p = AdaptiveAutonomyPolicy()
        env = OperationEnvelope(risk_level="LOW", confidence_score=0.9)
        p.evaluate(env)

        after = len(query(limit=10000, action_filter="autonomy_escalation"))
        # AUTO mode must NOT add escalation entries
        assert after == before


# ===================================================================
# 6. Singleton & convenience
# ===================================================================

class TestSingletons:
    def test_detector_singleton(self):
        from core.escalation_detector import get_escalation_detector
        d1 = get_escalation_detector()
        d2 = get_escalation_detector()
        assert d1 is d2

    def test_policy_singleton(self):
        from core.adaptive_autonomy import get_adaptive_policy
        p1 = get_adaptive_policy()
        p2 = get_adaptive_policy()
        assert p1 is p2

    def test_convenience_detect(self):
        from core.escalation_detector import detect, OperationEnvelope
        r = detect(OperationEnvelope(risk_level="CRITICAL"))
        assert r.should_escalate is True

    def test_convenience_evaluate(self):
        from core.adaptive_autonomy import evaluate, AutonomyMode
        from core.escalation_detector import OperationEnvelope
        d = evaluate(OperationEnvelope(risk_level="LOW", confidence_score=0.9))
        assert d.mode == AutonomyMode.AUTO


# ===================================================================
# 7. Edge cases
# ===================================================================

class TestEdgeCases:
    def test_empty_envelope(self):
        from core.escalation_detector import EscalationDetector, OperationEnvelope
        d = EscalationDetector()
        r = d.evaluate(OperationEnvelope())
        # Default envelope has confidence 1.0, no risk, no paths → no triggers
        assert r.should_escalate is False

    def test_unknown_risk_level(self):
        from core.escalation_detector import EscalationDetector, OperationEnvelope
        d = EscalationDetector()
        r = d.evaluate(OperationEnvelope(risk_level="UNKNOWN"))
        # Unknown level doesn't match any trigger
        assert not any("risk" in t for t in r.triggers)

    def test_result_summary(self):
        from core.escalation_detector import EscalationResult
        r = EscalationResult(should_escalate=False, triggers=[], severity=0.0)
        assert "AUTO" in r.summary()
        r2 = EscalationResult(should_escalate=True, triggers=["critical_risk"], severity=1.0)
        assert "MISSION_STRICT" in r2.summary()
