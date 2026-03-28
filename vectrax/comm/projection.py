"""
Vectrax Core Projection
===========================
Structural projection of the nucleus for user-channel consumers.

CRITICAL RULE: this module NEVER exposes content, embeddings, or any
identifying text from creator-channel stars.  Only aggregated metrics,
layer topology, and gravitational coordinates are returned.

Each user receives their own projection instance — stateless, rebuilt
on demand, no sensitive data cached.
"""
from __future__ import annotations

import logging
from typing import Dict, List

from vectrax.identity import CHANNEL_CREATOR, CREATOR_OWNER

logger = logging.getLogger("vectrax.comm.projection")


class CoreProjection:
    """
    Read-only structural view of the Vectrax nucleus.

    Provides users with orientation context (how the nucleus is shaped)
    without revealing any creator content.  All methods return aggregate
    numbers and topology — never text, embeddings, or identities.
    """

    def __init__(self, owner: str) -> None:
        """
        owner: the user requesting the projection (for audit logging only).
        """
        self._requester = owner

    # ------------------------------------------------------------------
    # Structure — aggregate metrics
    # ------------------------------------------------------------------

    def get_structure(self) -> dict:
        """
        Return a structural summary of the nucleus.

        Includes:
          - Total star count per layer (no content)
          - Total constellation count
          - Average gravity score
          - Mass distribution
          - Pending proposal count (count only, no descriptions)

        Does NOT include: star content, embeddings, owner info, or
        any data that could identify specific creator entries.
        """
        from vectrax import db
        db.init_db()

        creator_stars = db.get_all_stars(
            channel=CHANNEL_CREATOR, owner=CREATOR_OWNER
        )
        creator_constellations = db.get_all_constellations(
            channel=CHANNEL_CREATOR, owner=CREATOR_OWNER
        )
        pending = db.get_proposals(status="pending")

        # Layer distribution
        layers: Dict[str, int] = {}
        for s in creator_stars:
            layers[s.layer] = layers.get(s.layer, 0) + 1

        # Gravity statistics
        gravities = [s.gravity_score for s in creator_stars]
        avg_gravity = sum(gravities) / len(gravities) if gravities else 0.0
        max_gravity = max(gravities) if gravities else 0.0
        min_gravity = min(gravities) if gravities else 0.0

        # Mass statistics
        masses = [s.mass for s in creator_stars]
        avg_mass = sum(masses) / len(masses) if masses else 0.0

        # Constellation density
        const_sizes = [c.member_count for c in creator_constellations]
        avg_const_size = (
            sum(const_sizes) / len(const_sizes) if const_sizes else 0.0
        )

        return {
            "nucleus_stars": len(creator_stars),
            "nucleus_constellations": len(creator_constellations),
            "pending_proposals": len(pending),
            "layers": layers,
            "gravity": {
                "avg": round(avg_gravity, 4),
                "max": round(max_gravity, 4),
                "min": round(min_gravity, 4),
            },
            "mass": {
                "avg": round(avg_mass, 6),
            },
            "constellation_avg_size": round(avg_const_size, 2),
            # Explicit declaration that content is not included
            "_projection_note": "structural_only:no_content_exposed",
        }

    # ------------------------------------------------------------------
    # Navigation — gravitational coordinates
    # ------------------------------------------------------------------

    def get_navigation_context(self) -> dict:
        """
        Return gravitational navigation context so a user can understand
        their relative position in the knowledge cosmos.

        Provides:
          - Layer boundaries (core/mid/outer thresholds)
          - Average distances per layer (how concentrated the nucleus is)
          - Total system density (stars / layers)

        Does NOT reveal which stars are where or what they contain.
        """
        from vectrax import db
        from vectrax.models import (
            GRAVITY_CORE_THRESHOLD,
            GRAVITY_MID_THRESHOLD,
            LAYER_CORE,
            LAYER_MID,
            LAYER_OUTER,
        )
        db.init_db()

        creator_stars = db.get_all_stars(
            channel=CHANNEL_CREATOR, owner=CREATOR_OWNER
        )

        # Distance statistics per layer
        layer_distances: Dict[str, List[float]] = {
            LAYER_CORE: [],
            LAYER_MID: [],
            LAYER_OUTER: [],
        }
        for s in creator_stars:
            if s.layer in layer_distances:
                layer_distances[s.layer].append(s.distance_to_core)

        avg_distances = {}
        for layer, dists in layer_distances.items():
            avg_distances[layer] = (
                round(sum(dists) / len(dists), 4) if dists else 1.0
            )

        # System density
        total_stars = len(creator_stars)
        active_layers = sum(1 for d in layer_distances.values() if d)

        return {
            "thresholds": {
                "core": GRAVITY_CORE_THRESHOLD,
                "mid": GRAVITY_MID_THRESHOLD,
            },
            "avg_distance_per_layer": avg_distances,
            "system_density": {
                "total_stars": total_stars,
                "active_layers": active_layers,
                "stars_per_layer": (
                    round(total_stars / active_layers, 1)
                    if active_layers > 0 else 0
                ),
            },
            "_projection_note": "navigation_only:no_content_exposed",
        }
