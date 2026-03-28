"""
Tests for the Vectrax Convergence Engine and Trajectory System.

Covers:
    1. Trajectory recording and retrieval
    2. Automatic convergence detection (pipeline hook)
    3. Convergence coordinates listed by distance to nucleus
    4. Knowledge retrieval around a convergence coordinate
    5. Navigation by convergence coordinates
    6. Convergence star deduplication
    7. Convergence only from primary stars (not cascading)
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Deterministic fake embeddings
# ---------------------------------------------------------------------------

_DIMENSION = 384


def _fake_embed(text: str) -> np.ndarray:
    """Deterministic unit-norm embedding from text hash."""
    rng = np.random.RandomState(hash(text) % (2**31))
    vec = rng.randn(_DIMENSION).astype(np.float32)
    vec /= np.linalg.norm(vec)
    return vec


def _make_similar_vec(base_vec: np.ndarray, noise: float = 0.03) -> np.ndarray:
    """Return a vector very similar to base_vec with small perturbation."""
    rng = np.random.RandomState(42)
    perturbed = base_vec + rng.randn(_DIMENSION).astype(np.float32) * noise
    perturbed /= np.linalg.norm(perturbed)
    return perturbed


# ---------------------------------------------------------------------------
# Patch setup
# ---------------------------------------------------------------------------

_tmpdir = tempfile.mkdtemp(prefix="vectrax_test_conv_")
_test_db_path = os.path.join(_tmpdir, "vectrax.db")


def _setup_patches():
    patches = []
    p1 = patch("vectrax.db.DB_DIR", Path(_tmpdir))
    p2 = patch("vectrax.db.DB_PATH", Path(_test_db_path))
    p3 = patch("vectrax.embeddings.embed", side_effect=_fake_embed)
    p4 = patch("vectrax.cognitive_gravity.embed", side_effect=_fake_embed)
    p5 = patch("vectrax.convergence_engine.embed", side_effect=_fake_embed)
    patches.extend([p1, p2, p3, p4, p5])
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
# 1. Trajectory recording
# ===================================================================

class TestTrajectory(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph, core_nucleus
        graph.reset_graph()
        core_nucleus._reset()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_record_and_retrieve_trajectory(self):
        from vectrax.trajectory import record_point, get_user_trajectory
        from vectrax.cognitive_gravity import create_node

        star = create_node("first step", channel="user", owner="alice")
        record_point(star.id, "user", "alice")

        traj = get_user_trajectory("user", "alice")
        self.assertGreaterEqual(len(traj), 1)

    def test_trajectory_order_is_sequential(self):
        from vectrax.trajectory import record_point, get_user_trajectory
        from vectrax.cognitive_gravity import create_node

        stars = []
        for i in range(5):
            s = create_node(f"step {i}", channel="user", owner="bob")
            record_point(s.id, "user", "bob")
            stars.append(s)

        traj = get_user_trajectory("user", "bob")
        self.assertEqual(len(traj), 5)
        # Verify sequential order
        for i, point in enumerate(traj):
            self.assertEqual(point["star_id"], stars[i].id)

    def test_trajectory_direction_returns_vector(self):
        from vectrax.trajectory import record_point, get_trajectory_direction
        from vectrax.cognitive_gravity import create_node

        for i in range(3):
            s = create_node(f"direction test {i}", channel="user", owner="carol")
            record_point(s.id, "user", "carol")

        direction = get_trajectory_direction("user", "carol")
        self.assertIsNotNone(direction)
        self.assertEqual(direction.shape[0], _DIMENSION)
        # Should be normalised
        self.assertAlmostEqual(float(np.linalg.norm(direction)), 1.0, places=3)

    def test_trajectory_direction_none_for_empty(self):
        from vectrax.trajectory import get_trajectory_direction

        direction = get_trajectory_direction("user", "nobody")
        self.assertIsNone(direction)


# ===================================================================
# 2. Auto convergence detection
# ===================================================================

class TestAutoConvergence(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph, core_nucleus
        graph.reset_graph()
        core_nucleus._reset()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_no_convergence_with_few_stars(self):
        from vectrax.convergence_engine import auto_check_convergence
        from vectrax.cognitive_gravity import create_node
        from vectrax.embeddings import decode_embedding

        star = create_node("lonely star", channel="user", owner="test")
        vec = decode_embedding(star.embedding)
        result = auto_check_convergence(star, vec, "user", "test")
        self.assertIsNone(result)

    def test_convergence_skips_convergence_stars(self):
        from vectrax.convergence_engine import auto_check_convergence
        from vectrax.models import STAR_TYPE_CONVERGENCE, Star

        star = Star(content="[CONVERGENCE] fake", star_type=STAR_TYPE_CONVERGENCE)
        vec = _fake_embed("fake")
        result = auto_check_convergence(star, vec, "user", "test")
        self.assertIsNone(result)


# ===================================================================
# 3. Convergence coordinates
# ===================================================================

class TestCoordinates(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph, core_nucleus
        graph.reset_graph()
        core_nucleus._reset()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_list_coordinates_empty(self):
        from vectrax.convergence_engine import list_coordinates

        coords = list_coordinates(channel="user", owner="test")
        self.assertEqual(coords, [])

    def test_coordinates_ordered_by_distance(self):
        """If convergence stars exist, they should be ordered by distance_to_core."""
        from vectrax import db
        from vectrax.convergence_engine import list_coordinates
        from vectrax.models import STAR_TYPE_CONVERGENCE, Star, CONVERGENCE_PREFIX
        from vectrax.embeddings import encode_embedding

        # Manually insert convergence stars with different distances
        for i, dist in enumerate([0.3, 0.9, 0.1, 0.5]):
            vec = _fake_embed(f"coord {i}")
            star = Star(
                content=f"{CONVERGENCE_PREFIX} topic {i}",
                embedding=encode_embedding(vec),
                star_type=STAR_TYPE_CONVERGENCE,
                distance_to_core=dist,
                mass=1.0 - dist,
                channel="user",
                owner="test",
            )
            db.insert_star(star)

        coords = list_coordinates(channel="user", owner="test")
        self.assertEqual(len(coords), 4)
        # Should be ordered by distance ascending
        distances = [c["distance_to_core"] for c in coords]
        self.assertEqual(distances, sorted(distances))


# ===================================================================
# 4. Knowledge around coordinate
# ===================================================================

class TestKnowledgeAround(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph, core_nucleus
        graph.reset_graph()
        core_nucleus._reset()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_knowledge_around_nonexistent_raises(self):
        from vectrax.convergence_engine import get_knowledge_around

        with self.assertRaises(ValueError):
            get_knowledge_around("nonexistent_id")

    def test_knowledge_around_non_convergence_raises(self):
        from vectrax.convergence_engine import get_knowledge_around
        from vectrax.cognitive_gravity import create_node

        primary = create_node("I am primary", channel="user", owner="test")
        with self.assertRaises(ValueError):
            get_knowledge_around(primary.id)

    def test_knowledge_around_returns_connected_stars(self):
        from vectrax import db, graph as g
        from vectrax.convergence_engine import get_knowledge_around
        from vectrax.cognitive_gravity import create_node
        from vectrax.models import STAR_TYPE_CONVERGENCE, Star, CONVERGENCE_PREFIX
        from vectrax.embeddings import encode_embedding

        # Create some primary stars
        p1 = create_node("alpha knowledge", channel="user", owner="test")
        p2 = create_node("beta knowledge", channel="user", owner="test")

        # Create a convergence star manually and link to primaries
        vec = _fake_embed("convergence point")
        conv = Star(
            content=f"{CONVERGENCE_PREFIX} convergence point",
            embedding=encode_embedding(vec),
            star_type=STAR_TYPE_CONVERGENCE,
            channel="user",
            owner="test",
        )
        db.insert_star(conv)
        g.add_star(conv.id, star_type=STAR_TYPE_CONVERGENCE)
        g.link_stars(conv.id, p1.id, 0.9)
        g.link_stars(conv.id, p2.id, 0.85)

        result = get_knowledge_around(conv.id)
        self.assertEqual(result["coordinate"]["id"], conv.id)
        self.assertGreaterEqual(result["total_connected"], 2)
        self.assertGreaterEqual(len(result["stars"]), 2)

    def test_knowledge_ordered_by_distance_to_nucleus(self):
        from vectrax import db, graph as g
        from vectrax.convergence_engine import get_knowledge_around
        from vectrax.models import STAR_TYPE_CONVERGENCE, Star, CONVERGENCE_PREFIX
        from vectrax.embeddings import encode_embedding

        # Create stars with different distances
        star_ids = []
        for i, dist in enumerate([0.8, 0.2, 0.5]):
            vec = _fake_embed(f"star dist {i}")
            s = Star(
                content=f"star {i}",
                embedding=encode_embedding(vec),
                distance_to_core=dist,
                mass=1.0 - dist,
                channel="user",
                owner="test",
            )
            db.insert_star(s)
            g.add_star(s.id)
            star_ids.append(s.id)

        # Convergence coordinate
        cvec = _fake_embed("coord center")
        conv = Star(
            content=f"{CONVERGENCE_PREFIX} center",
            embedding=encode_embedding(cvec),
            star_type=STAR_TYPE_CONVERGENCE,
            channel="user",
            owner="test",
        )
        db.insert_star(conv)
        g.add_star(conv.id, star_type=STAR_TYPE_CONVERGENCE)
        for sid in star_ids:
            g.link_stars(conv.id, sid, 0.9)

        result = get_knowledge_around(conv.id)
        distances = [s["distance_to_core"] for s in result["stars"]]
        self.assertEqual(distances, sorted(distances))


# ===================================================================
# 5. Navigation by coordinates
# ===================================================================

class TestNavigation(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph, core_nucleus
        graph.reset_graph()
        core_nucleus._reset()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_navigate_with_no_coordinates_returns_fallback(self):
        from vectrax.convergence_engine import navigate_by_coordinates

        result = navigate_by_coordinates("any query", channel="user", owner="test")
        self.assertEqual(result["coordinates_used"], 0)
        self.assertIn("fallback", result)

    def test_navigate_returns_query_echo(self):
        from vectrax.convergence_engine import navigate_by_coordinates

        result = navigate_by_coordinates("my question", channel="user", owner="test")
        self.assertEqual(result["query"], "my question")


# ===================================================================
# 6. Star type integrity
# ===================================================================

class TestStarTypeIntegrity(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph, core_nucleus
        graph.reset_graph()
        core_nucleus._reset()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_created_node_is_primary(self):
        from vectrax.cognitive_gravity import create_node
        from vectrax.models import STAR_TYPE_PRIMARY

        star = create_node("test node", channel="user", owner="test")
        self.assertEqual(star.star_type, STAR_TYPE_PRIMARY)

    def test_convergence_stars_marked_correctly(self):
        from vectrax import db
        from vectrax.models import STAR_TYPE_CONVERGENCE, Star, CONVERGENCE_PREFIX
        from vectrax.embeddings import encode_embedding

        vec = _fake_embed("conv test")
        star = Star(
            content=f"{CONVERGENCE_PREFIX} conv test",
            embedding=encode_embedding(vec),
            star_type=STAR_TYPE_CONVERGENCE,
            channel="user",
            owner="test",
        )
        db.insert_star(star)

        loaded = db.get_star(star.id)
        self.assertEqual(loaded.star_type, STAR_TYPE_CONVERGENCE)

    def test_convergence_query_filters_correctly(self):
        from vectrax import db
        from vectrax.models import STAR_TYPE_CONVERGENCE, STAR_TYPE_PRIMARY, Star, CONVERGENCE_PREFIX
        from vectrax.embeddings import encode_embedding

        # Insert 2 primary + 1 convergence
        for i in range(2):
            vec = _fake_embed(f"primary {i}")
            db.insert_star(Star(
                content=f"primary {i}",
                embedding=encode_embedding(vec),
                star_type=STAR_TYPE_PRIMARY,
                channel="user", owner="test",
            ))

        vec = _fake_embed("conv")
        db.insert_star(Star(
            content=f"{CONVERGENCE_PREFIX} conv",
            embedding=encode_embedding(vec),
            star_type=STAR_TYPE_CONVERGENCE,
            channel="user", owner="test",
        ))

        convergence_stars = db.get_convergence_stars(channel="user", owner="test")
        self.assertEqual(len(convergence_stars), 1)
        self.assertEqual(convergence_stars[0].star_type, STAR_TYPE_CONVERGENCE)


if __name__ == "__main__":
    unittest.main()
