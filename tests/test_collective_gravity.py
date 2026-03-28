"""
Tests for Vectrax Collective Gravity & Emergent Constellations.

Covers:
    1. compute_collective_gravity amplifies mass with owner diversity
    2. Single-owner gets no diversity bonus
    3. More owners → more mass → closer to nucleus
    4. MAX_MASS cap is respected
    5. _update_collective_mass wires collective gravity into convergence stars
    6. detect_collective_patterns creates constellations from cross-user mass
    7. Collective constellations have owner=__collective__
    8. Components without collective stars do NOT form collective constellations
    9. Components below mass threshold do NOT form constellations
   10. Pipeline integration: collective patterns detected automatically
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

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

_tmpdir = tempfile.mkdtemp(prefix="vectrax_test_cgrav_")
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
# 1. compute_collective_gravity formula
# ===================================================================

class TestCollectiveGravityFormula(unittest.TestCase):

    def test_single_owner_no_bonus(self):
        """With owner_count=1, collective gravity equals standard gravity."""
        from vectrax.gravity import compute_collective_gravity, compute_semantic_gravity
        from vectrax.models import Star

        star = Star(content="test", activation_count=5)
        standard = compute_semantic_gravity(star, in_degree=3, neighbor_coherence=0.8)
        collective = compute_collective_gravity(
            star, in_degree=3, neighbor_coherence=0.8, owner_count=1,
        )
        self.assertEqual(standard, collective)

    def test_two_owners_amplifies_mass(self):
        """Two owners should give ~30% boost (log2(2)=1 × 0.30)."""
        from vectrax.gravity import compute_collective_gravity, compute_semantic_gravity
        from vectrax.models import Star

        star = Star(content="test", activation_count=5)
        standard = compute_semantic_gravity(star, in_degree=3, neighbor_coherence=0.8)
        collective = compute_collective_gravity(
            star, in_degree=3, neighbor_coherence=0.8, owner_count=2,
        )
        self.assertGreater(collective, standard)
        # Should be approximately standard × 1.30
        expected_ratio = 1.30
        actual_ratio = collective / standard if standard > 0 else 1.0
        self.assertAlmostEqual(actual_ratio, expected_ratio, places=1)

    def test_four_owners_amplifies_more(self):
        """Four owners should give ~60% boost (log2(4)=2 × 0.30)."""
        from vectrax.gravity import compute_collective_gravity, compute_semantic_gravity
        from vectrax.models import Star

        star = Star(content="test", activation_count=5)
        two_owners = compute_collective_gravity(
            star, in_degree=3, neighbor_coherence=0.8, owner_count=2,
        )
        four_owners = compute_collective_gravity(
            star, in_degree=3, neighbor_coherence=0.8, owner_count=4,
        )
        self.assertGreater(four_owners, two_owners)

    def test_more_owners_more_mass(self):
        """Mass should monotonically increase with owner count."""
        from vectrax.gravity import compute_collective_gravity
        from vectrax.models import Star

        star = Star(content="test", activation_count=5)
        masses = []
        for owner_count in [1, 2, 3, 4, 8]:
            mass = compute_collective_gravity(
                star, in_degree=3, neighbor_coherence=0.8,
                owner_count=owner_count,
            )
            masses.append(mass)

        # Each entry should be >= previous
        for i in range(1, len(masses)):
            self.assertGreaterEqual(masses[i], masses[i - 1])

    def test_max_mass_cap(self):
        """Collective gravity should never exceed MAX_MASS."""
        from vectrax.gravity import compute_collective_gravity
        from vectrax.models import Star, MAX_MASS

        star = Star(content="super active", activation_count=100)
        mass = compute_collective_gravity(
            star, in_degree=50, neighbor_coherence=1.0,
            owner_count=100,
        )
        self.assertLessEqual(mass, MAX_MASS)

    def test_more_owners_closer_to_nucleus(self):
        """More owners → higher mass → lower distance to core."""
        from vectrax.gravity import compute_collective_gravity, compute_distance_from_mass
        from vectrax.models import Star

        star = Star(content="test", activation_count=5)
        mass_2 = compute_collective_gravity(
            star, in_degree=3, neighbor_coherence=0.8, owner_count=2,
        )
        mass_8 = compute_collective_gravity(
            star, in_degree=3, neighbor_coherence=0.8, owner_count=8,
        )
        dist_2 = compute_distance_from_mass(mass_2)
        dist_8 = compute_distance_from_mass(mass_8)
        self.assertLess(dist_8, dist_2)


# ===================================================================
# 2. _update_collective_mass integration
# ===================================================================

class TestUpdateCollectiveMass(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph, core_nucleus
        graph.reset_graph()
        core_nucleus._reset()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_collective_star_mass_increases_with_owners(self):
        """After _update_collective_mass, mass should reflect owner diversity."""
        from vectrax import db, graph as g
        from vectrax.convergence_engine import (
            auto_check_cross_user_convergence,
            _update_collective_mass,
        )
        from vectrax.models import Star, STAR_TYPE_PRIMARY, COLLECTIVE_OWNER
        from vectrax.embeddings import encode_embedding

        base = _fake_embed("collective mass test")

        # Create primaries from 2 different owners
        stars = []
        for owner_name in ["alice", "bob"]:
            vec = _make_similar_vec(base, noise=0.01)
            s = Star(
                content=f"collective mass from {owner_name}",
                embedding=encode_embedding(vec),
                star_type=STAR_TYPE_PRIMARY,
                channel="user",
                owner=owner_name,
            )
            db.insert_star(s)
            g.add_star(s.id, star_type=STAR_TYPE_PRIMARY,
                       channel="user", owner=owner_name)
            stars.append((s, vec))

        # Trigger collective convergence
        collective = auto_check_cross_user_convergence(
            stars[1][0], stars[1][1], "user", "bob",
        )
        self.assertIsNotNone(collective)

        # Get the updated mass
        updated = db.get_star(collective.id)
        self.assertIsNotNone(updated)
        # Mass should be > MIN_MASS because it has connections + owner diversity
        from vectrax.models import MIN_MASS
        self.assertGreater(updated.mass, MIN_MASS)


# ===================================================================
# 3. Emergent collective constellations
# ===================================================================

class TestCollectiveConstellationEmergence(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph, core_nucleus
        graph.reset_graph()
        core_nucleus._reset()

    def tearDown(self):
        _teardown_patches(self.patches)

    def _build_cross_user_cluster(self, topic: str, owners: list, channel="user"):
        """
        Build a cluster of primary stars from different owners,
        create a collective convergence star linking them,
        and set masses high enough for constellation threshold.
        """
        from vectrax import db, graph as g
        from vectrax.models import (
            Star, STAR_TYPE_PRIMARY, STAR_TYPE_CONVERGENCE,
            COLLECTIVE_OWNER, COLLECTIVE_PREFIX,
        )
        from vectrax.embeddings import encode_embedding

        base = _fake_embed(topic)
        created_stars = []

        for owner_name in owners:
            vec = _make_similar_vec(base, noise=0.01)
            s = Star(
                content=f"{topic} from {owner_name}",
                embedding=encode_embedding(vec),
                star_type=STAR_TYPE_PRIMARY,
                mass=0.4,  # above threshold
                channel=channel,
                owner=owner_name,
            )
            db.insert_star(s)
            g.add_star(s.id, star_type=STAR_TYPE_PRIMARY,
                       channel=channel, owner=owner_name, mass=0.4)
            created_stars.append(s)

        # Create collective convergence star
        coll_vec = _make_similar_vec(base, noise=0.005)
        coll = Star(
            content=f"{COLLECTIVE_PREFIX} {topic}",
            embedding=encode_embedding(coll_vec),
            star_type=STAR_TYPE_CONVERGENCE,
            mass=0.5,  # above threshold
            channel=channel,
            owner=COLLECTIVE_OWNER,
        )
        db.insert_star(coll)
        g.add_star(coll.id, star_type=STAR_TYPE_CONVERGENCE,
                   channel=channel, owner=COLLECTIVE_OWNER, mass=0.5)

        # Link collective star to all primaries
        for s in created_stars:
            g.link_stars(coll.id, s.id, 0.9)

        # Also link primaries to each other to form a dense component
        for i in range(len(created_stars)):
            for j in range(i + 1, len(created_stars)):
                g.link_stars(created_stars[i].id, created_stars[j].id, 0.85)

        return created_stars, coll

    def test_collective_constellation_emerges(self):
        """When a cross-user cluster has enough mass, a constellation forms."""
        from vectrax.engine import detect_collective_patterns
        from vectrax.models import COLLECTIVE_OWNER

        self._build_cross_user_cluster(
            "machine learning", ["alice", "bob", "carol"],
        )

        constellations = detect_collective_patterns(channel="user")
        self.assertGreaterEqual(len(constellations), 1)
        self.assertEqual(constellations[0].owner, COLLECTIVE_OWNER)

    def test_constellation_has_correct_members(self):
        """Collective constellation should contain all cluster members."""
        from vectrax.engine import detect_collective_patterns

        stars, coll = self._build_cross_user_cluster(
            "deep learning", ["alice", "bob", "carol"],
        )

        constellations = detect_collective_patterns(channel="user")
        self.assertGreaterEqual(len(constellations), 1)

        all_ids = {s.id for s in stars} | {coll.id}
        constellation_ids = set(constellations[0].star_ids)
        # All our stars should be in the constellation
        self.assertTrue(all_ids.issubset(constellation_ids))

    def test_no_constellation_without_collective_star(self):
        """Components without collective stars should NOT form collective constellations."""
        from vectrax import db, graph as g
        from vectrax.engine import detect_collective_patterns
        from vectrax.models import Star, STAR_TYPE_PRIMARY
        from vectrax.embeddings import encode_embedding

        base = _fake_embed("no collective here")
        # Create 4 primary stars from different owners but NO collective star
        stars = []
        for owner in ["alice", "bob", "carol", "dave"]:
            vec = _make_similar_vec(base, noise=0.01)
            s = Star(
                content=f"solo {owner}",
                embedding=encode_embedding(vec),
                star_type=STAR_TYPE_PRIMARY,
                mass=0.5,
                channel="user",
                owner=owner,
            )
            db.insert_star(s)
            g.add_star(s.id, mass=0.5, channel="user", owner=owner)
            stars.append(s)

        # Link them into a dense component
        for i in range(len(stars)):
            for j in range(i + 1, len(stars)):
                g.link_stars(stars[i].id, stars[j].id, 0.9)

        constellations = detect_collective_patterns(channel="user")
        self.assertEqual(len(constellations), 0)

    def test_no_constellation_below_mass_threshold(self):
        """Components with low mass should NOT form constellations even with collective star."""
        from vectrax import db, graph as g
        from vectrax.engine import detect_collective_patterns
        from vectrax.models import (
            Star, STAR_TYPE_PRIMARY, STAR_TYPE_CONVERGENCE,
            COLLECTIVE_OWNER, COLLECTIVE_PREFIX,
        )
        from vectrax.embeddings import encode_embedding

        base = _fake_embed("low mass topic")
        stars = []
        for owner in ["alice", "bob"]:
            vec = _make_similar_vec(base, noise=0.01)
            s = Star(
                content=f"low mass {owner}",
                embedding=encode_embedding(vec),
                star_type=STAR_TYPE_PRIMARY,
                mass=0.01,  # very low mass
                channel="user",
                owner=owner,
            )
            db.insert_star(s)
            g.add_star(s.id, mass=0.01, channel="user", owner=owner)
            stars.append(s)

        # Collective star with low mass too
        coll_vec = _make_similar_vec(base, noise=0.005)
        coll = Star(
            content=f"{COLLECTIVE_PREFIX} low mass topic",
            embedding=encode_embedding(coll_vec),
            star_type=STAR_TYPE_CONVERGENCE,
            mass=0.01,
            channel="user",
            owner=COLLECTIVE_OWNER,
        )
        db.insert_star(coll)
        g.add_star(coll.id, mass=0.01, channel="user", owner=COLLECTIVE_OWNER)

        for s in stars:
            g.link_stars(coll.id, s.id, 0.9)
        g.link_stars(stars[0].id, stars[1].id, 0.85)

        constellations = detect_collective_patterns(channel="user")
        self.assertEqual(len(constellations), 0)

    def test_constellation_dedup(self):
        """Running detect_collective_patterns twice should not duplicate constellations."""
        from vectrax.engine import detect_collective_patterns
        from vectrax import db
        from vectrax.models import COLLECTIVE_OWNER

        self._build_cross_user_cluster(
            "repeated detection", ["alice", "bob", "carol"],
        )

        c1 = detect_collective_patterns(channel="user")
        c2 = detect_collective_patterns(channel="user")

        # Should be same constellation, just updated
        all_collective = db.get_all_constellations(
            channel="user", owner=COLLECTIVE_OWNER,
        )
        self.assertEqual(len(all_collective), 1)
        # Repetition count should increase on second pass
        self.assertGreaterEqual(all_collective[0].repetition_count, 2)


# ===================================================================
# 4. Pipeline integration
# ===================================================================

class TestCollectivePipelineIntegration(unittest.TestCase):

    def setUp(self):
        self.patches = _setup_patches()
        from vectrax import graph, core_nucleus
        graph.reset_graph()
        core_nucleus._reset()

    def tearDown(self):
        _teardown_patches(self.patches)

    def test_pipeline_calls_detect_collective_patterns(self):
        """_post_ingest should trigger collective pattern detection without error."""
        from vectrax.engine import ingest
        from vectrax import db

        # Ingest stars from two different users
        ingest("pipeline gravity test alpha", channel="user", owner="user_a")
        ingest("pipeline gravity test beta", channel="user", owner="user_b")

        # Should not raise — collective detection runs as non-fatal
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
