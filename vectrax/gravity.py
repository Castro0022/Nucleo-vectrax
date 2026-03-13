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
    GRAVITY_CORE_THRESHOLD,
    GRAVITY_MID_THRESHOLD,
    LAYER_CORE,
    LAYER_MID,
    LAYER_OUTER,
    Constellation,
    Star,
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
