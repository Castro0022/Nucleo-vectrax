"""
Tests — Caracterización de procedencia (mecanismo de transición AUTOMÁTICO)
===========================================================================
Objetivo (petición explícita): convertir la transición "ventana completa →
procedencia selectiva" en un mecanismo automático, NO en una nota que alguien
tenga que recordar.

CONTRATO HOY: `answer_now()` proyecta la VENTANA COMPLETA. Con una ventana de
N, la procedencia tiene N ids y `expand()` devuelve exactamente esos N.
`test_characterization_full_window_expands_fully` fija ese contrato y pasa hoy.

CONTRATO FUTURO: en cuanto `answer_now()` pase a seleccionar solo PARTE de la
ventana (p. ej. 3 de 20), la caracterización FALLARÁ sola (la procedencia
dejará de ser la ventana completa). En ESE MISMO commit hay que:
  1. borrar `test_characterization_full_window_expands_fully`, y
  2. activar `test_selective_provenance_definitive` (quitar el skip).
No se acepta introducir selección sin actualizar esta prueba a la vez.

Hermético: op_cycles temporal.
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


def _make_db(path: str, n: int) -> None:
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
    rows = [
        (f"c{i:02d}", 1000.0 + i, "telegram", "factual_query", "self_aware", 100.0 + i, 1)
        for i in range(n)
    ]
    conn.executemany(
        "INSERT INTO op_cycles "
        "(id, timestamp, channel, perceive_intent, decide_route, act_latency_ms, verify_passed) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_characterization_full_window_expands_fully(tmp_path):
    """HOY: ventana completa ⇒ procedencia = ventana completa ⇒ expand = todo.

    Si este assert falla, es porque `answer_now()` empezó a seleccionar solo
    parte de la ventana. Eso es correcto — pero ENTONCES este test debe
    borrarse y activarse `test_selective_provenance_definitive` en el mismo
    commit. (Mecanismo automático, no nota.)
    """
    db = tmp_path / "operational_cycles.db"
    _make_db(str(db), 20)  # ventana disponible: 20

    a = phase0.answer_now(limit=20, db_path=str(db))
    assert len(a.provenance) == 20, (
        "answer_now ya NO proyecta la ventana completa → introdujiste selección. "
        "Borra este test y activa test_selective_provenance_definitive EN ESTE COMMIT."
    )

    got = phase0.expand(a.provenance, db_path=str(db))
    assert len(got) == 20, "expand devuelve la ventana completa"
    assert [r["id"] for r in got] == list(a.provenance), "orden exacto, ni un nodo más"


@pytest.mark.skip(
    reason="Test DEFINITIVO de procedencia selectiva. Activar (quitar skip) EN EL "
    "MISMO COMMIT en que answer_now pase a seleccionar parte de la ventana; "
    "sustituye a test_characterization_full_window_expands_fully."
)
def test_selective_provenance_definitive(tmp_path):
    """FUTURO: con selección, la procedencia es un SUBCONJUNTO ESTRICTO de la
    ventana (p. ej. 3 de 20) y expand devuelve exactamente ese subconjunto."""
    db = tmp_path / "operational_cycles.db"
    _make_db(str(db), 20)

    a = phase0.answer_now(limit=20, db_path=str(db))
    assert 0 < len(a.provenance) < 20, "selección: subconjunto estricto de la ventana (p.ej. 3 de 20)"

    got = phase0.expand(a.provenance, db_path=str(db))
    assert len(got) == len(a.provenance), "expand devuelve EXACTAMENTE la procedencia seleccionada"
    assert [r["id"] for r in got] == list(a.provenance)
