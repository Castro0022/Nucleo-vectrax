"""
Tests para core.router_learning_cycle — Ciclo de Aprendizaje Continuo
=====================================================================

Cubre:
  1. Activación / desactivación del ciclo
  2. Detección de conflictos semántico vs regex
  3. Análisis de pesos entre capas
  4. Detección de memory gaps
  5. Generación de propuestas
  6. Persistencia de propuestas
  7. Integración con SmartRouter (no rompe nada)
"""

import json
import os
import time

import pytest

from core.router_learning import (
    RouterDecisionLedger,
    RouterFeedback,
    RouteQualityScore,
)
from core.router_learning_cycle import (
    LearningProposal,
    RouterLearningCycle,
    get_learning_cycle,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_data(tmp_path):
    """Create temp paths for ledger + proposals."""
    return {
        "ledger": str(tmp_path / "feedback.jsonl"),
        "proposals": str(tmp_path / "proposals.jsonl"),
    }


@pytest.fixture
def cycle(tmp_data, monkeypatch):
    """RouterLearningCycle with temp storage and isolated state."""
    import core.router_learning_cycle as rlc
    import core.router_learning as rl
    from core import state_manager

    monkeypatch.setattr(rlc, "PROPOSALS_PATH", tmp_data["proposals"])
    monkeypatch.setattr(rl, "LEDGER_PATH", tmp_data["ledger"])
    rl._ledger = None  # reset singleton

    # Isolate state: reset learning-related keys
    state = state_manager.load()
    state.pop("router_learning_mode", None)
    state.pop("router_learning_activated_at", None)
    state.pop("router_learning_cycles", None)
    state_manager.save(state)

    c = RouterLearningCycle()
    return c


def _fb(**kwargs) -> RouterFeedback:
    defaults = {
        "intent": "online",
        "confidence": 0.8,
        "topic": "general",
        "strategy": "resolve_online",
        "risk_level": "LOW",
        "success": True,
        "classification_method": "semantic",
    }
    defaults.update(kwargs)
    return RouterFeedback(**defaults)


def _populate(ledger_path, n=20):
    """Populate ledger with mixed data for testing."""
    ledger = RouterDecisionLedger(path=ledger_path)
    # Semantic successes
    for i in range(8):
        ledger.append(_fb(
            intent="memory", confidence=0.9, classification_method="semantic",
            success=True, quality_score="correct_route",
        ))
    # Regex fallback LOCAL successes
    for i in range(4):
        ledger.append(_fb(
            intent="local", confidence=0.8, classification_method="regex_fallback",
            success=True, quality_score="correct_route",
        ))
    # Online with conflict (semantic would have said local)
    for i in range(4):
        fb = _fb(
            intent="online", confidence=0.28, classification_method="regex_fallback",
            success=False, quality_score="failed_route",
        )
        d = fb.to_dict()
        d["semantic_would_have_been"] = "web_search"
        d["semantic_below_threshold"] = True
        d["decision_note"] = "conflict: semantic=web_search vs regex=online"
        # Write raw dict
        with open(ledger_path, "a") as f:
            f.write(json.dumps(d) + "\n")
    # Semantic successes for place/identity
    for intent in ["place_search", "identity", "ai_single", "ai_multi"]:
        ledger.append(_fb(
            intent=intent, confidence=0.9, classification_method="semantic",
            success=True, quality_score="correct_route",
        ))
    return ledger


# ===========================================================================
# 1. Activation / Deactivation
# ===========================================================================

class TestActivation:

    def test_activate(self, cycle):
        result = cycle.activate()
        assert result["new_mode"] == "active"
        assert cycle.is_active()

    def test_deactivate(self, cycle):
        cycle.activate()
        result = cycle.deactivate()
        assert result["new_mode"] == "inactive"
        assert not cycle.is_active()

    def test_inactive_by_default(self, cycle):
        assert not cycle.is_active()

    def test_cycle_skips_when_inactive(self, cycle):
        result = cycle.run_cycle()
        assert result["skipped"] is True
        assert "inactive" in result["reason"]


# ===========================================================================
# 2. Run Cycle
# ===========================================================================

class TestRunCycle:

    def test_cycle_insufficient_data(self, cycle, tmp_data):
        cycle.activate()
        result = cycle.run_cycle()
        assert result["skipped"] is True
        assert "Insufficient" in result["reason"]

    def test_cycle_with_data(self, cycle, tmp_data):
        _populate(tmp_data["ledger"])
        cycle.activate()
        result = cycle.run_cycle()
        assert result["skipped"] is False
        assert result["cycle_id"] == 1
        assert result["total_records_analyzed"] >= 20
        assert "weight_analysis" in result
        assert "memory_gaps" in result
        assert "proposals" in result

    def test_cycle_increments_counter(self, cycle, tmp_data):
        _populate(tmp_data["ledger"])
        cycle.activate()
        r1 = cycle.run_cycle()
        r2 = cycle.run_cycle()
        assert r2["cycle_id"] == r1["cycle_id"] + 1


# ===========================================================================
# 3. Conflict Detection
# ===========================================================================

class TestConflictDetection:

    def test_detects_conflicts(self, cycle, tmp_data):
        # Create records with semantic vs regex conflict
        ledger_path = tmp_data["ledger"]
        for i in range(5):
            d = _fb(intent="online", classification_method="regex_fallback").to_dict()
            d["semantic_would_have_been"] = "local"
            with open(ledger_path, "a") as f:
                f.write(json.dumps(d) + "\n")
        # Pad to min records
        ledger = RouterDecisionLedger(path=ledger_path)
        for i in range(10):
            ledger.append(_fb(intent="memory", classification_method="semantic"))

        records = ledger.read_all()
        conflicts = cycle._detect_conflicts(records)
        assert "local→online" in conflicts
        assert conflicts["local→online"] == 5


# ===========================================================================
# 4. Layer Weight Analysis
# ===========================================================================

class TestLayerWeights:

    def test_weight_analysis(self, cycle, tmp_data):
        _populate(tmp_data["ledger"])
        records = cycle._ledger.read_all()
        wa = cycle._analyze_layer_weights(records)
        assert wa["total"] >= 20
        assert wa["semantic_count"] >= 0
        assert wa["regex_count"] >= 0
        assert wa["semantic_rate"] + wa["regex_rate"] <= 1.01  # rounding


# ===========================================================================
# 5. Memory Gap Detection
# ===========================================================================

class TestMemoryGaps:

    def test_detects_regex_local(self, cycle, tmp_data):
        _populate(tmp_data["ledger"])
        records = cycle._ledger.read_all()
        gaps = cycle._detect_memory_gaps(records)
        assert gaps["total_local"] >= 4
        assert gaps["regex_local"] >= 4

    def test_no_gaps_when_no_local(self, cycle, tmp_data):
        ledger = RouterDecisionLedger(path=tmp_data["ledger"])
        for i in range(15):
            ledger.append(_fb(intent="online", classification_method="semantic"))
        records = ledger.read_all()
        gaps = cycle._detect_memory_gaps(records)
        assert gaps["total_local"] == 0


# ===========================================================================
# 6. Proposal Persistence
# ===========================================================================

class TestProposalPersistence:

    def test_persist_and_read(self, cycle, tmp_data):
        p = LearningProposal(
            proposal_type="test",
            priority="low",
            description="Test proposal",
        )
        cycle._persist_proposal(p)
        proposals = cycle._read_proposals()
        assert len(proposals) == 1
        assert proposals[0]["proposal_type"] == "test"

    def test_get_pending(self, cycle, tmp_data):
        for status in ["pending", "approved", "pending"]:
            p = LearningProposal(
                proposal_type="test",
                priority="low",
                description=f"Proposal ({status})",
                status=status,
            )
            cycle._persist_proposal(p)
        pending = cycle.get_pending_proposals()
        assert len(pending) == 2


# ===========================================================================
# 7. Status
# ===========================================================================

class TestStatus:

    def test_status_inactive(self, cycle):
        status = cycle.get_status()
        assert status["mode"] == "inactive"
        assert status["cycles_completed"] == 0

    def test_status_after_cycle(self, cycle, tmp_data):
        _populate(tmp_data["ledger"])
        cycle.activate()
        cycle.run_cycle()
        status = cycle.get_status()
        assert status["mode"] == "active"
        assert status["cycles_completed"] == 1
        assert status["total_feedback_records"] >= 20


# ===========================================================================
# 8. Proposals do not modify core (safety)
# ===========================================================================

class TestSafety:

    def test_proposals_are_passive(self, cycle, tmp_data):
        """Proposals are generated but NEVER applied automatically."""
        _populate(tmp_data["ledger"])
        cycle.activate()
        result = cycle.run_cycle()
        for p in result["proposals"]:
            assert p["status"] == "pending"

    def test_routing_not_affected(self, tmp_data):
        """SmartRouter continues to work correctly regardless of learning cycle."""
        from core.smart_router import SmartRouter, Intent
        router = SmartRouter()
        route = router.route("que es python?")
        assert route.intent == Intent.ONLINE
        route2 = router.route("que dije ayer?")
        assert route2.intent == Intent.LOCAL


# ===========================================================================
# 9. Singleton
# ===========================================================================

class TestSingleton:

    def test_get_learning_cycle_returns_same(self):
        import core.router_learning_cycle as rlc
        rlc._cycle = None
        c1 = get_learning_cycle()
        c2 = get_learning_cycle()
        assert c1 is c2
        rlc._cycle = None
