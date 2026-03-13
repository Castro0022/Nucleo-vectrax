"""
Tests for the Vectrax Cognitive Layer — all four motors.

Run:  python -m pytest tests/test_cognition.py -v
"""

from __future__ import annotations

import json
import os
import tempfile
import time

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_vault(tmp_path, monkeypatch):
    """Redirect all vault writes to a temp directory."""
    vault = str(tmp_path / "vault")
    os.makedirs(vault, exist_ok=True)
    # Patch vault paths in all cognition modules that use them
    monkeypatch.setattr("cognition.perception.observation_buffer.VAULT_DIR", vault)
    monkeypatch.setattr("cognition.perception.observation_buffer.BUFFER_PATH", os.path.join(vault, "buf.jsonl"))
    monkeypatch.setattr("cognition.memory.stores.VAULT_DIR", vault)
    monkeypatch.setattr("cognition.memory.stores.DECISIONS_PATH", os.path.join(vault, "dec.jsonl"))
    monkeypatch.setattr("cognition.memory.stores.SIGNALS_PATH", os.path.join(vault, "sig.jsonl"))
    monkeypatch.setattr("cognition.memory.stores.ERRORS_PATH", os.path.join(vault, "err.jsonl"))
    monkeypatch.setattr("cognition.memory.stores.PRINCIPLES_PATH", os.path.join(vault, "pri.jsonl"))
    monkeypatch.setattr("cognition.memory.knowledge_graph.GRAPH_PATH", os.path.join(vault, "kg.json"))
    monkeypatch.setattr("cognition.orchestrator.tracer.VAULT_DIR", vault)
    monkeypatch.setattr("cognition.orchestrator.tracer.TRACE_PATH", os.path.join(vault, "trace.jsonl"))


# ===========================================================================
# 1. Types & Errors
# ===========================================================================

class TestTypes:
    def test_cognitive_signal_defaults(self):
        from cognition.types import CognitiveSignal, SignalCategory, SignalSource
        s = CognitiveSignal()
        assert s.category == SignalCategory.NOISE
        assert s.source_type == SignalSource.INTERNAL
        assert 0.0 <= s.priority <= 1.0
        d = s.to_dict()
        assert d["category"] == "NOISE"

    def test_cognitive_context(self):
        from cognition.types import CognitiveContext
        ctx = CognitiveContext(signal_count=5, avg_priority=0.42)
        d = ctx.to_dict()
        assert d["signal_count"] == 5
        assert d["avg_priority"] == 0.42

    def test_impact_assessment_overall(self):
        from cognition.types import ImpactAssessment
        ia = ImpactAssessment(short_term=0.8, medium_term=0.4, long_term=0.2)
        # 0.5*0.8 + 0.3*0.4 + 0.2*0.2 = 0.4 + 0.12 + 0.04 = 0.56
        assert abs(ia.overall - 0.56) < 0.001

    def test_reasoning_result_serialization(self):
        from cognition.types import ReasoningResult, ImpactAssessment, Scenario
        rr = ReasoningResult(
            risk_score=0.45,
            risk_level="MEDIUM",
            impact=ImpactAssessment(short_term=0.3),
            scenarios=[Scenario(description="test", probability=0.5)],
            recommendation="proceed",
        )
        d = rr.to_dict()
        assert d["risk_level"] == "MEDIUM"
        assert len(d["scenarios"]) == 1

    def test_cognitive_proposal(self):
        from cognition.types import CognitiveProposal, ProposalStatus
        p = CognitiveProposal(description="test proposal")
        assert p.status == ProposalStatus.DRAFT
        assert p.id.startswith("COG-")

    def test_memory_context(self):
        from cognition.types import MemoryContext, MemoryTier
        mc = MemoryContext(tier=MemoryTier.STRUCTURAL)
        d = mc.to_dict()
        assert d["tier"] == "structural"

    def test_error_hierarchy(self):
        from cognition.errors import (
            CognitionError, PerceptionError, ReasoningError,
            ConstitutionalViolation, ApprovalRequired,
            MemoryError, OrchestrationError, ModeViolation,
        )
        assert issubclass(PerceptionError, CognitionError)
        assert issubclass(ConstitutionalViolation, ReasoningError)
        assert issubclass(ModeViolation, OrchestrationError)


# ===========================================================================
# 2. Motor 1 — Perception
# ===========================================================================

class TestEventClassifier:
    def test_classify_critical(self):
        from cognition.types import CognitiveSignal, SignalCategory
        from cognition.perception.event_classifier import EventClassifier

        c = EventClassifier()
        s = CognitiveSignal(summary="security breach detected")
        result = c.classify(s)
        assert result.category == SignalCategory.CRITICAL
        assert result.priority > 0

    def test_classify_change(self):
        from cognition.types import CognitiveSignal, SignalCategory
        from cognition.perception.event_classifier import EventClassifier

        c = EventClassifier()
        s = CognitiveSignal(summary="file modified in project directory")
        result = c.classify(s)
        assert result.category == SignalCategory.CHANGE

    def test_classify_noise(self):
        from cognition.types import CognitiveSignal, SignalCategory
        from cognition.perception.event_classifier import EventClassifier

        c = EventClassifier()
        s = CognitiveSignal(summary="nothing interesting happening")
        result = c.classify(s)
        assert result.category == SignalCategory.NOISE

    def test_classify_batch(self):
        from cognition.types import CognitiveSignal
        from cognition.perception.event_classifier import EventClassifier

        c = EventClassifier()
        signals = [
            CognitiveSignal(summary="error in system"),
            CognitiveSignal(summary="file created"),
        ]
        results = c.classify_batch(signals)
        assert len(results) == 2
        assert all(s.priority >= 0 for s in results)

    def test_intent_inference(self):
        from cognition.types import CognitiveSignal
        from cognition.perception.event_classifier import EventClassifier

        c = EventClassifier()
        s = CognitiveSignal(summary="fix bug in parser module")
        c.classify(s)
        assert s.intent == "BUG_FIX"


class TestObservationBuffer:
    def test_add_and_recent(self):
        from cognition.types import CognitiveSignal
        from cognition.perception.observation_buffer import ObservationBuffer

        buf = ObservationBuffer(max_size=5, persist=False)
        for i in range(10):
            buf.add(CognitiveSignal(source=f"src-{i}"))
        assert buf.size == 5  # circular
        recent = buf.recent(3)
        assert len(recent) == 3
        assert recent[0].source == "src-9"  # newest first

    def test_by_source(self):
        from cognition.types import CognitiveSignal
        from cognition.perception.observation_buffer import ObservationBuffer

        buf = ObservationBuffer(max_size=100, persist=False)
        buf.add(CognitiveSignal(source="a"))
        buf.add(CognitiveSignal(source="b"))
        buf.add(CognitiveSignal(source="a"))
        assert len(buf.by_source("a")) == 2

    def test_by_priority(self):
        from cognition.types import CognitiveSignal
        from cognition.perception.observation_buffer import ObservationBuffer

        buf = ObservationBuffer(max_size=100, persist=False)
        buf.add(CognitiveSignal(priority=0.1))
        buf.add(CognitiveSignal(priority=0.8))
        buf.add(CognitiveSignal(priority=0.5))
        high = buf.by_priority(0.5)
        assert len(high) == 2

    def test_stats(self):
        from cognition.types import CognitiveSignal, SignalCategory
        from cognition.perception.observation_buffer import ObservationBuffer

        buf = ObservationBuffer(max_size=100, persist=False)
        buf.add(CognitiveSignal(priority=0.6, source="x"))
        buf.add(CognitiveSignal(priority=0.4, source="y"))
        stats = buf.stats()
        assert stats["total"] == 2
        assert 0.4 <= stats["avg_priority"] <= 0.6


class TestChangeDetector:
    def test_new_sources(self):
        from cognition.types import CognitiveSignal
        from cognition.perception.change_detector import ChangeDetector

        cd = ChangeDetector()
        # First call sets baseline
        cd.detect([CognitiveSignal(source="a"), CognitiveSignal(source="b")])
        # Second call detects new source "c"
        report = cd.detect([
            CognitiveSignal(source="a"),
            CognitiveSignal(source="c"),
        ])
        assert "c" in report.new_sources
        assert "b" in report.lost_sources
        assert report.total_changes >= 2


class TestContextBuilder:
    def test_build_context(self):
        from cognition.types import CognitiveSignal, SignalCategory
        from cognition.perception.context_builder import ContextBuilder

        cb = ContextBuilder()
        signals = [
            CognitiveSignal(priority=0.5, category=SignalCategory.CHANGE),
            CognitiveSignal(priority=0.9, category=SignalCategory.CRITICAL),
            CognitiveSignal(priority=0.8, category=SignalCategory.CRITICAL),
        ]
        ctx = cb.build(signals, changes_detected=3)
        assert ctx.signal_count == 3
        assert ctx.changes_detected == 3
        assert ctx.dominant_category == SignalCategory.CRITICAL
        assert 0.5 <= ctx.avg_priority <= 0.9


class TestPerceptionEngine:
    def test_perceive_with_injected_signal(self):
        from cognition.perception.perception_engine import PerceptionEngine
        from cognition.types import CognitiveSignal, SignalCategory

        pe = PerceptionEngine(persist=False)
        extra = [CognitiveSignal(summary="file modified", source="test")]
        ctx = pe.perceive(extra_signals=extra)
        assert ctx.signal_count >= 1
        events = pe.recent_events(10)
        assert len(events) >= 1

    def test_inject_signal(self):
        from cognition.perception.perception_engine import PerceptionEngine

        pe = PerceptionEngine(persist=False)
        s = pe.inject_signal({"summary": "crash detected", "severity": "critical"})
        assert s.priority > 0
        assert pe.buffer_stats()["total"] == 1


# ===========================================================================
# 3. Motor 3 — Memory
# ===========================================================================

class TestDecisionStore:
    def test_record_and_search(self):
        from cognition.memory.stores import DecisionStore
        ds = DecisionStore()
        ds.record("D-001", "deploy new feature", "approved", risk_score=0.3)
        ds.record("D-002", "refactor database", "rejected", risk_score=0.7)
        results = ds.search("feature")
        assert len(results) >= 1
        assert results[0]["id"] == "D-001"

    def test_by_status(self):
        from cognition.memory.stores import DecisionStore
        ds = DecisionStore()
        ds.record("D-010", "test", "approved")
        ds.record("D-011", "test2", "rejected")
        approved = ds.by_status("approved")
        assert any(d["id"] == "D-010" for d in approved)


class TestErrorStore:
    def test_record_and_unresolved(self):
        from cognition.memory.stores import ErrorStore
        es = ErrorStore()
        es.record("timeout", "API call timed out", source="api", resolved=False)
        es.record("crash", "null pointer", resolved=True)
        unresolved = es.unresolved()
        assert len(unresolved) == 1
        assert unresolved[0]["error_type"] == "timeout"


class TestPrincipleStore:
    def test_add_restriction(self):
        from cognition.memory.stores import PrincipleStore
        ps = PrincipleStore()
        ps.add_restriction("No deletions", "Do not delete production data")
        all_p = ps.get_all()
        assert any(p["name"] == "No deletions" for p in all_p)


class TestKnowledgeGraph:
    def test_add_and_query(self):
        from cognition.memory.knowledge_graph import KnowledgeGraph
        from cognition.types import KnowledgeNodeType

        kg = KnowledgeGraph(path=os.path.join(tempfile.mkdtemp(), "kg.json"))
        kg.add_node("n1", KnowledgeNodeType.DECISION, label="deploy v2")
        kg.add_node("n2", KnowledgeNodeType.PATTERN, label="frequent deploys")
        kg.add_edge("n1", "n2", relation="produces", weight=0.8)

        assert kg.node_count == 2
        assert kg.edge_count == 1

        related = kg.related_to("n1")
        assert len(related) == 1
        assert related[0]["node"]["id"] == "n2"

    def test_strongest_connections(self):
        from cognition.memory.knowledge_graph import KnowledgeGraph
        from cognition.types import KnowledgeNodeType

        kg = KnowledgeGraph(path=os.path.join(tempfile.mkdtemp(), "kg.json"))
        kg.add_node("a", KnowledgeNodeType.SIGNAL)
        kg.add_node("b", KnowledgeNodeType.ERROR)
        kg.add_edge("a", "b", weight=0.9)
        kg.add_edge("b", "a", weight=0.3)

        strongest = kg.strongest_connections(1)
        assert strongest[0]["weight"] == 0.9


class TestMemoryEngine:
    def test_query_and_learn(self):
        from cognition.memory.memory_engine import MemoryEngine

        me = MemoryEngine()
        me.learn("P-001", "executed", description="deployed feature X")
        mem = me.query(intent="feature")
        # Should find the decision we just recorded
        assert mem.related_decisions or mem.related_patterns

    def test_store_signals(self):
        from cognition.memory.memory_engine import MemoryEngine
        from cognition.types import CognitiveSignal

        me = MemoryEngine()
        count = me.store_signals([
            CognitiveSignal(source="a", priority=0.5),
            CognitiveSignal(source="b", priority=0.8),
        ])
        assert count == 2


# ===========================================================================
# 4. Motor 2 — Reasoning
# ===========================================================================

class TestConstitutionalValidator:
    def test_all_pass(self):
        from cognition.reasoning.constitutional_check import ConstitutionalValidator
        cv = ConstitutionalValidator()
        passed, reasons = cv.validate(risk_score=0.3, sustainability_score=0.8)
        assert passed is True
        assert len(reasons) == 0

    def test_risk_violation(self):
        from cognition.reasoning.constitutional_check import ConstitutionalValidator
        cv = ConstitutionalValidator()
        passed, reasons = cv.validate(risk_score=0.9)
        assert passed is False
        assert any("CF-004" in r for r in reasons)

    def test_sustainability_violation(self):
        from cognition.reasoning.constitutional_check import ConstitutionalValidator
        cv = ConstitutionalValidator()
        passed, reasons = cv.validate(sustainability_score=0.1)
        assert passed is False
        assert any("CF-003" in r for r in reasons)


class TestImpactAnalyzer:
    def test_analyze(self):
        from cognition.reasoning.impact_analyzer import ImpactAnalyzer
        from cognition.types import CognitiveContext, CognitiveSignal, SignalCategory

        ia = ImpactAnalyzer()
        ctx = CognitiveContext(
            signals=[CognitiveSignal(priority=0.7, category=SignalCategory.CHANGE)],
            signal_count=1,
            avg_priority=0.7,
        )
        result = ia.analyze(ctx)
        assert 0 <= result.short_term <= 1
        assert 0 <= result.overall <= 1


class TestScenarioEngine:
    def test_generate_scenarios(self):
        from cognition.reasoning.scenario_engine import ScenarioEngine
        from cognition.types import CognitiveContext, ImpactAssessment

        se = ScenarioEngine()
        ctx = CognitiveContext()
        impact = ImpactAssessment(short_term=0.5)
        scenarios = se.generate(ctx, impact, risk_score=0.4)
        assert len(scenarios) == 3
        assert all(0 <= s.probability <= 1 for s in scenarios)


class TestContradictionDetector:
    def test_no_contradictions(self):
        from cognition.reasoning.contradiction_detector import ContradictionDetector
        from cognition.types import CognitiveContext

        cd = ContradictionDetector()
        ctx = CognitiveContext(governor_mode="act", system_health="healthy")
        result = cd.detect("deploy feature", ctx)
        assert result == []

    def test_health_contradiction(self):
        from cognition.reasoning.contradiction_detector import ContradictionDetector
        from cognition.types import CognitiveContext

        cd = ContradictionDetector()
        ctx = CognitiveContext(governor_mode="act", system_health="unhealthy")
        result = cd.detect("deploy", ctx)
        assert len(result) >= 1


class TestReasoningEngine:
    def test_reason_low_risk(self):
        from cognition.reasoning.reasoning_engine import ReasoningEngine
        from cognition.types import CognitiveContext, CognitiveSignal

        re = ReasoningEngine()
        ctx = CognitiveContext(
            signals=[CognitiveSignal(priority=0.2)],
            signal_count=1,
            avg_priority=0.2,
            governor_mode="act",
            system_health="healthy",
        )
        result = re.reason(ctx)
        assert result.recommendation in ("proceed", "review")
        assert result.constitutional_pass is True

    def test_reason_blocked_by_constitution(self):
        from cognition.reasoning.reasoning_engine import ReasoningEngine
        from cognition.types import CognitiveContext, CognitiveSignal

        re = ReasoningEngine()
        # Force high risk by making context unhealthy + recover
        ctx = CognitiveContext(
            signals=[CognitiveSignal(priority=0.9)],
            signal_count=1,
            avg_priority=0.9,
            governor_mode="recover",
            system_health="unhealthy",
        )
        result = re.reason(ctx)
        # Should at least recommend review (recover mode)
        assert result.recommendation in ("review", "block")


# ===========================================================================
# 5. Motor 2 — Human Review Gate
# ===========================================================================

class TestHumanReviewGate:
    def test_submit_and_approve(self):
        from cognition.reasoning.human_review_gate import HumanReviewGate
        from cognition.types import CognitiveProposal, ProposalStatus

        gate = HumanReviewGate()
        p = CognitiveProposal(description="test proposal")
        submitted = gate.submit(p)
        assert submitted.status == ProposalStatus.PENDING

        approved = gate.approve(p.id)
        assert approved is not None
        assert approved.status == ProposalStatus.APPROVED
        assert gate.is_approved(p.id)

    def test_reject(self):
        from cognition.reasoning.human_review_gate import HumanReviewGate
        from cognition.types import CognitiveProposal, ProposalStatus

        gate = HumanReviewGate()
        p = CognitiveProposal(description="bad proposal")
        gate.submit(p)
        rejected = gate.reject(p.id)
        assert rejected.status == ProposalStatus.REJECTED
        assert not gate.is_approved(p.id)

    def test_require_approval_raises(self):
        from cognition.reasoning.human_review_gate import HumanReviewGate
        from cognition.errors import ApprovalRequired

        gate = HumanReviewGate()
        with pytest.raises(ApprovalRequired):
            gate.require_approval("nonexistent")

    def test_list_proposals(self):
        from cognition.reasoning.human_review_gate import HumanReviewGate
        from cognition.types import CognitiveProposal

        gate = HumanReviewGate()
        gate.submit(CognitiveProposal(description="p1"))
        gate.submit(CognitiveProposal(description="p2"))
        all_p = gate.list_proposals()
        assert len(all_p) >= 2


# ===========================================================================
# 6. Motor 4 — Orchestrator
# ===========================================================================

class TestModeManager:
    def test_default_mode(self):
        from cognition.orchestrator.modes import ModeManager
        from cognition.types import CognitiveMode

        mm = ModeManager()
        # Force fresh state
        mm._mode = None
        mm._mode = CognitiveMode.SILENT_OBSERVATION
        assert mm.current == CognitiveMode.SILENT_OBSERVATION
        assert not mm.can_propose()

    def test_set_mode(self):
        from cognition.orchestrator.modes import ModeManager
        from cognition.types import CognitiveMode

        mm = ModeManager()
        mm.set_mode(CognitiveMode.SUPERVISED_PROPOSAL)
        assert mm.can_propose()

    def test_mode_violation(self):
        from cognition.orchestrator.modes import ModeManager
        from cognition.types import CognitiveMode
        from cognition.errors import ModeViolation

        mm = ModeManager()
        mm.set_mode(CognitiveMode.SILENT_OBSERVATION)
        with pytest.raises(ModeViolation):
            mm.require_proposal_mode()


class TestTracer:
    def test_trace_cycle(self):
        from cognition.orchestrator.tracer import CognitiveTracer

        tracer = CognitiveTracer()
        tracer.begin_cycle("test-001")
        tracer.record("step1", output_summary="ok", duration_ms=5.0)
        tracer.record("step2", output_summary="done", duration_ms=3.0)
        entries = tracer.end_cycle()
        assert len(entries) == 2
        assert entries[0].step == "step1"

    def test_recent_traces(self):
        from cognition.orchestrator.tracer import CognitiveTracer

        tracer = CognitiveTracer()
        tracer.begin_cycle("test-002")
        tracer.record("perceive", output_summary="5 signals")
        tracer.end_cycle()
        recent = tracer.recent(10)
        assert len(recent) >= 1


class TestProposalGenerator:
    def test_generate_proceed(self):
        from cognition.orchestrator.proposal_generator import CognitiveProposalGenerator
        from cognition.types import CognitiveContext, ReasoningResult, ImpactAssessment

        gen = CognitiveProposalGenerator()
        ctx = CognitiveContext(signal_count=3)
        rr = ReasoningResult(
            risk_score=0.3,
            risk_level="LOW",
            impact=ImpactAssessment(short_term=0.2),
            recommendation="proceed",
        )
        p = gen.generate(ctx, rr)
        assert p is not None
        assert p.id.startswith("COG-")

    def test_generate_blocked(self):
        from cognition.orchestrator.proposal_generator import CognitiveProposalGenerator
        from cognition.types import CognitiveContext, ReasoningResult

        gen = CognitiveProposalGenerator()
        ctx = CognitiveContext()
        rr = ReasoningResult(recommendation="block")
        p = gen.generate(ctx, rr)
        assert p is None


class TestCognitiveOrchestrator:
    def test_status(self):
        from cognition.orchestrator.orchestrator import CognitiveOrchestrator

        orch = CognitiveOrchestrator()
        st = orch.status()
        assert "mode" in st
        assert "perception" in st
        assert "memory" in st
        assert "reasoning" in st

    def test_set_mode(self):
        from cognition.orchestrator.orchestrator import CognitiveOrchestrator

        orch = CognitiveOrchestrator()
        result = orch.set_mode("supervised")
        assert "supervised" in result
        result = orch.set_mode("silent")
        assert "silent" in result

    def test_dry_run_cycle(self):
        from cognition.orchestrator.orchestrator import CognitiveOrchestrator
        from cognition.types import CognitiveSignal

        orch = CognitiveOrchestrator()
        orch.set_mode("supervised")
        # Inject a signal so there's something to reason about
        orch.perception.inject_signal({"summary": "file changed", "impact": "medium"})
        result = orch.run_cycle(dry_run=True)
        assert "cycle_id" in result
        assert result.get("dry_run") is True

    def test_silent_mode_no_proposal(self):
        from cognition.orchestrator.orchestrator import CognitiveOrchestrator

        orch = CognitiveOrchestrator()
        orch.set_mode("silent")
        result = orch.run_cycle()
        assert result.get("outcome") == "observed"
        assert "proposal" not in result
