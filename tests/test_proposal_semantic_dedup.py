"""
tests/test_proposal_semantic_dedup.py — Deduplicación por patrón de propuestas
===============================================================================
Reproduce y fija la causa demostrada de la acumulación de propuestas
`fallback_resolved` casi idénticas (92 → 160 casos entre el 4 y el 21 de
septiembre de 2026, todas `pending`).

CAUSA
-----
`IdeaStore.ingest_from_router_learning()` construye
`source_id = f"router_{cycle_id}_{proposal_type}_{created_at}"`. Ese
identificador cambia en CADA ciclo de aprendizaje aunque el patrón observado
sea exactamente el mismo, así que la deduplicación por `source_id` —que
funciona correctamente para lo suyo, evitar reprocesar la misma línea— nunca
podía reconocer dos propuestas equivalentes. Era identidad por OCURRENCIA,
no por PATRÓN.

AISLAMIENTO: todas las pruebas usan `tmp_path`. Ninguna toca
`data/ideas.jsonl` ni `data/router_proposals.jsonl` reales.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.idea_store import (  # noqa: E402
    IdeaPriority,
    IdeaSource,
    IdeaStatus,
    IdeaStore,
    _semantic_dedup_key,
)


def _fallback_proposal(cycle_id: int, created_at: int, cases: int) -> dict:
    """Propuesta del router con el MISMO patrón y distinta ocurrencia —
    exactamente la forma observada en producción."""
    return {
        "cycle_id": cycle_id,
        "created_at": created_at,
        "proposal_type": "fallback_resolved",
        "priority": "medium",
        "affected_component": "smart_router",
        "description": f"{cases} casos donde el fallback resolvió mejor que la ruta primaria",
        "suggested_action": "La ruta primaria (resolve_local, resolve_identity) necesita mejorar",
        "evidence": {"cases": cases, "routes": ["resolve_local", "resolve_identity"]},
        "status": "pending",
    }


# ---------------------------------------------------------------------------
# La causa: el source_id cambia aunque el patrón no
# ---------------------------------------------------------------------------

def test_source_id_differs_across_cycles_for_the_same_pattern():
    """Prueba de regresión de la CAUSA, no del síntoma."""
    a = _fallback_proposal(cycle_id=41, created_at=1000, cases=92)
    b = _fallback_proposal(cycle_id=57, created_at=2000, cases=160)
    sid = lambda p: f"router_{p['cycle_id']}_{p['proposal_type']}_{p['created_at']}"
    assert sid(a) != sid(b), "premisa del defecto: la identidad por ocurrencia difiere"
    # …y sin embargo son el MISMO patrón:
    assert _semantic_dedup_key(a) == _semantic_dedup_key(b)


def test_semantic_key_ignores_volatile_counters():
    """Los conteos cambian cada ciclo sin cambiar el significado."""
    base = _fallback_proposal(1, 1, cases=92)
    grown = _fallback_proposal(9, 9, cases=160)
    assert _semantic_dedup_key(base) == _semantic_dedup_key(grown)


def test_semantic_key_separates_genuinely_different_patterns():
    """La deduplicación NO puede tragarse propuestas distintas."""
    fallback = _fallback_proposal(1, 1, cases=92)
    other = dict(fallback, proposal_type="semantic_regex_conflict")
    another = dict(fallback, affected_component="semantic_classifier")
    keys = {_semantic_dedup_key(fallback), _semantic_dedup_key(other),
            _semantic_dedup_key(another)}
    assert len(keys) == 3, "patrones distintos deben producir claves distintas"


def test_semantic_key_is_empty_without_stable_discriminators():
    """Fail-open: sin discriminante estable no se deduplica (mejor repetir
    que perder una propuesta distinta)."""
    assert _semantic_dedup_key({"cycle_id": 1, "created_at": 2}) == ""


# ---------------------------------------------------------------------------
# El efecto: el store deja de acumular pendientes equivalentes
# ---------------------------------------------------------------------------

def test_repeated_pattern_is_ingested_once_while_pending(tmp_path):
    store = IdeaStore(path=str(tmp_path / "ideas.jsonl"))
    key = _semantic_dedup_key(_fallback_proposal(1, 1, 92))

    first = store.add(
        title="92 casos de fallback", description="d", source=IdeaSource.ROUTER_LEARNING,
        priority=IdeaPriority.MEDIUM, impact_score=0.5, affected_component="smart_router",
        evidence={"cases": 92}, source_id="router_41_fallback_resolved_1000", dedup_key=key,
    )
    second = store.add(
        title="160 casos de fallback", description="d", source=IdeaSource.ROUTER_LEARNING,
        priority=IdeaPriority.MEDIUM, impact_score=0.5, affected_component="smart_router",
        evidence={"cases": 160}, source_id="router_57_fallback_resolved_2000", dedup_key=key,
    )
    assert first is not None, "la primera aparición debe registrarse"
    assert second is None, "la segunda es el MISMO patrón y sigue pendiente"
    assert len(store.pending()) == 1


def test_pattern_reappears_after_the_previous_one_was_reviewed(tmp_path):
    """Si Mario ya decidió sobre el patrón, una nueva aparición es información
    nueva — no debe silenciarse."""
    store = IdeaStore(path=str(tmp_path / "ideas.jsonl"))
    key = _semantic_dedup_key(_fallback_proposal(1, 1, 92))
    common = dict(
        description="d", source=IdeaSource.ROUTER_LEARNING, priority=IdeaPriority.MEDIUM,
        impact_score=0.5, affected_component="smart_router", dedup_key=key,
    )
    first = store.add(title="92 casos", evidence={}, source_id="router_1_x_1", **common)
    assert first is not None
    assert store.reject(first.idea_id, by="creator") is True

    again = store.add(title="160 casos", evidence={}, source_id="router_2_x_2", **common)
    assert again is not None, "tras revisar el patrón, una reaparición vuelve a verse"


def test_existing_proposals_are_never_modified_or_removed(tmp_path):
    """La corrección es aditiva: no reescribe el significado histórico."""
    path = tmp_path / "ideas.jsonl"
    store = IdeaStore(path=str(path))
    legacy = store.add(
        title="propuesta histórica sin dedup_key", description="d",
        source=IdeaSource.ROUTER_LEARNING, priority=IdeaPriority.MEDIUM,
        impact_score=0.5, affected_component="smart_router",
        evidence={"cases": 92}, source_id="router_1_fallback_resolved_1",
    )
    assert legacy is not None
    snapshot = path.read_bytes()

    store.add(
        title="nueva con dedup_key", description="d", source=IdeaSource.ROUTER_LEARNING,
        priority=IdeaPriority.MEDIUM, impact_score=0.5, affected_component="smart_router",
        evidence={}, source_id="router_2_fallback_resolved_2",
        dedup_key=_semantic_dedup_key(_fallback_proposal(1, 1, 92)),
    )
    assert snapshot in path.read_bytes(), "el registro anterior fue alterado"
    assert store.get_by_id(legacy.idea_id) is not None
    assert store.get_by_id(legacy.idea_id).status is IdeaStatus.PENDING


def test_occurrence_dedup_still_works(tmp_path):
    """El eje que ya existía no se rompe."""
    store = IdeaStore(path=str(tmp_path / "ideas.jsonl"))
    common = dict(
        title="t", description="d", source=IdeaSource.ROUTER_LEARNING,
        priority=IdeaPriority.MEDIUM, impact_score=0.5,
        affected_component="smart_router", evidence={}, source_id="router_1_x_1",
    )
    assert store.add(**common) is not None
    assert store.add(**common) is None


def test_end_to_end_ingest_collapses_equivalent_cycles(tmp_path, monkeypatch):
    """Ruta real `ingest_from_router_learning()` con fixture aislada."""
    import core.idea_store as mod
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(mod, "_DATA_DIR", str(data_dir))

    proposals = data_dir / "router_proposals.jsonl"
    with open(proposals, "w", encoding="utf-8") as fh:
        for cycle, (created, cases) in enumerate(((1000, 92), (2000, 118), (3000, 160)), 1):
            fh.write(json.dumps(_fallback_proposal(cycle, created, cases)) + "\n")

    store = IdeaStore(path=str(tmp_path / "ideas.jsonl"))
    added = store.ingest_from_router_learning()

    assert added == 1, f"3 ciclos del mismo patrón produjeron {added} propuestas"
    assert len(store.pending()) == 1
