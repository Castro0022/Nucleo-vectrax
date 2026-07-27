"""
Tests — voz+texto del Canal del Creador (payload del servidor)
==============================================================
El habla y la sincronía por palabra (Web Speech `onboundary`) son del CLIENTE;
aquí probamos el PAYLOAD determinista y honesto (§5) que entrega el servidor.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.presence import phase0 as P0


def _make_db(path: str, rows) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE op_cycles (id TEXT PRIMARY KEY, timestamp REAL, channel TEXT, "
        "perceive_intent TEXT, decide_route TEXT, act_latency_ms REAL, verify_passed INTEGER)"
    )
    conn.executemany("INSERT INTO op_cycles VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_utterance_payload_deterministic_with_data():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "operational_cycles.db")
        _make_db(db, [
            ("c1", 100.0, "telegram", "saludo", "identity", 12.0, 1),
            ("c2", 101.0, "api", "consulta", "memory", 40.0, 1),
        ])
        a = P0.utterance_payload(db_path=db)
        b = P0.utterance_payload(db_path=db)
        assert a == b                              # determinista (misma db)
        assert a["text"] and "Ahora" in a["text"]  # texto real de datos
        assert a["provenance_count"] == 2          # procedencia = ventana leída
        assert a["provenance"] == ["c2", "c1"]     # orden determinista (ts DESC, id DESC)
        assert "provenance_hash" in a


def test_utterance_payload_honest_without_data():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "operational_cycles.db")  # no existe → sin datos
        a = P0.utterance_payload(db_path=db)
        assert a["provenance_count"] == 0          # sin procedencia → no expandible
        assert a["provenance"] == []
        # §5: lo dice, no inventa una cifra.
        low = a["text"].lower()
        assert "mirado" in low or "ciclos" in low
