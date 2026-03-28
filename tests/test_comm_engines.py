"""
Tests for Vectrax Communication Engines.

Covers:
    1. Factory routing — Mario → CoreCommEngine, others → StarCommEngine
    2. CoreCommEngine — text ingestion, context, history
    3. StarCommEngine — text ingestion, isolation, context
    4. Channel separation — user cannot instantiate CoreCommEngine
    5. User isolation — user A cannot see user B's data
    6. CoreProjection — only structure, never content
    7. Voice pipeline — mock transcription
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Helpers: deterministic fake embeddings
# ---------------------------------------------------------------------------

_DIMENSION = 384


def _fake_embed(text: str) -> np.ndarray:
    rng = np.random.RandomState(hash(text) % (2**31))
    vec = rng.randn(_DIMENSION).astype(np.float32)
    vec /= np.linalg.norm(vec)
    return vec


# ---------------------------------------------------------------------------
# Patch setup: redirect DB to temp dir, mock embeddings
# ---------------------------------------------------------------------------

_tmpdir = tempfile.mkdtemp(prefix="vectrax_test_comm_")
_test_db_path = os.path.join(_tmpdir, "vectrax.db")


def _setup_patches():
    patches = []

    p1 = patch("vectrax.db.DB_DIR", Path(_tmpdir))
    p2 = patch("vectrax.db.DB_PATH", Path(_test_db_path))
    patches.extend([p1, p2])

    p3 = patch("vectrax.embeddings.embed", side_effect=_fake_embed)
    patches.append(p3)

    for p in patches:
        p.start()

    from vectrax import db
    db.init_db()

    return patches


def _teardown_patches(patches):
    for p in patches:
        p.stop()
    if os.path.exists(_test_db_path):
        os.unlink(_test_db_path)


# ===================================================================
# 1. Factory routing
# ===================================================================

class TestFactory(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_mario_gets_core_engine(self):
        from vectrax.comm import get_engine
        from vectrax.comm.engine_core import CoreCommEngine

        engine = get_engine("mario")
        self.assertIsInstance(engine, CoreCommEngine)

    def test_user_gets_star_engine(self):
        from vectrax.comm import get_engine
        from vectrax.comm.engine_star import StarCommEngine

        engine = get_engine("carlos")
        self.assertIsInstance(engine, StarCommEngine)
        self.assertEqual(engine.owner, "carlos")

    def test_different_users_get_separate_engines(self):
        from vectrax.comm import get_engine

        e1 = get_engine("alice")
        e2 = get_engine("bob")
        self.assertNotEqual(e1.owner, e2.owner)
        self.assertNotEqual(e1.session_id, e2.session_id)


# ===================================================================
# 2. CoreCommEngine — text
# ===================================================================

class TestCoreCommEngine(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph
        graph.reset_graph()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_send_text_returns_response(self):
        from vectrax.comm.engine_core import CoreCommEngine

        engine = CoreCommEngine()
        response = engine.send_text("concepto fundacional de prueba")

        self.assertIsNotNone(response.star_id)
        self.assertNotEqual(response.star_id, "")
        self.assertNotEqual(response.text, "")
        self.assertEqual(response.source_type, "text")

    def test_send_text_ingests_into_creator_channel(self):
        from vectrax.comm.engine_core import CoreCommEngine
        from vectrax import db
        from vectrax.identity import CHANNEL_CREATOR, CREATOR_OWNER

        engine = CoreCommEngine()
        response = engine.send_text("entrada al núcleo")

        star = db.get_star(response.star_id)
        self.assertIsNotNone(star)
        self.assertEqual(star.channel, CHANNEL_CREATOR)
        self.assertEqual(star.owner, CREATOR_OWNER)

    def test_get_context_returns_nucleus_info(self):
        from vectrax.comm.engine_core import CoreCommEngine

        engine = CoreCommEngine()
        engine.send_text("semilla de contexto")

        ctx = engine.get_context()
        self.assertEqual(ctx["engine_type"], "core")
        self.assertIn("text", ctx["capabilities"])
        self.assertIn("voice", ctx["capabilities"])
        self.assertGreaterEqual(ctx["stars_total"], 1)

    def test_get_history_returns_messages(self):
        from vectrax.comm.engine_core import CoreCommEngine

        engine = CoreCommEngine()
        engine.send_text("mensaje de historial")

        history = engine.get_history()
        self.assertGreaterEqual(len(history), 2)  # mario msg + vectrax response


# ===================================================================
# 3. StarCommEngine — text + isolation
# ===================================================================

class TestStarCommEngine(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph
        graph.reset_graph()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_send_text_returns_response(self):
        from vectrax.comm.engine_star import StarCommEngine

        engine = StarCommEngine(owner="testuser")
        response = engine.send_text("nota de usuario")

        self.assertIsNotNone(response.star_id)
        self.assertEqual(response.owner, "testuser")
        self.assertNotEqual(response.text, "")

    def test_send_text_ingests_into_user_channel(self):
        from vectrax.comm.engine_star import StarCommEngine
        from vectrax import db
        from vectrax.identity import CHANNEL_USER

        engine = StarCommEngine(owner="carlos")
        response = engine.send_text("tarea de carlos")

        star = db.get_star(response.star_id)
        self.assertIsNotNone(star)
        self.assertEqual(star.channel, CHANNEL_USER)
        self.assertEqual(star.owner, "carlos")

    def test_get_context_includes_projection(self):
        from vectrax.comm.engine_star import StarCommEngine

        engine = StarCommEngine(owner="testuser")
        engine.send_text("semilla de contexto usuario")

        ctx = engine.get_context()
        self.assertEqual(ctx["engine_type"], "star")
        self.assertIn("text", ctx["capabilities"])
        self.assertIn("nucleus_projection", ctx)
        self.assertIn("navigation", ctx)


# ===================================================================
# 4. Channel separation
# ===================================================================

class TestChannelSeparation(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_mario_cannot_use_star_engine(self):
        from vectrax.comm.engine_star import StarCommEngine
        from vectrax.identity import ChannelViolation

        with self.assertRaises(ChannelViolation):
            StarCommEngine(owner="mario")

    def test_empty_owner_rejected(self):
        from vectrax.comm.engine_star import StarCommEngine

        with self.assertRaises(ValueError):
            StarCommEngine(owner="")


# ===================================================================
# 5. User isolation
# ===================================================================

class TestUserIsolation(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph
        graph.reset_graph()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_users_have_separate_stars(self):
        from vectrax.comm.engine_star import StarCommEngine
        from vectrax import db
        from vectrax.identity import CHANNEL_USER

        # Alice ingests
        alice = StarCommEngine(owner="alice")
        alice.send_text("secreto de alice")

        # Bob ingests
        bob = StarCommEngine(owner="bob")
        bob.send_text("secreto de bob")

        # Check isolation
        alice_stars = db.get_all_stars(channel=CHANNEL_USER, owner="alice")
        bob_stars = db.get_all_stars(channel=CHANNEL_USER, owner="bob")

        self.assertGreaterEqual(len(alice_stars), 1)
        self.assertGreaterEqual(len(bob_stars), 1)

        # No cross-contamination
        alice_ids = {s.id for s in alice_stars}
        bob_ids = {s.id for s in bob_stars}
        self.assertEqual(alice_ids & bob_ids, set())

    def test_users_cannot_see_creator_stars(self):
        from vectrax.comm.engine_core import CoreCommEngine
        from vectrax.comm.engine_star import StarCommEngine
        from vectrax import db
        from vectrax.identity import CHANNEL_CREATOR, CHANNEL_USER

        # Mario creates nucleus content
        core = CoreCommEngine()
        core.send_text("datos secretos del núcleo")

        # User queries their own stars — should not contain nucleus data
        user_stars = db.get_all_stars(channel=CHANNEL_USER, owner="testuser")
        creator_stars = db.get_all_stars(channel=CHANNEL_CREATOR)

        for us in user_stars:
            self.assertNotIn(us.id, {s.id for s in creator_stars})


# ===================================================================
# 6. CoreProjection safety
# ===================================================================

class TestCoreProjection(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph
        graph.reset_graph()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_projection_returns_structure_only(self):
        from vectrax.comm.engine_core import CoreCommEngine
        from vectrax.comm.projection import CoreProjection

        # Create some nucleus content
        core = CoreCommEngine()
        core.send_text("información confidencial del creador")

        # User gets projection
        proj = CoreProjection(owner="testuser")
        structure = proj.get_structure()

        # Must contain counts, not content
        self.assertIn("nucleus_stars", structure)
        self.assertIn("layers", structure)
        self.assertIn("gravity", structure)
        self.assertEqual(
            structure["_projection_note"],
            "structural_only:no_content_exposed",
        )

        # Must NOT contain any text content
        structure_str = str(structure)
        self.assertNotIn("información confidencial", structure_str)

    def test_navigation_context_has_no_content(self):
        from vectrax.comm.projection import CoreProjection

        proj = CoreProjection(owner="testuser")
        nav = proj.get_navigation_context()

        self.assertIn("thresholds", nav)
        self.assertIn("avg_distance_per_layer", nav)
        self.assertEqual(
            nav["_projection_note"],
            "navigation_only:no_content_exposed",
        )


# ===================================================================
# 7. Voice pipeline (mock)
# ===================================================================

class TestVoicePipeline(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph
        graph.reset_graph()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_voice_delegates_to_text(self):
        from vectrax.comm.engine_core import CoreCommEngine

        engine = CoreCommEngine()

        # Create a fake audio file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"fake audio content")
            audio_path = f.name

        try:
            # Mock transcription to return known text
            with patch.object(
                CoreCommEngine, "_transcribe", return_value="mensaje de voz transcrito"
            ):
                response = engine.send_voice(audio_path)

            self.assertEqual(response.source_type, "voice")
            self.assertNotEqual(response.text, "")
            self.assertNotEqual(response.star_id, "")
        finally:
            os.unlink(audio_path)

    def test_voice_file_not_found_raises(self):
        from vectrax.comm.engine_core import CoreCommEngine

        engine = CoreCommEngine()
        with self.assertRaises(FileNotFoundError):
            engine.send_voice("/nonexistent/audio.wav")


if __name__ == "__main__":
    unittest.main()
