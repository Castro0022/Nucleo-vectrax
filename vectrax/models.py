"""
Core data models for the Vectrax memory graph.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

from vectrax.identity import CHANNEL_CREATOR, CHANNEL_USER


# ---------------------------------------------------------------------------
# Layer constants
# ---------------------------------------------------------------------------
LAYER_CORE = "core"
LAYER_MID = "mid"
LAYER_OUTER = "outer"

# Gravity thresholds for layer assignment
GRAVITY_CORE_THRESHOLD = 0.6
GRAVITY_MID_THRESHOLD = 0.3

# Constellation thresholds
CONSTELLATION_MIN_STARS = 3
PROPOSAL_GRAVITY_THRESHOLD = 0.8
PROPOSAL_MIN_MEMBERS = 5

# Similarity threshold for linking stars
SIMILARITY_THRESHOLD = 0.75

# ---------------------------------------------------------------------------
# Gravitational mass constants
# ---------------------------------------------------------------------------
MIN_MASS = 0.01
MAX_MASS = 1.0
MASS_CONNECTION_WEIGHT = 0.40   # incoming connections contribution
MASS_COHERENCE_WEIGHT = 0.35    # semantic coherence contribution
MASS_ACTIVATION_WEIGHT = 0.25   # activation frequency contribution
CONVERGENCE_THRESHOLD = 0.80    # min similarity for convergence detection
CONVERGENCE_MIN_PATHS = 3       # min distinct paths to trigger convergence

# Star types
STAR_TYPE_PRIMARY = "primary"           # user-generated primary star
STAR_TYPE_CONVERGENCE = "convergence"   # auto-generated convergence coordinate
CONVERGENCE_PREFIX = "[CONVERGENCE]"    # content prefix for convergence stars

# Coordinate navigation
COORDINATE_SEARCH_RADIUS = 0.70  # min similarity to consider within a coordinate's radius
TRAJECTORY_WINDOW = 20           # recent trajectory points for direction computation

# Cross-user (collective) convergence
COLLECTIVE_OWNER = "__collective__"      # owner for shared convergence stars
COLLECTIVE_PREFIX = "[COLLECTIVE]"       # content prefix for collective convergence
CROSS_USER_MIN_OWNERS = 2               # min distinct users to trigger collective convergence
CROSS_USER_CONVERGENCE_THRESHOLD = 0.80  # similarity threshold across users

# Collective gravity — owner diversity amplifies mass
COLLECTIVE_OWNER_DIVERSITY_WEIGHT = 0.30  # log2(owner_count) × this weight → mass multiplier
COLLECTIVE_CONSTELLATION_MIN_MASS = 0.25  # min avg mass for collective constellation emergence
COLLECTIVE_CONSTELLATION_MIN_STARS = 3    # min stars for collective constellation


@dataclass
class Star:
    """A single event/experience node in the memory graph."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    content: str = ""
    timestamp: float = field(default_factory=time.time)
    embedding: Optional[bytes] = None        # serialised numpy float32 array
    layer: str = LAYER_OUTER
    gravity_score: float = 0.0
    repetition_count: int = 1
    success_count: int = 0
    total_count: int = 1
    # Gravitational mass fields
    mass: float = MIN_MASS                   # dynamic mass (0.01 → 1.0)
    activation_count: int = 0                # how many times this node was activated
    last_activated: float = field(default_factory=time.time)
    distance_to_core: float = 1.0            # 1.0 = max periphery, 0.0 = nucleus
    star_type: str = STAR_TYPE_PRIMARY        # 'primary' | 'convergence'
    # Channel identity — set at creation, never changed
    channel: str = CHANNEL_USER             # 'creator' | 'user'
    owner: str = ""                         # 'mario' for creator, username for users

    @property
    def success_rate(self) -> float:
        if self.total_count == 0:
            return 0.0
        return self.success_count / self.total_count

    def __post_init__(self) -> None:
        # Creator stars always start in the nucleus (core)
        if self.channel == CHANNEL_CREATOR:
            if self.layer == LAYER_OUTER:
                self.layer = LAYER_CORE

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "timestamp": self.timestamp,
            "layer": self.layer,
            "gravity_score": round(self.gravity_score, 4),
            "repetition_count": self.repetition_count,
            "success_count": self.success_count,
            "total_count": self.total_count,
            "mass": round(self.mass, 6),
            "activation_count": self.activation_count,
            "last_activated": self.last_activated,
            "distance_to_core": round(self.distance_to_core, 6),
            "star_type": self.star_type,
            "channel": self.channel,
            "owner": self.owner,
        }


@dataclass
class Constellation:
    """A cluster of related stars detected through embedding similarity."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    star_ids: List[str] = field(default_factory=list)
    coherence_score: float = 0.0
    repetition_count: int = 1
    success_rate: float = 0.0
    gravity_score: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # Channel identity — all member stars share the same channel
    channel: str = CHANNEL_USER
    owner: str = ""

    @property
    def member_count(self) -> int:
        return len(self.star_ids)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "member_count": self.member_count,
            "coherence_score": round(self.coherence_score, 4),
            "repetition_count": self.repetition_count,
            "success_rate": round(self.success_rate, 4),
            "gravity_score": round(self.gravity_score, 4),
        }


@dataclass
class Proposal:
    """A structural improvement proposal generated by the engine."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    constellation_id: str = ""
    description: str = ""
    evidence: dict = field(default_factory=dict)   # stored as JSON
    status: str = "pending"                         # pending | approved | rejected
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "constellation_id": self.constellation_id,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at,
        }
