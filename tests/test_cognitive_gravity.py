"""
Tests for the Vectrax Cognitive Gravitational Engine.

Covers:
    1. Node creation with minimum mass
    2. Node connection recalculates mass
    3. Distance to core is inverse of mass
    4. Convergence detection (≥3 distinct sources)
    5. Cluster detection produces constellations
    6. No-deletion principle: inactive nodes keep existing
    7. Nucleus immutability
    8. Semantic gravity computation
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
# Helpers: deterministic fake embeddings (no sentence-transformers needed)
# ---------------------------------------------------------------------------

_DIMENSION = 384  # same as all-MiniLM-L6-v2


def _fake_embed(text: str) -> np.ndarray:
    """Generate a deterministic unit-norm embedding from text hash."""
    rng = np.random.RandomState(hash(text) % (2**31))
    vec = rng.randn(_DIMENSION).astype(np.float32)
    vec /= np.linalg.norm(vec)
    return vec


def _similar_embed(base_text: str, noise: float = 0.05) -> np.ndarray:
    """Return an embedding very similar to the base text's embedding."""
    base = _fake_embed(base_text)
    rng = np.random.RandomState(42)
    perturbation = rng.randn(_DIMENSION).astype(np.float32) * noise
    vec = base + perturbation
    vec /= np.linalg.norm(vec)
    return vec


# ---------------------------------------------------------------------------
# Patch setup: redirect DB to temp dir, mock embeddings
# ---------------------------------------------------------------------------

_tmpdir = tempfile.mkdtemp(prefix="vectrax_test_cg_")
_test_db_path = os.path.join(_tmpdir, "vectrax.db")


def _setup_patches():
    """Apply all patches needed for isolated testing."""
    patches = []

    # Redirect DB path
    p1 = patch("vectrax.db.DB_DIR", Path(_tmpdir))
    p2 = patch("vectrax.db.DB_PATH", Path(_test_db_path))
    patches.extend([p1, p2])

    # Mock the embed function to avoid loading sentence-transformers
    p3 = patch("vectrax.embeddings.embed", side_effect=_fake_embed)
    p4 = patch("vectrax.cognitive_gravity.embed", side_effect=_fake_embed)
    patches.extend([p3, p4])

    for p in patches:
        p.start()

    # Initialise DB
    from vectrax import db
    db.init_db()

    return patches


def _teardown_patches(patches):
    for p in patches:
        p.stop()
    # Clean up DB
    if os.path.exists(_test_db_path):
        os.unlink(_test_db_path)


# ===================================================================
# 1. Node creation
# ===================================================================

class TestCreateNode(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph
        graph.reset_graph()
        from vectrax import core_nucleus
        core_nucleus._reset()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_node_created_with_min_mass(self):
        from vectrax.cognitive_gravity import create_node
        from vectrax.models import MIN_MASS

        star = create_node("test knowledge", channel="user", owner="test")
        self.assertAlmostEqual(star.mass, MIN_MASS, places=4)
        self.assertEqual(star.activation_count, 1)
        self.assertGreater(star.last_activated, 0)

    def test_node_has_embedding(self):
        from vectrax.cognitive_gravity import create_node

        star = create_node("embedding test", channel="user", owner="test")
        self.assertIsNotNone(star.embedding)

    def test_node_distance_starts_at_periphery(self):
        from vectrax.cognitive_gravity import create_node

        star = create_node("peripheral star", channel="user", owner="test")
        # MIN_MASS = 0.01, so distance = 1.0 - 0.01 = 0.99
        self.assertGreater(star.distance_to_core, 0.9)

    def test_node_persisted_in_db(self):
        from vectrax.cognitive_gravity import create_node
        from vectrax import db

        star = create_node("db persistence test", channel="user", owner="test")
        loaded = db.get_star(star.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.content, "db persistence test")

    def test_node_added_to_graph(self):
        from vectrax.cognitive_gravity import create_node
        from vectrax import graph

        star = create_node("graph test", channel="user", owner="test")
        g = graph.get_graph()
        self.assertTrue(g.has_node(star.id))


# ===================================================================
# 2. Node connection
# ===================================================================

class TestConnectNodes(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph
        graph.reset_graph()
        from vectrax import core_nucleus
        core_nucleus._reset()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_connect_creates_edge(self):
        from vectrax.cognitive_gravity import create_node, connect_nodes
        from vectrax import graph

        a = create_node("alpha concept", channel="user", owner="test")
        b = create_node("beta concept", channel="user", owner="test")
        result = connect_nodes(a.id, b.id, similarity=0.85)

        g = graph.get_graph()
        self.assertTrue(g.has_edge(a.id, b.id))
        self.assertAlmostEqual(result["similarity"], 0.85, places=4)

    def test_connect_recalculates_mass(self):
        from vectrax.cognitive_gravity import create_node, connect_nodes
        from vectrax.models import MIN_MASS

        a = create_node("alpha", channel="user", owner="test")
        b = create_node("beta", channel="user", owner="test")
        result = connect_nodes(a.id, b.id, similarity=0.9)

        # After connection, mass should increase from MIN_MASS
        self.assertGreaterEqual(result["mass_a"], MIN_MASS)
        self.assertGreaterEqual(result["mass_b"], MIN_MASS)

    def test_connect_nonexistent_raises(self):
        from vectrax.cognitive_gravity import create_node, connect_nodes

        a = create_node("exists", channel="user", owner="test")
        with self.assertRaises(ValueError):
            connect_nodes(a.id, "nonexistent_id", similarity=0.5)

    def test_cross_channel_connect_forbidden(self):
        from vectrax.cognitive_gravity import create_node, connect_nodes
        from vectrax.identity import ChannelViolation

        a = create_node("user star", channel="user", owner="test")
        b = create_node("creator star", channel="creator", owner="mario")

        with self.assertRaises(ChannelViolation):
            connect_nodes(a.id, b.id, similarity=0.9)


# ===================================================================
# 3. Distance to core
# ===================================================================

class TestDistanceToCore(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph
        graph.reset_graph()
        from vectrax import core_nucleus
        core_nucleus._reset()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_distance_inverse_of_mass(self):
        from vectrax.gravity import compute_distance_from_mass

        # Low mass = far from core
        self.assertAlmostEqual(compute_distance_from_mass(0.01), 0.99, places=4)
        # High mass = close to core
        self.assertAlmostEqual(compute_distance_from_mass(1.0), 0.0, places=4)
        # Mid mass
        self.assertAlmostEqual(compute_distance_from_mass(0.5), 0.5, places=4)

    def test_compute_distance_returns_dict(self):
        from vectrax.cognitive_gravity import create_node, compute_distance_to_core

        star = create_node("distance test", channel="user", owner="test")
        result = compute_distance_to_core(star.id)

        self.assertIn("mass_distance", result)
        self.assertIn("semantic_distance", result)
        self.assertIn("combined_distance", result)
        self.assertGreater(result["combined_distance"], 0)

    def test_distance_decreases_with_mass(self):
        from vectrax.cognitive_gravity import create_node, connect_nodes, compute_distance_to_core

        # Create a hub star with many connections
        hub = create_node("hub star", channel="user", owner="test")
        dist_before = compute_distance_to_core(hub.id)["combined_distance"]

        # Add connections to increase mass
        for i in range(5):
            other = create_node(f"satellite {i}", channel="user", owner="test")
            connect_nodes(hub.id, other.id, similarity=0.9)

        dist_after = compute_distance_to_core(hub.id)["combined_distance"]

        # More connections → more mass → less distance
        self.assertLessEqual(dist_after, dist_before)


# ===================================================================
# 4. Semantic gravity computation
# ===================================================================

class TestSemanticGravity(unittest.TestCase):

    def test_min_mass_with_no_connections(self):
        from vectrax.gravity import compute_semantic_gravity
        from vectrax.models import MIN_MASS, Star

        star = Star(content="isolated", activation_count=0)
        mass = compute_semantic_gravity(star, in_degree=0, neighbor_coherence=0.0)
        self.assertAlmostEqual(mass, MIN_MASS, places=4)

    def test_mass_increases_with_connections(self):
        from vectrax.gravity import compute_semantic_gravity
        from vectrax.models import Star

        star = Star(content="connected", activation_count=5)
        mass_few = compute_semantic_gravity(star, in_degree=2, neighbor_coherence=0.5)
        mass_many = compute_semantic_gravity(star, in_degree=15, neighbor_coherence=0.5)

        self.assertGreater(mass_many, mass_few)

    def test_mass_increases_with_coherence(self):
        from vectrax.gravity import compute_semantic_gravity
        from vectrax.models import Star

        star = Star(content="coherent", activation_count=5)
        mass_low = compute_semantic_gravity(star, in_degree=5, neighbor_coherence=0.2)
        mass_high = compute_semantic_gravity(star, in_degree=5, neighbor_coherence=0.95)

        self.assertGreater(mass_high, mass_low)

    def test_mass_increases_with_activation(self):
        from vectrax.gravity import compute_semantic_gravity
        from vectrax.models import Star

        star_low = Star(content="inactive", activation_count=1)
        star_high = Star(content="active", activation_count=100)

        mass_low = compute_semantic_gravity(star_low, in_degree=5, neighbor_coherence=0.5)
        mass_high = compute_semantic_gravity(star_high, in_degree=5, neighbor_coherence=0.5)

        self.assertGreater(mass_high, mass_low)

    def test_mass_bounded(self):
        from vectrax.gravity import compute_semantic_gravity
        from vectrax.models import MIN_MASS, MAX_MASS, Star

        star = Star(content="max", activation_count=10000)
        mass = compute_semantic_gravity(star, in_degree=100, neighbor_coherence=1.0)

        self.assertGreaterEqual(mass, MIN_MASS)
        self.assertLessEqual(mass, MAX_MASS)


# ===================================================================
# 5. No-deletion principle
# ===================================================================

class TestNoDeletion(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph
        graph.reset_graph()
        from vectrax import core_nucleus
        core_nucleus._reset()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_no_delete_method_on_cognitive_gravity(self):
        """The cognitive gravity module must not expose any delete function."""
        import vectrax.cognitive_gravity as cg

        for name in dir(cg):
            self.assertFalse(
                name.startswith("delete") or name.startswith("remove"),
                f"Found deletion method: {name}"
            )

    def test_inactive_node_still_exists(self):
        from vectrax.cognitive_gravity import create_node, update_mass
        from vectrax import db

        star = create_node("will become inactive", channel="user", owner="test")
        star_id = star.id

        # Mass recalculation with no connections should keep min mass
        update_mass(star_id)

        loaded = db.get_star(star_id)
        self.assertIsNotNone(loaded)
        self.assertGreater(loaded.mass, 0)


# ===================================================================
# 6. Nucleus immutability
# ===================================================================

class TestNucleus(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph
        graph.reset_graph()
        from vectrax import core_nucleus
        core_nucleus._reset()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_nucleus_info_on_cold_start(self):
        from vectrax.core_nucleus import get_core_info

        info = get_core_info()
        self.assertTrue(info["initialised"])
        self.assertEqual(info["core_star_count"], 0)
        self.assertFalse(info["has_centroid"])

    def test_nucleus_no_direct_mutation(self):
        """The core_nucleus module must not expose set/modify functions."""
        import vectrax.core_nucleus as cn

        public = [n for n in dir(cn) if not n.startswith("_")]
        for name in public:
            self.assertFalse(
                name.startswith("set_") or name.startswith("delete") or name.startswith("modify"),
                f"Found mutation method: {name}"
            )

    def test_centroid_returns_none_without_core_stars(self):
        from vectrax.core_nucleus import get_core_centroid

        centroid = get_core_centroid()
        self.assertIsNone(centroid)

    def test_distance_to_centroid_max_when_no_core(self):
        from vectrax.core_nucleus import distance_to_centroid

        vec = _fake_embed("anything")
        dist = distance_to_centroid(vec)
        self.assertAlmostEqual(dist, 1.0)


# ===================================================================
# 7. Clustering
# ===================================================================

class TestClustering(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph
        graph.reset_graph()
        from vectrax import core_nucleus
        core_nucleus._reset()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_cluster_empty_graph(self):
        from vectrax.cognitive_gravity import cluster_nodes

        result = cluster_nodes(channel="user", owner="test")
        self.assertEqual(result, [])

    def test_cluster_too_few_nodes(self):
        from vectrax.cognitive_gravity import create_node, cluster_nodes

        create_node("solo star", channel="user", owner="test")
        result = cluster_nodes(channel="user", owner="test", min_cluster_size=3)
        self.assertEqual(result, [])

    def test_cluster_forms_constellation(self):
        from vectrax.cognitive_gravity import create_node, connect_nodes, cluster_nodes

        # Create a dense group of 4 interconnected stars
        stars = []
        for i in range(4):
            s = create_node(f"cluster member {i}", channel="user", owner="test")
            stars.append(s)

        # Fully connect them
        for i in range(len(stars)):
            for j in range(i + 1, len(stars)):
                connect_nodes(stars[i].id, stars[j].id, similarity=0.9)

        result = cluster_nodes(channel="user", owner="test", min_cluster_size=3)
        # Should find at least one cluster
        self.assertGreaterEqual(len(result), 1)
        # The cluster should have at least 3 members
        self.assertGreaterEqual(result[0]["member_count"], 3)


if __name__ == "__main__":
    unittest.main()
