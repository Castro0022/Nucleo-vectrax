"""
Vectrax Core Nucleus — Immutable gravity center of the cognitive universe.

The nucleus is the centroid of all core-layer stars. It cannot be deleted
or modified directly; it recalculates itself when core stars change.

Design:
  - Singleton pattern: one nucleus per runtime
  - Centroid = normalised mean of all core-star embeddings
  - If no core stars exist, centroid is None (cold start)
  - refresh_centroid() must be called after core-star mutations
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np


# ---------------------------------------------------------------------------
# Singleton state
# ---------------------------------------------------------------------------
_centroid: Optional[np.ndarray] = None
_core_star_count: int = 0
_initialised: bool = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_core_centroid() -> Optional[np.ndarray]:
    """Return the current nucleus centroid vector, or None if cold start."""
    global _initialised
    if not _initialised:
        refresh_centroid()
    return _centroid


def get_core_info() -> Dict:
    """Return a summary dict of the nucleus state (safe for JSON)."""
    if not _initialised:
        refresh_centroid()
    return {
        "initialised": _initialised,
        "core_star_count": _core_star_count,
        "has_centroid": _centroid is not None,
    }


def refresh_centroid() -> Optional[np.ndarray]:
    """
    Recalculate the nucleus centroid from all core-layer stars in the DB.

    Call this after any mutation that adds/promotes stars to the core layer.
    """
    global _centroid, _core_star_count, _initialised
    _initialised = True

    from vectrax import db
    from vectrax.embeddings import decode_embedding, mean_embedding
    from vectrax.models import LAYER_CORE

    core_stars = db.get_all_stars(layer=LAYER_CORE)
    _core_star_count = len(core_stars)

    vecs: List[np.ndarray] = []
    for s in core_stars:
        if s.embedding:
            vecs.append(decode_embedding(s.embedding))

    if not vecs:
        _centroid = None
        return None

    _centroid = mean_embedding(vecs)
    return _centroid


def distance_to_centroid(vec: np.ndarray) -> float:
    """
    Compute semantic distance from a vector to the nucleus centroid.

    Returns a value in [0, 1]:
      0.0 = identical to nucleus
      1.0 = maximally distant / no centroid available
    """
    centroid = get_core_centroid()
    if centroid is None:
        return 1.0
    # cosine similarity of unit-normalised vectors = dot product
    similarity = float(np.dot(vec, centroid))
    # Convert similarity [−1, 1] to distance [0, 1]
    return float(np.clip(1.0 - similarity, 0.0, 1.0))


# ---------------------------------------------------------------------------
# Reset (for tests)
# ---------------------------------------------------------------------------

def _reset() -> None:
    """Reset singleton state. Only use in tests."""
    global _centroid, _core_star_count, _initialised
    _centroid = None
    _core_star_count = 0
    _initialised = False
