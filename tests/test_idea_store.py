"""
tests/test_idea_store.py
========================
Tests del IdeaStore — store unificado de ideas operacionales.
"""
from __future__ import annotations

import json
import os
import pytest
import tempfile

from core.idea_store import (
    IdeaStore, IdeaStatus, IdeaPriority, IdeaSource,
    Idea, _get_convergence_level, get_idea_store,
)


@pytest.fixture
def tmp_store(tmp_path):
    """IdeaStore usando un archivo temporal aislado."""
    path = str(tmp_path / "ideas_test.jsonl")
    return IdeaStore(path=path)


# ---------------------------------------------------------------------------
# 1. Creación y persistencia
# ---------------------------------------------------------------------------

class TestIdeaCreation:

    def test_add_returns_idea(self, tmp_store):
        idea = tmp_store.add(
            title="Test idea",
            description="Descripción de prueba",
            source=IdeaSource.MANUAL,
            priority=IdeaPriority.MEDIUM,
            impact_score=0.6,
            affected_component="test_component",
        )
        assert idea is not None
        assert idea.idea_id.startswith("IDEA-")
        assert idea.status == IdeaStatus.PENDING
        assert idea.priority == IdeaPriority.MEDIUM

    def test_priority_score_calculated(self, tmp_store):
        idea = tmp_store.add(
            title="High impact",
            description="desc",
            source=IdeaSource.MANUAL,
            priority=IdeaPriority.HIGH,
            impact_score=0.8,
            affected_component="comp",
        )
        assert idea is not None
        # priority_score = impact * pw * (1 + conv) / 2
        # pw(HIGH)=0.75, conv ~0.5 default
        # 0.8 * 0.75 * 1.5 / 2 = 0.45
        assert idea.priority_score > 0
        assert 0 <= idea.priority_score <= 1.0

    def test_persistence(self, tmp_store, tmp_path):
        tmp_store.add(
            title="Persisted idea",
            description="desc",
            source=IdeaSource.ROUTER_LEARNING,
            priority=IdeaPriority.LOW,
            impact_score=0.3,
            affected_component="router",
        )
        # Reload from same file
        store2 = IdeaStore(path=str(tmp_path / "ideas_test.jsonl"))
        ideas = store2.all()
        assert len(ideas) == 1
        assert ideas[0].title == "Persisted idea"

    def test_deduplication_by_source_id(self, tmp_store):
        for _ in range(3):
            tmp_store.add(
                title="Duplicate",
                description="desc",
                source=IdeaSource.ROUTER_LEARNING,
                priority=IdeaPriority.MEDIUM,
                impact_score=0.5,
                affected_component="router",
                source_id="router_1_conflict_pattern_123",
            )
        assert len(tmp_store.all()) == 1  # only first inserted

    def test_no_dedup_without_source_id(self, tmp_store):
        for _ in range(3):
            tmp_store.add(
                title="No dedup",
                description="desc",
                source=IdeaSource.MANUAL,
                priority=IdeaPriority.MEDIUM,
                impact_score=0.5,
                affected_component="comp",
                source_id="",  # no source_id → no dedup
            )
        assert len(tmp_store.all()) == 3


# ---------------------------------------------------------------------------
# 2. Aprobación y rechazo
# ---------------------------------------------------------------------------

class TestReview:

    def test_approve_sets_approved(self, tmp_store):
        idea = tmp_store.add(
            title="Approve me",
            description="desc",
            source=IdeaSource.MANUAL,
            priority=IdeaPriority.HIGH,
            impact_score=0.7,
            affected_component="comp",
        )
        ok = tmp_store.approve(idea.idea_id, by="creator", note="tiene sentido")
        assert ok is True
        reloaded = tmp_store.get_by_id(idea.idea_id)
        assert reloaded.status == IdeaStatus.APPROVED
        assert reloaded.reviewed_by == "creator"
        assert reloaded.review_note == "tiene sentido"
        assert reloaded.reviewed_at is not None

    def test_reject_sets_rejected(self, tmp_store):
        idea = tmp_store.add(
            title="Reject me",
            description="desc",
            source=IdeaSource.MANUAL,
            priority=IdeaPriority.LOW,
            impact_score=0.2,
            affected_component="comp",
        )
        ok = tmp_store.reject(idea.idea_id, by="creator")
        assert ok is True
        reloaded = tmp_store.get_by_id(idea.idea_id)
        assert reloaded.status == IdeaStatus.REJECTED

    def test_cannot_approve_twice(self, tmp_store):
        idea = tmp_store.add(
            title="Once only",
            description="desc",
            source=IdeaSource.MANUAL,
            priority=IdeaPriority.MEDIUM,
            impact_score=0.5,
            affected_component="comp",
        )
        tmp_store.approve(idea.idea_id)
        ok2 = tmp_store.approve(idea.idea_id)
        assert ok2 is False

    def test_approve_nonexistent(self, tmp_store):
        ok = tmp_store.approve("IDEA-FFFFFFFF")
        assert ok is False

    def test_mark_applied(self, tmp_store):
        idea = tmp_store.add(
            title="Apply me",
            description="desc",
            source=IdeaSource.MANUAL,
            priority=IdeaPriority.HIGH,
            impact_score=0.9,
            affected_component="comp",
        )
        tmp_store.approve(idea.idea_id)
        ok = tmp_store.mark_applied(idea.idea_id)
        assert ok is True
        reloaded = tmp_store.get_by_id(idea.idea_id)
        assert reloaded.status == IdeaStatus.APPLIED
        assert reloaded.applied_at is not None


# ---------------------------------------------------------------------------
# 3. Ranking y filtrado
# ---------------------------------------------------------------------------

class TestRanking:

    def test_top_sorted_by_priority_score(self, tmp_store):
        tmp_store.add("Low", "d", IdeaSource.MANUAL, IdeaPriority.LOW, 0.1, "c")
        tmp_store.add("High", "d", IdeaSource.MANUAL, IdeaPriority.HIGH, 0.9, "c")
        tmp_store.add("Medium", "d", IdeaSource.MANUAL, IdeaPriority.MEDIUM, 0.5, "c")

        top = tmp_store.get_top(n=3)
        scores = [i.priority_score for i in top]
        assert scores == sorted(scores, reverse=True)

    def test_pending_filter(self, tmp_store):
        i1 = tmp_store.add("Pending", "d", IdeaSource.MANUAL, IdeaPriority.HIGH, 0.8, "c")
        i2 = tmp_store.add("Approved", "d", IdeaSource.MANUAL, IdeaPriority.HIGH, 0.8, "c")
        tmp_store.approve(i2.idea_id)

        pending = tmp_store.pending()
        assert len(pending) == 1
        assert pending[0].idea_id == i1.idea_id

    def test_get_top_with_status_filter(self, tmp_store):
        for _ in range(3):
            tmp_store.add("Idea", "d", IdeaSource.MANUAL, IdeaPriority.MEDIUM, 0.5, "c")
        ideas = tmp_store.all()
        tmp_store.approve(ideas[0].idea_id)

        approved = tmp_store.get_top(n=10, status=IdeaStatus.APPROVED)
        assert all(i.status == IdeaStatus.APPROVED for i in approved)


# ---------------------------------------------------------------------------
# 4. Ingesta desde router_proposals.jsonl
# ---------------------------------------------------------------------------

class TestRouterIngestion:

    def test_ingest_from_router_learning(self, tmp_store, tmp_path, monkeypatch):
        # Crear un router_proposals.jsonl ficticio
        proposals_path = str(tmp_path / "router_proposals.jsonl")
        proposals = [
            {
                "proposal_type": "conflict_pattern",
                "priority": "high",
                "description": "Conflicto recurrente (78x): sem=memory vs regex=online",
                "evidence": {"occurrences": 78},
                "suggested_action": "Expandir patrones semanticos para memory",
                "affected_component": "semantic_classifier",
                "cycle_id": 2,
                "created_at": 1234567890.0,
                "status": "pending",
            },
            {
                "proposal_type": "threshold_adjust",
                "priority": "low",
                "description": "Threshold semantico suboptimo",
                "evidence": {},
                "suggested_action": "Ajustar threshold a 0.85",
                "affected_component": "smart_router",
                "cycle_id": 2,
                "created_at": 1234567891.0,
                "status": "superseded",  # NO debe importarse
            },
        ]
        with open(proposals_path, "w") as f:
            for p in proposals:
                f.write(json.dumps(p) + "\n")

        # Monkeypatch DATA_DIR
        import core.idea_store as m
        monkeypatch.setattr(m, "_DATA_DIR", str(tmp_path))

        added = tmp_store.ingest_from_router_learning()
        assert added == 1  # solo el pending
        ideas = tmp_store.all()
        assert len(ideas) == 1
        assert ideas[0].priority == IdeaPriority.HIGH
        assert ideas[0].source == IdeaSource.ROUTER_LEARNING

    def test_ingest_skip_superseded(self, tmp_store, tmp_path, monkeypatch):
        proposals_path = str(tmp_path / "router_proposals.jsonl")
        with open(proposals_path, "w") as f:
            f.write(json.dumps({
                "proposal_type": "weight_shift",
                "priority": "medium",
                "description": "Old proposal",
                "evidence": {},
                "suggested_action": "do something",
                "affected_component": "x",
                "cycle_id": 1,
                "created_at": 1.0,
                "status": "superseded",
            }) + "\n")

        import core.idea_store as m
        monkeypatch.setattr(m, "_DATA_DIR", str(tmp_path))

        added = tmp_store.ingest_from_router_learning()
        assert added == 0


# ---------------------------------------------------------------------------
# 5. Panel de texto
# ---------------------------------------------------------------------------

class TestPanel:

    def test_panel_with_no_ideas(self, tmp_store):
        panel = tmp_store.build_panel()
        assert "EVOLUCIÓN DEL NÚCLEO" in panel
        assert "Sin ideas" in panel

    def test_panel_with_ideas(self, tmp_store):
        tmp_store.add("Idea A", "desc", IdeaSource.MANUAL, IdeaPriority.HIGH, 0.8, "comp")
        tmp_store.add("Idea B", "desc", IdeaSource.MANUAL, IdeaPriority.LOW, 0.2, "comp")
        panel = tmp_store.build_panel(n=5)
        assert "IDEA-" in panel
        assert "aprobar" in panel.lower() or "Pendientes" in panel

    def test_panel_shows_convergence(self, tmp_store):
        panel = tmp_store.build_panel()
        assert "Convergencia" in panel


# ---------------------------------------------------------------------------
# 6. Idea.short_summary
# ---------------------------------------------------------------------------

class TestIdeaSummary:

    def test_short_summary_format(self, tmp_store):
        idea = tmp_store.add(
            title="Summary test",
            description="desc",
            source=IdeaSource.ROUTER_LEARNING,
            priority=IdeaPriority.HIGH,
            impact_score=0.7,
            affected_component="router",
        )
        summary = idea.short_summary(idx=1)
        assert "#1" in summary
        assert idea.idea_id in summary
        assert "router_learning" in summary

    def test_short_summary_status_icons(self, tmp_store):
        idea = tmp_store.add(
            title="Icon test",
            description="desc",
            source=IdeaSource.MANUAL,
            priority=IdeaPriority.CRITICAL,
            impact_score=0.95,
            affected_component="core",
        )
        summary = idea.short_summary()
        assert "🔵" in summary or "✅" in summary or "❌" in summary or "🟢" in summary


# ---------------------------------------------------------------------------
# 7. Stats
# ---------------------------------------------------------------------------

class TestStats:

    def test_stats_empty(self, tmp_store):
        s = tmp_store.stats()
        assert s["total"] == 0
        assert s["by_status"] == {}

    def test_stats_counts(self, tmp_store):
        i1 = tmp_store.add("A", "d", IdeaSource.MANUAL, IdeaPriority.HIGH, 0.8, "c")
        i2 = tmp_store.add("B", "d", IdeaSource.ROUTER_LEARNING, IdeaPriority.LOW, 0.2, "c")
        tmp_store.approve(i1.idea_id)

        s = tmp_store.stats()
        assert s["total"] == 2
        assert s["by_status"].get("approved") == 1
        assert s["by_status"].get("pending") == 1
        assert s["by_source"].get("manual") == 1
        assert s["by_source"].get("router_learning") == 1


# ---------------------------------------------------------------------------
# 8. Singleton
# ---------------------------------------------------------------------------

def test_singleton_returns_same_instance():
    s1 = get_idea_store()
    s2 = get_idea_store()
    assert s1 is s2
