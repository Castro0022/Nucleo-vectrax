"""
tests/continuity/test_followups.py — tests para items #4 #5 #8 #9.

  #4 self_summary TTL cache
  #5 voice_forbidden persistencia
  #8 working_memory turn-based
  #9 pending_actions ok/dale flow
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_TMPDIR = tempfile.mkdtemp(prefix="vx_followups_test_")
os.environ["VECTRAX_CONTINUITY_DB"] = os.path.join(_TMPDIR, "cont.db")
os.environ["VECTRAX_BASELINE_SNAPSHOT"] = os.path.join(_TMPDIR, "baseline.json")

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.continuity.voice_forbidden import (  # noqa: E402
    is_forbidden, mark_forbidden, unmark, list_forbidden,
    reset_for_tests as reset_vf,
)
from core.continuity.working_memory import (  # noqa: E402
    record_turn, get_recent_turns, format_for_prompt,
    clear_for_chat, reset_for_tests as reset_wm,
)
from core.continuity.pending_actions import (  # noqa: E402
    propose, consume_if_confirmation, consume_by_id,
    list_pending, expire_old, reset_for_tests as reset_pa,
)


# ===========================================================================
# #4 — self_summary cache
# ===========================================================================

class TestSelfSummaryCache(unittest.TestCase):
    def setUp(self):
        from core.self_observation.self_summary import invalidate_cache
        invalidate_cache()

    def test_cache_hit_returns_same_string(self):
        from core.self_observation import compose_self_summary
        s1 = compose_self_summary()
        s2 = compose_self_summary()
        # mismo objeto en cache (al menos contenido idéntico)
        self.assertEqual(s1, s2)

    def test_use_cache_false_recomputes(self):
        from core.self_observation import compose_self_summary
        compose_self_summary()  # populate cache
        # Forzar recompute pasando reflection explícita
        s2 = compose_self_summary(
            reflection={"stability_line": "x", "memory_line": "y",
                        "modes_line": "z"},
            use_cache=False,
        )
        # No usa cache; resultado depende del reflection pasado
        self.assertIn("x", s2)


# ===========================================================================
# #5 — voice_forbidden persistente
# ===========================================================================

class TestVoiceForbidden(unittest.TestCase):
    def setUp(self):
        reset_vf()

    def test_mark_and_check(self):
        self.assertFalse(is_forbidden(123))
        self.assertTrue(mark_forbidden(123))
        self.assertTrue(is_forbidden(123))

    def test_unmark(self):
        mark_forbidden(456)
        self.assertTrue(is_forbidden(456))
        self.assertTrue(unmark(456))
        self.assertFalse(is_forbidden(456))

    def test_list(self):
        for cid in (1, 2, 3):
            mark_forbidden(cid)
        ls = list_forbidden()
        self.assertEqual(set(ls), {1, 2, 3})

    def test_persistence_across_module_state(self):
        """Simular reload: marcar, limpiar in-memory cache, re-leer desde DB."""
        mark_forbidden(99)
        # Forzar reload desde DB
        import core.continuity.voice_forbidden as vf
        vf._in_memory_cache.clear()
        vf._cache_loaded = False
        # Próxima query relee de DB
        self.assertTrue(is_forbidden(99))


# ===========================================================================
# #8 — working_memory
# ===========================================================================

class TestWorkingMemory(unittest.TestCase):
    def setUp(self):
        reset_wm()

    def test_record_and_get(self):
        record_turn("chat1", "user", "hola")
        record_turn("chat1", "vectrax", "hey")
        turns = get_recent_turns("chat1", limit=10)
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["role"], "user")
        self.assertEqual(turns[0]["turn_index"], 0)
        self.assertEqual(turns[1]["role"], "vectrax")
        self.assertEqual(turns[1]["turn_index"], 1)

    def test_isolation_between_chats(self):
        record_turn("A", "user", "msgA")
        record_turn("B", "user", "msgB")
        a = get_recent_turns("A")
        b = get_recent_turns("B")
        self.assertEqual(len(a), 1)
        self.assertEqual(len(b), 1)
        self.assertEqual(a[0]["content"], "msgA")
        self.assertEqual(b[0]["content"], "msgB")

    def test_format_for_prompt(self):
        record_turn("c", "user", "hola Vectrax")
        record_turn("c", "vectrax", "Mario, dime")
        out = format_for_prompt("c")
        self.assertIn("HILO RECIENTE", out)
        self.assertIn("Mario:", out)
        self.assertIn("Vectrax:", out)

    def test_clear(self):
        record_turn("z", "user", "x")
        record_turn("z", "user", "y")
        n = clear_for_chat("z")
        self.assertEqual(n, 2)
        self.assertEqual(get_recent_turns("z"), [])

    def test_invalid_role_falls_to_user(self):
        idx = record_turn("c2", "alien", "weird")
        self.assertGreaterEqual(idx, 0)
        turns = get_recent_turns("c2")
        self.assertEqual(turns[0]["role"], "user")


# ===========================================================================
# #9 — pending_actions ok/dale flow
# ===========================================================================

class TestPendingActions(unittest.TestCase):
    def setUp(self):
        reset_pa()

    def test_propose_and_list(self):
        aid = propose("c1", "delete_cache", {"target": "tts"})
        self.assertTrue(aid)
        ps = list_pending("c1")
        self.assertEqual(len(ps), 1)
        self.assertEqual(ps[0]["action_type"], "delete_cache")
        self.assertEqual(ps[0]["payload"], {"target": "tts"})

    def test_confirmation_consumes(self):
        aid = propose("c1", "act", {"x": 1})
        out = consume_if_confirmation("c1", "ok")
        self.assertIsNotNone(out)
        self.assertEqual(out["id"], aid)
        self.assertEqual(out["payload"], {"x": 1})
        # Ya no debe quedar pending
        self.assertEqual(list_pending("c1"), [])

    def test_dale_works_too(self):
        propose("c2", "act", {})
        out = consume_if_confirmation("c2", "dale")
        self.assertIsNotNone(out)

    def test_rejection_cancels(self):
        propose("c3", "deploy", {"branch": "main"})
        out = consume_if_confirmation("c3", "no")
        self.assertIsNotNone(out)
        self.assertEqual(out["action_type"], "__cancelled__")
        self.assertEqual(list_pending("c3"), [])

    def test_neutral_text_does_not_consume(self):
        propose("c4", "act", {})
        out = consume_if_confirmation("c4", "qué tal el clima?")
        self.assertIsNone(out)
        # Pending sigue viva
        self.assertEqual(len(list_pending("c4")), 1)

    def test_expired_not_consumable(self):
        aid = propose("c5", "act", {}, ttl=30)
        # Forzar expiración manual
        import sqlite3
        c = sqlite3.connect(os.environ["VECTRAX_CONTINUITY_DB"])
        c.execute(
            "UPDATE vctx_pending_actions SET expires_at = ? WHERE id = ?",
            (int(time.time()) - 100, aid),
        )
        c.commit()
        c.close()
        out = consume_by_id(aid)
        self.assertIsNone(out)

    def test_lifo_consume_latest(self):
        propose("c6", "first", {"v": 1})
        time.sleep(0.01)
        propose("c6", "second", {"v": 2})
        out = consume_if_confirmation("c6", "ok")
        self.assertEqual(out["action_type"], "second")

    def test_isolation_between_chats(self):
        propose("A", "do_a", {})
        out = consume_if_confirmation("B", "ok")
        self.assertIsNone(out)
        # La pending de A sigue
        self.assertEqual(len(list_pending("A")), 1)


if __name__ == "__main__":
    unittest.main()
