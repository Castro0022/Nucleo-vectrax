"""
tests/test_idea_constitutional_visibility.py — Un bloqueo no es una omisión.

EL PROBLEMA
-----------
Los tres llamadores internos de `IdeaStore.add()` —`ingest_from_router_learning`,
`ingest_from_convergence_learner`, `ingest_from_router_analysis`— viven dentro
de un `try/except Exception`. Al hacer efectivo el control constitucional,
un bloqueo caía en ese manejador genérico junto a un timeout de red o un JSON
corrupto, y la ingesta devolvía 0 sin distinguir nada.

Desde fuera, tres cosas muy distintas se veían iguales:

  * "el control decidió que esta idea no debe crearse, y estas reglas lo
    explican"
  * "el control no pudo pronunciarse"
  * "no había ideas que importar"

LO QUE SE EXIGE
---------------
1. Bloqueo constitucional -> la idea NO se crea Y el bloqueo es VISIBLE:
   resultado estructurado, contador y registro auditable con correlation_id,
   reglas y motivo.
2. Error técnico -> tratamiento de siempre, sin contarse como bloqueo.
3. Idea permitida -> se crea con normalidad.

No se cambia ninguna política: `create_idea` sigue siendo AUTO.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.idea_store import (  # noqa: E402
    ConstitutionalBlock,
    ConstitutionalGuardUnavailable,
    IdeaPriority,
    IdeaSource,
    IdeaStore,
)
from core.operator import constitutional_mode  # noqa: E402
from core.operator.constitutional_guard import GateDecision  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(
        constitutional_mode, "_MODE_PATH",
        str(tmp_path / "mode.json"), raising=False,
    )
    constitutional_mode._cache["mode"] = None
    yield
    constitutional_mode._cache["mode"] = None


@pytest.fixture
def store(tmp_path):
    return IdeaStore(path=str(tmp_path / "ideas.jsonl"))


def _blocked_gate(reason="Ley 7 lo impide", rules=("L7=block",)):
    """Una `GateDecision` que deniega, con reglas y correlación reales."""
    import time
    return GateDecision(
        allowed=False, reason=reason, correlation_id="CID-BLOQUEO",
        action="create_idea", application_point="core.idea_store.IdeaStore.create",
        actor="router_learning", mode=constitutional_mode.ACTIVE,
        timestamp=time.time(), rules=rules,
    )


def _add(store, title="idea de prueba"):
    return store.add(
        title=title, description="d", source=IdeaSource.ROUTER_LEARNING,
        priority=IdeaPriority.MEDIUM, impact_score=0.5,
        affected_component="core",
    )


# ===========================================================================
# 3. Idea permitida -> se crea con normalidad
# ===========================================================================

class TestAllowedIdeaIsCreated:

    def test_an_allowed_idea_is_created(self, store):
        idea = _add(store)
        assert idea is not None
        assert idea.status.value == "pending"

    def test_no_block_is_recorded(self, store):
        _add(store)
        assert store.constitutional_block_count() == 0
        assert store.constitutional_blocks() == []

    def test_create_idea_is_still_auto(self):
        """No se cambia la política confirmada por Mario."""
        from core.operator.decision_authority import AUTO_ACTIONS, check_authority
        assert "create_idea" in AUTO_ACTIONS
        assert check_authority(
            "create_idea", governor_mode="observe", risk_level="LOW",
        ).auto_approved is True


# ===========================================================================
# 1. Bloqueo constitucional -> idea NO creada + bloqueo VISIBLE
# ===========================================================================

class TestConstitutionalBlockIsVisible:

    def test_the_idea_is_not_created(self, store):
        with patch(
            "core.operator.constitutional_guard.enforce_check",
            return_value=_blocked_gate(),
        ):
            with pytest.raises(ConstitutionalBlock):
                _add(store)
        assert store.all() == [], "la idea se creó pese al bloqueo"

    def test_the_exception_carries_the_audit_fields(self, store):
        with patch(
            "core.operator.constitutional_guard.enforce_check",
            return_value=_blocked_gate(),
        ):
            with pytest.raises(ConstitutionalBlock) as excinfo:
                _add(store)
        exc = excinfo.value
        assert exc.correlation_id == "CID-BLOQUEO"
        assert exc.rules == ("L7=block",)
        assert "Ley 7 lo impide" in exc.reason
        assert exc.to_dict()["rules"] == ["L7=block"]

    def test_an_ingest_records_the_block_instead_of_swallowing_it(self, store):
        """El caso real: dentro del `except Exception` de la ingesta."""
        recs = [{
            "proposal_type": "conflict_pattern", "priority": "high",
            "description": "propuesta bloqueada", "suggested_action": "x",
            "affected_component": "smart_router", "evidence": {},
        }]
        with patch.object(
            IdeaStore, "add",
            side_effect=ConstitutionalBlock("Ley 7 lo impide", _blocked_gate()),
        ), patch("core.idea_store._router_learning_proposals", return_value=recs,
                 create=True):
            added = store.ingest_from_router_learning()

        assert added == 0
        assert store.constitutional_block_count() >= 1, (
            "el bloqueo se absorbió en el except genérico: invisible"
        )
        block = store.constitutional_blocks()[0]
        assert block["correlation_id"] == "CID-BLOQUEO"
        assert block["rules"] == ["L7=block"]
        assert "Ley 7 lo impide" in block["reason"]
        assert block["source"] == "router_learning"

    def test_the_block_is_logged_auditably(self, store, caplog):
        with caplog.at_level(logging.WARNING, logger="vectrax.idea_store"):
            store._record_constitutional_block(
                ConstitutionalBlock("Ley 7 lo impide", _blocked_gate()),
                source="router_learning", title="idea X",
            )
        msg = caplog.text
        assert "CONSTITUTIONAL" in msg
        assert "CID-BLOQUEO" in msg
        assert "L7=block" in msg
        assert "Ley 7 lo impide" in msg

    def test_the_counter_is_bounded(self, store):
        for _ in range(store.MAX_CONSTITUTIONAL_BLOCKS + 25):
            store._record_constitutional_block(
                ConstitutionalBlock("r", _blocked_gate()),
                source="router_learning", title="t",
            )
        assert store.constitutional_block_count() == store.MAX_CONSTITUTIONAL_BLOCKS

    def test_blocks_are_returned_newest_first(self, store):
        for i in range(3):
            store._record_constitutional_block(
                ConstitutionalBlock(f"motivo {i}", _blocked_gate()),
                source="router_learning", title=f"idea {i}",
            )
        titles = [b["title"] for b in store.constitutional_blocks()]
        assert titles == ["idea 2", "idea 1", "idea 0"]


# ===========================================================================
# 2. Error técnico -> tratamiento de siempre, NO contado como bloqueo
# ===========================================================================

class TestTechnicalErrorKeepsItsOldTreatment:

    def test_a_crashed_guard_is_not_a_verdict(self, store):
        """Ambos impiden la acción, pero significan cosas opuestas."""
        assert issubclass(ConstitutionalGuardUnavailable, ConstitutionalBlock)
        with patch(
            "core.operator.constitutional_guard.enforce_check",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(ConstitutionalGuardUnavailable) as excinfo:
                _add(store)
        assert "no pudo evaluar" in str(excinfo.value)
        assert store.all() == [], "falla cerrado: la idea no se crea"

    def test_a_crashed_guard_is_not_counted_as_a_block(self, store):
        recs = [{
            "proposal_type": "conflict_pattern", "priority": "high",
            "description": "propuesta", "suggested_action": "x",
            "affected_component": "smart_router", "evidence": {},
        }]
        with patch.object(
            IdeaStore, "add",
            side_effect=ConstitutionalGuardUnavailable("control caído"),
        ), patch("core.idea_store._router_learning_proposals", return_value=recs,
                 create=True):
            added = store.ingest_from_router_learning()
        assert added == 0
        assert store.constitutional_block_count() == 0, (
            "una avería técnica se contó como bloqueo constitucional"
        )

    def test_an_ordinary_exception_keeps_the_old_treatment(self, store):
        recs = [{"proposal_type": "conflict_pattern", "priority": "high",
                 "description": "p", "suggested_action": "x",
                 "affected_component": "smart_router", "evidence": {}}]
        with patch.object(IdeaStore, "add", side_effect=ValueError("json roto")), \
             patch("core.idea_store._router_learning_proposals", return_value=recs,
                   create=True):
            added = store.ingest_from_router_learning()
        assert added == 0
        assert store.constitutional_block_count() == 0

    def test_the_three_outcomes_are_distinguishable(self, store):
        """El punto entero: tres desenlaces, tres lecturas distintas."""
        # (a) permitido
        assert _add(store, "permitida") is not None
        assert store.constitutional_block_count() == 0
        # (b) bloqueo -> contador sube
        store._record_constitutional_block(
            ConstitutionalBlock("Ley 7", _blocked_gate()),
            source="router_learning", title="bloqueada",
        )
        assert store.constitutional_block_count() == 1
        # (c) avería -> contador NO sube
        with patch(
            "core.operator.constitutional_guard.enforce_check",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(ConstitutionalGuardUnavailable):
                _add(store, "averiada")
        assert store.constitutional_block_count() == 1
        assert len(store.all()) == 1
