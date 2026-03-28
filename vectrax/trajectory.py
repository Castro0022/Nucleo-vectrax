"""
Vectrax Trajectory Tracking
==============================

Each user's interactions trace a path (trajectory) through the knowledge
graph.  Trajectories are sequences of stars visited/created over time.

A trajectory has a **direction** — the embedding centroid of the user's
most recent stars — which indicates where the user's attention is heading
in semantic space.

Trajectory intersections between different users (or the same user's
past paths) are candidate convergence events.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from vectrax import db
from vectrax.embeddings import decode_embedding, mean_embedding
from vectrax.models import TRAJECTORY_WINDOW


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------

def record_point(
    star_id: str,
    channel: str,
    owner: str,
) -> str:
    """
    Append a star to the user's trajectory.
    Returns the trajectory point ID.
    """
    db.init_db()
    return db.insert_trajectory_point(star_id, channel, owner)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def get_user_trajectory(
    channel: str,
    owner: str,
    limit: int = 50,
) -> List[dict]:
    """Return the user's recent trajectory with star metadata."""
    db.init_db()
    return db.get_trajectory(channel, owner, limit)


def get_trajectory_star_ids(
    channel: str,
    owner: str,
    limit: int = TRAJECTORY_WINDOW,
) -> List[str]:
    """Return just the star IDs of the user's recent trajectory."""
    db.init_db()
    return db.get_trajectory_star_ids(channel, owner, limit)


# ---------------------------------------------------------------------------
# Direction (semantic centroid of recent trajectory)
# ---------------------------------------------------------------------------

def get_trajectory_direction(
    channel: str,
    owner: str,
    window: int = TRAJECTORY_WINDOW,
) -> Optional[np.ndarray]:
    """
    Compute the semantic direction of a user's trajectory.

    Returns the normalised centroid embedding of the most recent `window`
    trajectory stars.  This vector represents "where the user's attention
    is heading" in semantic space.

    Returns None if no trajectory exists yet.
    """
    db.init_db()
    star_ids = db.get_trajectory_star_ids(channel, owner, limit=window)
    if not star_ids:
        return None

    vecs: List[np.ndarray] = []
    for sid in star_ids:
        star = db.get_star(sid)
        if star and star.embedding:
            vecs.append(decode_embedding(star.embedding))

    if not vecs:
        return None

    return mean_embedding(vecs)


# ---------------------------------------------------------------------------
# Trajectory intersection detection
# ---------------------------------------------------------------------------

def find_trajectory_intersections(
    channel: str,
    owner: str,
    threshold: float = 0.80,
    window: int = TRAJECTORY_WINDOW,
) -> List[Dict]:
    """
    Find points where this user's recent trajectory intersects with
    other users' trajectories (or their own past paths).

    An intersection occurs when a recent star from one trajectory is
    semantically similar to a recent star from another trajectory.

    Returns a list of intersection descriptors:
      {star_a, star_b, owner_a, owner_b, similarity}
    """
    from vectrax.embeddings import cosine_similarity

    db.init_db()
    my_ids = db.get_trajectory_star_ids(channel, owner, limit=window)
    if not my_ids:
        return []

    # Build my trajectory embeddings
    my_stars: List[Tuple[str, np.ndarray]] = []
    for sid in my_ids:
        star = db.get_star(sid)
        if star and star.embedding:
            my_stars.append((sid, decode_embedding(star.embedding)))

    if not my_stars:
        return []

    # Get all other users' trajectory star IDs
    # We query all stars not belonging to this owner in the same channel
    all_stars = db.get_all_stars(channel=channel)
    other_stars: List[Tuple[str, np.ndarray, str]] = []
    my_id_set = set(my_ids)
    for s in all_stars:
        if s.id not in my_id_set and s.embedding and s.owner != owner:
            other_stars.append((s.id, decode_embedding(s.embedding), s.owner))

    intersections: List[Dict] = []
    for my_sid, my_vec in my_stars:
        for other_sid, other_vec, other_owner in other_stars:
            sim = cosine_similarity(my_vec, other_vec)
            if sim >= threshold:
                intersections.append({
                    "star_a": my_sid,
                    "star_b": other_sid,
                    "owner_a": owner,
                    "owner_b": other_owner,
                    "similarity": round(float(sim), 6),
                })

    # Sort by similarity descending
    intersections.sort(key=lambda x: x["similarity"], reverse=True)
    return intersections
