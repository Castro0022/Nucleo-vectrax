"""
tests/core/test_core_pipeline.py — Tests del pipeline gravitacional core.

Cubre:
  1. ingest() crea knowledge star y la persiste en DB
  2. ingest_v2() crea pattern + user_star
  3. get_universe_status() retorna knowledge_stars y stars separados
  4. UniverseSnapshot incluye knowledge_star_count
  5. _read_universe_state() formatea ambos conteos correctamente
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Redirect DBs BEFORE importing modules
_TMPDIR = tempfile.mkdtemp(prefix="vx_core_test_")
_TEST_DB = os.path.join(_TMPDIR, "vectrax.db")

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ===========================================================================
# Helpers
# ===========================================================================

def _fake_embed(text: str):
    """Embedding determinista de 8 dims (np.ndarray) para tests sin red."""
    import hashlib
    import numpy as np
    h = hashlib.md5(text.encode()).digest()
    return np.array([float(b) / 255.0 for b in h[:8]], dtype=np.float32)


def _setup_test_db():
    """Point vectrax.db to temp dir and init schema."""
    import vectrax.db as db_mod
    db_mod.DB_DIR = Path(_TMPDIR)
    db_mod.DB_PATH = Path(_TEST_DB)
    db_mod.init_db()


# ===========================================================================
# Test 1 — ingest() creates knowledge star
# ===========================================================================

class TestIngestV1(unittest.TestCase):
    """ingest() should create a knowledge star in the stars table."""

    def setUp(self):
        _setup_test_db()

    @patch("vectrax.engine.embed", side_effect=_fake_embed)
    @patch("vectrax.engine.find_similar", return_value=[])
    def test_ingest_creates_knowledge_star(self, _mock_similar, _mock_embed):
        from vectrax.engine import ingest
        from vectrax.db import get_all_stars

        star = ingest(text="Vectrax es un organismo digital", channel="user", owner="test_user")

        self.assertIsNotNone(star)
        self.assertIsNotNone(star.id)
        self.assertEqual(star.channel, "user")
        self.assertEqual(star.owner, "test_user")

        # Verify persisted
        all_stars = get_all_stars()
        self.assertGreaterEqual(len(all_stars), 1)
        ids = [s.id for s in all_stars]
        self.assertIn(star.id, ids)

    @patch("vectrax.engine.embed", side_effect=_fake_embed)
    @patch("vectrax.engine.find_similar", return_value=[])
    def test_ingest_duplicate_increments_count(self, _mock_similar, _mock_embed):
        from vectrax.engine import ingest
        from vectrax.db import get_star

        star1 = ingest(text="Test duplicate", channel="user", owner="u1")
        # Simulate near-duplicate by mocking find_similar to return the star
        _mock_similar.return_value = [(star1.id, 0.99)]
        star2 = ingest(text="Test duplicate again", channel="user", owner="u1")

        self.assertEqual(star1.id, star2.id)
        refreshed = get_star(star1.id)
        self.assertEqual(refreshed.repetition_count, 2)


# ===========================================================================
# Test 2 — ingest_v2() creates pattern + user_star
# ===========================================================================

class TestIngestV2(unittest.TestCase):
    """ingest_v2() should create a pattern and update the user star."""

    def setUp(self):
        _setup_test_db()

    @patch("vectrax.engine.embed", side_effect=_fake_embed)
    @patch("vectrax.memory_evaluator.evaluate_memory_value", return_value=("stored", 0.5))
    def test_ingest_v2_creates_pattern_and_star(self, _mock_eval, _mock_embed):
        from vectrax.engine import ingest_v2
        from vectrax.db import get_user_star, get_patterns_for_user

        star = ingest_v2(text="Quiero recordar esto", user_id="tg:test123", topic="general")

        self.assertIsNotNone(star)
        self.assertEqual(star.user_id, "tg:test123")
        self.assertGreater(star.pattern_count, 0)

        # Verify user_star persisted
        persisted = get_user_star("tg:test123")
        self.assertIsNotNone(persisted)

        # Verify pattern persisted
        patterns = get_patterns_for_user("tg:test123")
        self.assertGreaterEqual(len(patterns), 1)

    @patch("vectrax.engine.embed", side_effect=_fake_embed)
    @patch("vectrax.memory_evaluator.evaluate_memory_value", return_value=("stored", 0.5))
    def test_ingest_v2_increments_activation(self, _mock_eval, _mock_embed):
        from vectrax.engine import ingest_v2

        star1 = ingest_v2(text="Primera interacción", user_id="tg:act_test", topic="general")
        star2 = ingest_v2(text="Segunda interacción", user_id="tg:act_test", topic="general")

        self.assertEqual(star2.user_id, star1.user_id)
        self.assertGreater(star2.activation_count, star1.activation_count)


# ===========================================================================
# Test 3 — get_universe_status() returns both counts
# ===========================================================================

class TestUniverseStatus(unittest.TestCase):
    """get_universe_status() must return knowledge_stars and stars separately."""

    def setUp(self):
        _setup_test_db()

    @patch("vectrax.engine.embed", side_effect=_fake_embed)
    @patch("vectrax.engine.find_similar", return_value=[])
    @patch("vectrax.memory_evaluator.evaluate_memory_value", return_value=("stored", 0.5))
    def test_universe_status_has_both_counts(self, _mock_eval, _mock_similar, _mock_embed):
        from vectrax.engine import ingest, ingest_v2
        from vectrax.db import get_universe_status

        # Create 2 knowledge stars (v1)
        ingest(text="Knowledge alpha", channel="user", owner="u1")
        ingest(text="Knowledge beta", channel="user", owner="u1")

        # Create 1 user star (v2)
        ingest_v2(text="User message", user_id="tg:user1", topic="general")

        status = get_universe_status()

        # knowledge_stars = count from stars table
        self.assertIn("knowledge_stars", status)
        self.assertGreaterEqual(status["knowledge_stars"], 2)

        # stars = count from user_stars table
        self.assertIn("stars", status)
        self.assertGreaterEqual(status["stars"], 1)

        # They should be independent
        self.assertNotEqual(status["knowledge_stars"], status["stars"])

    def test_universe_status_empty_db(self):
        from vectrax.db import get_universe_status, _get_conn
        # Clean tables for isolation
        with _get_conn() as conn:
            conn.execute("DELETE FROM user_stars")
            conn.execute("DELETE FROM stars")
            conn.execute("DELETE FROM patterns")

        status = get_universe_status()

        self.assertEqual(status["stars"], 0)
        self.assertEqual(status["knowledge_stars"], 0)
        self.assertEqual(status["patterns"], 0)


# ===========================================================================
# Test 4 — UniverseSnapshot includes knowledge_star_count
# ===========================================================================

class TestUniverseSnapshot(unittest.TestCase):
    """UniverseSnapshot dataclass must include knowledge_star_count."""

    def test_snapshot_has_knowledge_star_count_field(self):
        from core.self_observation.universe_observer import UniverseSnapshot

        snap = UniverseSnapshot()
        self.assertTrue(hasattr(snap, "knowledge_star_count"))
        self.assertEqual(snap.knowledge_star_count, 0)

    def test_snapshot_to_api_dict_includes_knowledge_stars(self):
        from core.self_observation.universe_observer import UniverseSnapshot

        snap = UniverseSnapshot(
            star_count=20,
            knowledge_star_count=107,
            pattern_count=89,
        )
        api = snap.to_api_dict()

        self.assertIn("knowledge_star_count", api)
        self.assertEqual(api["knowledge_star_count"], 107)
        self.assertEqual(api["star_count"], 20)
        self.assertEqual(api["pattern_count"], 89)

    def test_snapshot_to_dict_includes_knowledge_stars(self):
        from core.self_observation.universe_observer import UniverseSnapshot

        snap = UniverseSnapshot(knowledge_star_count=50)
        d = snap.to_dict()

        self.assertIn("knowledge_star_count", d)
        self.assertEqual(d["knowledge_star_count"], 50)


# ===========================================================================
# Test 5 — self_context formats both counts
# ===========================================================================

class TestSelfContextFormat(unittest.TestCase):
    """_read_universe_state() must surface BOTH knowledge stars and user stars.

    Contract: _read_universe_state() reads from the universe census (the single
    source of truth) — not a raw UniverseSnapshot. We mock get_census so the
    test is hermetic (independent of real machine DB state) and assert the real
    census-based output format.
    """

    @patch("core.self_observation.universe_observer.observe_universe")
    @patch("core.universe_census.get_census")
    def test_format_includes_both_star_types(self, mock_census, mock_observe):
        from core.universe_census import UniverseCensus
        from core.self_observation.universe_observer import UniverseSnapshot

        # Census = single source of truth for the universe totals/breakdown.
        mock_census.return_value = UniverseCensus(
            timestamp=time.time(),
            gravitational=0,
            knowledge=107,
            users=20,
            total=127,
            patterns=89,
            convergences=0,
            constellations=0,
            mass_total=2.5,
            layers={"core": 1, "mid": 0, "outer": 19},
            word_gravity_count=0,
        )
        # The operational sub-block calls observe_universe(); keep it hermetic.
        mock_observe.return_value = UniverseSnapshot(timestamp=time.time())

        from vectrax.self_context import _read_universe_state
        state = _read_universe_state()

        # Both star populations are surfaced (census SSOT format).
        self.assertIn("107 knowledge", state)
        self.assertIn("20 usuarios", state)
        # Layer breakdown is present.
        self.assertIn("core=1", state)
        self.assertIn("outer=19", state)


if __name__ == "__main__":
    unittest.main()
