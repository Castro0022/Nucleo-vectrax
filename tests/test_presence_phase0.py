"""
Tests — Presencia Fase 0 (spec §9 Fase 0 + §6 expansión)
=========================================================
Verifica el cimiento de la Presencia SIN nada visual:

  - Aceptación §9: la misma pregunta con los mismos datos da la MISMA
    respuesta y el MISMO conjunto de procedencia (determinismo; el LLM no
    decide nada).
  - §6: expand(procedencia) devuelve EXACTAMENTE ese conjunto. Ni uno más.
  - Regla de evidencia §5: sin datos, no inventa; procedencia vacía.

Hermético: usa una op_cycles temporal, no toca datos reales.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.presence import phase0


# --- op_cycles temporal (solo las columnas que la Fase 0 lee) --------------

def _make_db(path: str, rows) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE op_cycles (
            id               TEXT PRIMARY KEY,
            timestamp        REAL NOT NULL,
            channel          TEXT NOT NULL DEFAULT '',
            perceive_intent  TEXT NOT NULL DEFAULT '',
            decide_route     TEXT NOT NULL DEFAULT '',
            act_latency_ms   REAL NOT NULL DEFAULT 0.0,
            verify_passed    INTEGER NOT NULL DEFAULT 1
        )"""
    )
    conn.executemany(
        "INSERT INTO op_cycles "
        "(id, timestamp, channel, perceive_intent, decide_route, act_latency_ms, verify_passed) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


_ROWS = [
    # id, ts, channel, intent, route, latency, verify_passed
    ("aaa1", 1000.0, "telegram", "factual_query", "self_aware", 537.0, 1),
    ("bbb2", 1001.0, "telegram", "market",        "resolve_market", 210.0, 1),
    ("ccc3", 1002.0, "telegram", "identity",      "resolve_identity", 15.0, 1),
    ("ddd4", 1003.0, "web",      "place_search",  "resolve_places", 330.0, 0),
    ("eee5", 1004.0, "telegram", "casual",        "resolve_memory", 12.0, 1),
    ("fff6", 1005.0, "telegram", "factual_query", "self_aware", 480.0, 1),
]


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "operational_cycles.db"
    _make_db(str(p), _ROWS)
    return str(p)


# --- Aceptación §9: determinismo ------------------------------------------

def test_answer_is_deterministic(db):
    a = phase0.answer_now(db_path=db)
    b = phase0.answer_now(db_path=db)
    assert a.text == b.text, "misma data ⇒ mismo texto"
    assert a.provenance == b.provenance, "misma data ⇒ misma procedencia"
    assert a.provenance_hash == b.provenance_hash


def test_answer_reads_real_data_not_invented(db):
    a = phase0.answer_now(db_path=db, limit=5)
    # El último ciclo (ts=1005) es fff6 → debe aparecer en el texto.
    assert "factual_query" in a.text and "self_aware" in a.text
    assert a.provenance[0] == "fff6", "el más reciente primero (ts DESC)"


def test_window_size_bounds_provenance(db):
    a = phase0.answer_now(db_path=db, limit=3)
    assert len(a.provenance) == 3, "la ventana acota la procedencia"


def test_data_change_changes_answer(tmp_path):
    p1 = tmp_path / "a.db"
    _make_db(str(p1), _ROWS[:3])
    p2 = tmp_path / "b.db"
    _make_db(str(p2), _ROWS)  # más/otros ciclos
    a1 = phase0.answer_now(db_path=str(p1))
    a2 = phase0.answer_now(db_path=str(p2))
    assert a1.provenance != a2.provenance, "datos distintos ⇒ respuesta distinta"


# --- §6: expansión reversible EXACTA --------------------------------------

def test_expand_returns_exactly_provenance_set(db):
    a = phase0.answer_now(db_path=db, limit=4)
    got = phase0.expand(a.provenance, db_path=db)
    ids = [r["id"] for r in got]
    assert ids == list(a.provenance), "expand devuelve EXACTAMENTE la procedencia, en orden"
    assert len(got) == len(a.provenance), "ni un nodo más"


def test_expand_no_provenance_is_empty(db):
    assert phase0.expand((), db_path=db) == []


def test_expand_ignores_unknown_ids_never_invents(db):
    got = phase0.expand(("aaa1", "NO_EXISTE"), db_path=db)
    ids = [r["id"] for r in got]
    assert ids == ["aaa1"], "los id ausentes no se inventan; no aparecen"


# --- Regla de evidencia §5: sin datos, no fabrica -------------------------

def test_no_data_is_honest(tmp_path):
    p = tmp_path / "empty.db"
    _make_db(str(p), [])
    a = phase0.answer_now(db_path=str(p))
    assert a.provenance == (), "sin datos ⇒ sin procedencia (no se puede expandir, y se ve)"
    assert not a.has_provenance
    assert "No" in a.text  # dice que no lo ha mirado / no hay datos


def test_missing_db_does_not_raise(tmp_path):
    a = phase0.answer_now(db_path=str(tmp_path / "does_not_exist.db"))
    assert a.provenance == ()
    assert isinstance(a.text, str)
