"""
tests/continuity/test_idempotency.py — SQLite update_id ledger.

Verifica el cierre del bug 'el supervisor reinicia el gateway y
Telegram redelivera el update_id, causando doble respuesta':
  - is_processed(uid) → True después de mark_processed(uid).
  - Distintos uids son independientes.
  - Pruning saca entradas viejas.
  - Concurrencia: 100 marks paralelos = 100 entradas únicas.
  - Schema aplicado idempotentemente (todas las tablas vctx_*).
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

# Redirect DB path BEFORE importing the module
_TMPDIR = tempfile.mkdtemp(prefix="vx_continuity_test_")
os.environ["VECTRAX_CONTINUITY_DB"] = os.path.join(_TMPDIR, "cont.db")

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import sqlite3  # noqa: E402

from core.continuity import (  # noqa: E402
    SQLiteUpdateLedger,
    is_processed,
    mark_processed,
    prune_older_than,
    reset_for_tests,
)


class TestSchemaApplied(unittest.TestCase):
    """Verifica que las 5 tablas vctx_* existan tras la creación."""

    def test_all_tables_present(self):
        ledger = SQLiteUpdateLedger(
            db_path=os.path.join(_TMPDIR, "schema.db"),
        )
        c = sqlite3.connect(ledger.db_path)
        try:
            tables = {
                row[0] for row in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
        finally:
            c.close()
        for expected in (
            "vctx_identity",
            "vctx_processed_updates",
            "vctx_pending_actions",
            "vctx_working_memory",
            "vctx_conversation_snapshots",
        ):
            self.assertIn(expected, tables,
                          f"missing table: {expected}")


class TestIsProcessedMarkProcessed(unittest.TestCase):
    def setUp(self):
        reset_for_tests()

    def test_unmarked_returns_false(self):
        self.assertFalse(is_processed(12345))

    def test_after_mark_returns_true(self):
        mark_processed(12345, chat_id="2030762343")
        self.assertTrue(is_processed(12345))

    def test_different_ids_independent(self):
        mark_processed(111, chat_id="A")
        self.assertTrue(is_processed(111))
        self.assertFalse(is_processed(222))
        self.assertFalse(is_processed(333))

    def test_zero_update_id_no_op(self):
        self.assertFalse(is_processed(0))
        self.assertFalse(mark_processed(0, ""))

    def test_idempotent_mark(self):
        # Mark twice → no error, sigue True
        self.assertTrue(mark_processed(7777, "X"))
        self.assertTrue(mark_processed(7777, "X"))
        self.assertTrue(is_processed(7777))


class TestRedeliveryScenario(unittest.TestCase):
    """Escenario real del bug: supervisor reinicia mid-processing."""

    def setUp(self):
        reset_for_tests()

    def test_redelivery_is_idempotent(self):
        # Simulamos: el gateway recibe update_id=999, lo marca, despacha
        # al pool. Crash → restart. Telegram redelivera el mismo update_id.
        update_id = 999
        chat_id = "2030762343"

        # Primera entrega
        self.assertFalse(is_processed(update_id))
        mark_processed(update_id, chat_id=chat_id)

        # Crash + restart simulado: misma update_id llega de nuevo
        # El gateway pregunta antes de despachar.
        self.assertTrue(is_processed(update_id))
        # El gateway hace `continue` → NO despacha → NO doble audio.


class TestPruning(unittest.TestCase):
    def setUp(self):
        reset_for_tests()

    def test_prune_removes_old(self):
        mark_processed(1, "A")
        # Forzar received_at viejo
        ledger = SQLiteUpdateLedger()
        c = sqlite3.connect(ledger.db_path)
        try:
            old_ts = int(time.time()) - 10 * 86400  # 10 días
            c.execute(
                "UPDATE vctx_processed_updates SET received_at = ? "
                "WHERE update_id = 1",
                (old_ts,),
            )
            c.commit()
        finally:
            c.close()

        n = prune_older_than(seconds=7 * 86400)
        self.assertEqual(n, 1)
        self.assertFalse(is_processed(1))

    def test_prune_keeps_fresh(self):
        mark_processed(2, "A")
        n = prune_older_than(seconds=7 * 86400)
        self.assertEqual(n, 0)
        self.assertTrue(is_processed(2))


class TestConcurrency(unittest.TestCase):
    def setUp(self):
        reset_for_tests()

    def test_100_threads_100_marks(self):
        N = 100
        threads = []

        def _mark(uid):
            mark_processed(uid, chat_id="x")

        for i in range(1000, 1000 + N):
            t = threading.Thread(target=_mark, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        # Todos deben haber sido marcados
        for i in range(1000, 1000 + N):
            self.assertTrue(is_processed(i), f"missing {i}")


if __name__ == "__main__":
    unittest.main()
