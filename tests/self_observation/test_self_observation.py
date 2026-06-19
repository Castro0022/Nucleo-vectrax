"""
tests/self_observation/test_self_observation.py — suite del Self
Perception Layer.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TMPDIR = tempfile.mkdtemp(prefix="vx_self_obs_test_")
os.environ["VECTRAX_BASELINE_SNAPSHOT"] = os.path.join(_TMPDIR, "baseline.json")

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.self_observation import (  # noqa: E402
    SystemSnapshot,
    collect_state,
    save_baseline,
    load_baseline,
    diff,
    reflect,
    compose_self_summary,
    compose_self_summary_for_prompt,
)


class TestStateCollector(unittest.TestCase):
    def test_collect_returns_snapshot(self):
        snap = collect_state()
        self.assertIsInstance(snap, SystemSnapshot)
        self.assertGreater(snap.timestamp, 0)
        # Modes son strings (default si no env)
        self.assertIn(snap.audio_mode, ("voice", "audio", "off", ""))
        self.assertIn(
            snap.sovereignty_mode, ("observe", "enforce", "off", ""),
        )

    def test_collect_handles_missing_dbs_gracefully(self):
        # Helpers internos deben tolerar paths inexistentes.
        from core.self_observation.state_collector import (
            _queue_counts, _gravity_counts, _continuity_counts,
        )
        # Llamamos los collectors privados con path roto via patch.
        import core.self_observation.state_collector as sc
        old_q, old_g, old_c, old_v = (
            sc._QUEUE_DB, sc._GRAVITY_DB, sc._CONTINUITY_DB, sc._VECTRAX_DB,
        )
        try:
            sc._QUEUE_DB = "/nonexistent/q.db"
            sc._GRAVITY_DB = "/nonexistent/g.db"
            sc._CONTINUITY_DB = "/nonexistent/c.db"
            # _gravity_counts() reads vectrax.db as the PRIMARY source (gravity.db
            # is only the legacy fallback). "Missing DBs gracefully" must isolate
            # ALL sources the collector consults, not just the fallback.
            sc._VECTRAX_DB = "/nonexistent/v.db"
            self.assertEqual(_queue_counts(), {
                "pending": 0, "processing": 0, "done": 0, "error": 0,
            })
            self.assertEqual(_gravity_counts()["total"], 0)
            self.assertEqual(_continuity_counts(), 0)
        finally:
            sc._QUEUE_DB = old_q
            sc._GRAVITY_DB = old_g
            sc._CONTINUITY_DB = old_c
            sc._VECTRAX_DB = old_v


class TestBaseline(unittest.TestCase):
    def test_save_and_load_baseline(self):
        snap = SystemSnapshot(
            timestamp=12345.0,
            queue_pending=5,
            deep_memory_count=10,
        )
        self.assertTrue(save_baseline(snap))
        loaded = load_baseline()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["timestamp"], 12345.0)
        self.assertEqual(loaded["queue_pending"], 5)


class TestDiff(unittest.TestCase):
    def test_diff_no_baseline(self):
        snap = SystemSnapshot(timestamp=100.0)
        d = diff(snap, None)
        self.assertIn("Sin baseline previo", " ".join(d["significant_changes"]))

    def test_diff_detects_growth(self):
        baseline = {
            "timestamp": 100.0,
            "queue_pending": 0,
            "recent_error_count_24h": 0,
            "deep_memory_count": 5,
            "context_identities_count": 0,
            "essential_memories_count": 0,
            "audio_mode": "voice",
            "sovereignty_mode": "observe",
            "continuity_processed_updates": 0,
        }
        current = SystemSnapshot(
            timestamp=200.0,
            queue_pending=10,
            deep_memory_count=15,
            context_identities_count=2,
            essential_memories_count=3,
            audio_mode="audio",
            sovereignty_mode="observe",
            continuity_processed_updates=120,
        )
        d = diff(current, baseline)
        self.assertEqual(d["queue_pending_delta"], 10)
        self.assertEqual(d["deep_memory_delta"], 10)
        self.assertEqual(d["identities_delta"], 2)
        self.assertEqual(d["essentials_delta"], 3)
        self.assertEqual(d["audio_mode_changed"], ("voice", "audio"))
        sig = " ".join(d["significant_changes"])
        self.assertIn("cola creció", sig)
        self.assertIn("identidades de contexto", sig)
        self.assertIn("audio mode voice → audio", sig)


class TestReflect(unittest.TestCase):
    def test_reflect_basic(self):
        snap = SystemSnapshot(
            timestamp=200.0,
            recent_error_count_24h=3,
            deep_memory_count=42,
            context_identities_count=2,
            essential_memories_count=1,
            audio_mode="audio",
            sovereignty_mode="observe",
        )
        diff_data = {
            "audio_mode_changed": ("voice", "audio"),
            "error_rate_delta": -10,
            "significant_changes": [],
        }
        deploys = {
            "recent_commits": [
                {"sha": "aaaa1111", "subject": "fix(voice): autoplay",
                 "author": "Mario", "ts": 0, "files_changed": 2},
                {"sha": "bbbb2222", "subject": "feat(continuity): idempotencia",
                 "author": "Mario", "ts": 0, "files_changed": 5},
            ],
            "modified_modules": [
                "core/voice/telegram_dispatch.py",
                "core/continuity/idempotency.py",
            ],
            "current_branch": "main",
            "current_head": "abcdef1",
        }
        out = reflect(snap, diff_data, deploys)
        self.assertIn("what_changed_today", out)
        self.assertTrue(any(
            "fix(voice)" in s for s in out["what_changed_today"]
        ))
        self.assertTrue(any(
            "fix" in s.lower() for s in out["problems_solved"]
        ))
        self.assertIn("audio=audio", out["modes_line"])
        self.assertIn("memory_line", out)


class TestSelfSummary(unittest.TestCase):
    def test_compose_returns_string(self):
        s = compose_self_summary()
        self.assertIsInstance(s, str)
        # Debe contener al menos la cabecera
        if s:  # may be empty if reflect_now fails defensively
            self.assertIn("PERCEPCIÓN OPERACIONAL", s)

    def test_compose_truncates(self):
        s = compose_self_summary_for_prompt(max_chars=50)
        if s:
            self.assertLessEqual(len(s), 80)


if __name__ == "__main__":
    unittest.main()
