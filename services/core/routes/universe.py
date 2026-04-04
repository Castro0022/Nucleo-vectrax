"""
/v1/universe — Live heartbeat of the Vectrax cognitive universe.

Returns the nucleus state, all active stars, convergences, and
constellations. This is the "pulse" of the system.

No auth required for basic status (read-only, no PII).
"""

from __future__ import annotations

import time
from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter(tags=["universe"])


@router.get("/universe")
async def get_universe() -> Dict[str, Any]:
    """
    Live state of the Vectrax universe.

    Returns:
      - nucleus: centroid info, total mass, star count
      - stars: all user-stars with mass, distance, layer, patterns
      - convergences: active links between stars
      - heartbeat: current timestamp (the nucleus is alive)
    """
    from vectrax.db import get_universe_status, get_all_user_stars
    from vectrax.core_nucleus import get_core_info

    universe = get_universe_status()
    nucleus = get_core_info()

    # Build star list with details
    stars = []
    for s in get_all_user_stars():
        stars.append({
            "user_id": s.user_id,
            "role": s.role,
            "mass": round(s.mass, 4),
            "distance_to_core": round(s.distance_to_core, 4),
            "layer": s.layer,
            "pattern_count": s.pattern_count,
            "activation_count": s.activation_count,
            "last_active": s.last_active,
        })

    # Convergences from graph
    convergences = []
    try:
        from vectrax.graph import get_graph
        g = get_graph()
        for u, v, data in g.edges(data=True):
            convergences.append({
                "star_a": u,
                "star_b": v,
                "similarity": round(data.get("weight", 0), 4),
            })
    except Exception:
        pass

    return {
        "heartbeat": time.time(),
        "nucleus": {
            "alive": True,
            "star_count": nucleus.get("core_star_count", 0),
            "has_centroid": nucleus.get("has_centroid", False),
            "total_mass": universe.get("total_mass", 0),
        },
        "stars": stars,
        "star_count": universe.get("stars", 0),
        "pattern_count": universe.get("patterns", 0),
        "layers": universe.get("layers", {}),
        "convergences": convergences,
    }


@router.get("/universe/star/{user_id}")
async def get_star_detail(user_id: str) -> Dict[str, Any]:
    """Detail of a single user-star including topic fingerprint."""
    from vectrax.db import get_user_star, get_pattern_count, get_topic_distribution

    star = get_user_star(user_id)
    if star is None:
        return {"error": "Star not found", "user_id": user_id}

    return {
        "star": star.to_dict(),
        "patterns": get_pattern_count(user_id),
        "topics": get_topic_distribution(user_id),
    }
