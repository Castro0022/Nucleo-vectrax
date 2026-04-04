"""
Gravity scoring system.

gravity = repetition_count × coherence_score × success_rate

Layer assignment:
  gravity >= 0.6  →  core   (high-confidence, frequently confirmed patterns)
  gravity >= 0.3  →  mid    (emerging patterns, some evidence)
  gravity <  0.3  →  outer  (new or unconfirmed events)

Normalised repetition uses a logarithmic scale so that a star with 10
repetitions doesn't dominate one with 5 repetitions unboundedly.
"""
from __future__ import annotations

import math
from typing import List

from vectrax.models import (
    COLLECTIVE_OWNER_DIVERSITY_WEIGHT,
    CREATOR_INITIAL_MASS,
    GRAVITY_CORE_THRESHOLD,
    GRAVITY_MID_THRESHOLD,
    LAYER_CORE,
    LAYER_MID,
    LAYER_OUTER,
    MASS_ACTIVATION_WEIGHT,
    MASS_COHERENCE_WEIGHT,
    MASS_CONNECTION_WEIGHT,
    MAX_MASS,
    MIN_MASS,
    Constellation,
    Star,
    UserStar,
)


# ---------------------------------------------------------------------------
# Core scoring functions
# ---------------------------------------------------------------------------

def _normalise_repetition(count: int) -> float:
    """
    Map repetition count to [0, 1] using log scale.
    1 repetition → ~0.0, 10 repetitions → ~0.5, 100 → ~1.0
    """
    if count <= 1:
        return 0.0
    return min(math.log10(count) / 2.0, 1.0)


def compute_star_gravity(star: Star) -> float:
    """
    Compute gravity for a single star.

    Components:
      - rep_factor:     normalised log repetition count
      - success_factor: success_rate (0..1)

    Because a star is a single event, coherence is not applicable —
    it defaults to 1.0. The constellation's coherence is factored in there.
    """
    rep_factor = _normalise_repetition(star.repetition_count)
    success_factor = star.success_rate
    # Blend: 60% success, 40% repetition
    gravity = 0.6 * success_factor + 0.4 * rep_factor
    return round(float(gravity), 6)


def compute_constellation_gravity(c: Constellation) -> float:
    """
    Compute gravity for a constellation.

    gravity = repetition_factor × coherence_score × success_rate
    """
    rep_factor = _normalise_repetition(c.repetition_count)
    gravity = rep_factor * c.coherence_score * max(c.success_rate, 0.1)
    return round(float(min(gravity, 1.0)), 6)


# ---------------------------------------------------------------------------
# Layer assignment
# ---------------------------------------------------------------------------

def assign_layer(gravity: float) -> str:
    if gravity >= GRAVITY_CORE_THRESHOLD:
        return LAYER_CORE
    if gravity >= GRAVITY_MID_THRESHOLD:
        return LAYER_MID
    return LAYER_OUTER


# ---------------------------------------------------------------------------
# Semantic gravity (gravitational mass computation)
# ---------------------------------------------------------------------------

def _normalise_activation(count: int) -> float:
    """Map activation count to [0, 1] using log scale."""
    if count <= 0:
        return 0.0
    return min(math.log10(count + 1) / 2.0, 1.0)


def _normalise_connections(in_degree: int, max_degree: int = 20) -> float:
    """Map incoming connection count to [0, 1]."""
    if in_degree <= 0:
        return 0.0
    return min(in_degree / max_degree, 1.0)


def compute_semantic_gravity(
    star: Star,
    in_degree: int = 0,
    neighbor_coherence: float = 0.0,
) -> float:
    """
    Compute gravitational mass for a star based on three pillars:

      1. Incoming connections  (weight: 0.40)
      2. Semantic coherence    (weight: 0.35) — avg cosine sim with neighbours
      3. Activation frequency  (weight: 0.25)

    Returns a mass value in [MIN_MASS, MAX_MASS].
    """
    conn_score = _normalise_connections(in_degree)
    coherence_score = float(max(min(neighbor_coherence, 1.0), 0.0))
    activation_score = _normalise_activation(star.activation_count)

    raw_mass = (
        MASS_CONNECTION_WEIGHT * conn_score
        + MASS_COHERENCE_WEIGHT * coherence_score
        + MASS_ACTIVATION_WEIGHT * activation_score
    )
    return round(float(max(MIN_MASS, min(raw_mass, MAX_MASS))), 6)


def compute_collective_gravity(
    star: Star,
    in_degree: int = 0,
    neighbor_coherence: float = 0.0,
    owner_count: int = 1,
) -> float:
    """
    Gravitational mass for collective (cross-user) stars.

    Same three pillars as compute_semantic_gravity, amplified by
    a fourth factor: **owner diversity**.

    The more distinct users independently arrive at the same meaning,
    the stronger the gravitational pull of that knowledge point:

      multiplier = 1.0 + log₂(owner_count) × DIVERSITY_WEIGHT

      2 owners → ×1.30    (30% boost)
      4 owners → ×1.60    (60% boost)
      8 owners → ×1.90    (90% boost)

    Returns mass in [MIN_MASS, MAX_MASS].
    """
    base_mass = compute_semantic_gravity(star, in_degree, neighbor_coherence)

    if owner_count <= 1:
        return base_mass

    diversity_bonus = math.log2(owner_count) * COLLECTIVE_OWNER_DIVERSITY_WEIGHT
    amplified = base_mass * (1.0 + diversity_bonus)
    return round(float(max(MIN_MASS, min(amplified, MAX_MASS))), 6)


def compute_distance_from_mass(mass: float) -> float:
    """
    Distance to the core is the inverse of mass.

    mass → 0.01  ⇒  distance → 0.99  (periphery)
    mass → 1.0   ⇒  distance → 0.0   (nucleus)
    """
    return round(float(max(0.0, min(1.0 - mass, 1.0))), 6)


# ---------------------------------------------------------------------------
# User Star mass (v2 gravitational model)
# ---------------------------------------------------------------------------

def compute_user_star_mass(
    pattern_count: int,
    topic_diversity: int = 1,
    convergence_connections: int = 0,
    is_creator: bool = False,
) -> float:
    """
    Compute gravitational mass for a user star.

    Three pillars:
      1. Activity volume  (40%) — log(pattern_count)
         1 pattern → ~0.0, 10 → ~0.5, 100 → ~1.0
      2. Topic diversity  (35%) — breadth of knowledge
         1 topic → 0.1, 5 topics → ~0.7, 10+ → ~1.0
      3. Convergence connections (25%) — links to other user-stars
         0 → 0.0, 5 → 0.5, 10+ → 1.0

    Creator stars have a floor of CREATOR_INITIAL_MASS.

    Returns mass in [MIN_MASS, MAX_MASS].
    """
    # 1. Activity
    if pattern_count <= 1:
        activity = 0.0
    else:
        activity = min(math.log10(pattern_count) / 2.0, 1.0)

    # 2. Diversity
    diversity = min(math.log2(max(topic_diversity, 1) + 1) / 3.5, 1.0)

    # 3. Convergence
    convergence = min(convergence_connections / 10.0, 1.0)

    raw = (
        0.40 * activity
        + 0.35 * diversity
        + 0.25 * convergence
    )

    # Creator floor
    if is_creator:
        raw = max(raw, CREATOR_INITIAL_MASS)

    return round(float(max(MIN_MASS, min(raw, MAX_MASS))), 6)


# ---------------------------------------------------------------------------
# Batch recompute helpers
# ---------------------------------------------------------------------------

def recompute_stars(stars: List[Star]) -> List[Star]:
    """Recompute gravity and layer for a list of stars. Returns updated list."""
    updated = []
    for star in stars:
        star.gravity_score = compute_star_gravity(star)
        star.layer = assign_layer(star.gravity_score)
        updated.append(star)
    return updated


def recompute_constellations(constellations: List[Constellation]) -> List[Constellation]:
    """Recompute gravity for a list of constellations. Returns updated list."""
    updated = []
    for c in constellations:
        c.gravity_score = compute_constellation_gravity(c)
        updated.append(c)
    return updated


def recompute_all() -> dict:
    """
    Recompute gravity for all stars and constellations in the DB.
    Persists changes and returns a summary dict.
    """
    from vectrax import db

    stars = db.get_all_stars()
    updated_stars = recompute_stars(stars)
    for s in updated_stars:
        db.update_star(s)

    constellations = db.get_all_constellations()
    updated_constellations = recompute_constellations(constellations)
    for c in updated_constellations:
        db.upsert_constellation(c)

    layer_dist = {LAYER_CORE: 0, LAYER_MID: 0, LAYER_OUTER: 0}
    for s in updated_stars:
        layer_dist[s.layer] = layer_dist.get(s.layer, 0) + 1

    return {
        "stars_updated": len(updated_stars),
        "constellations_updated": len(updated_constellations),
        "layer_distribution": layer_dist,
    }
