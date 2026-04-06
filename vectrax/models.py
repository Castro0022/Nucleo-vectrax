"""
Core data models for the Vectrax cognitive universe.

Architecture:
  - Each user IS a star (UserStar). One star per user.
  - Each interaction feeds the star as a Pattern.
  - The star's embedding is the centroid of its patterns.
  - Gravity pulls the star toward the nucleus with activity.
  - Stars converge with other stars by semantic proximity.
  - Constellations are clusters of users with convergent knowledge.
  - The nucleus (Vectrax) is the gravity center — enriched by every star.
  - The creator (Mario) is a privileged root star, not the nucleus.

Legacy:
  - The Star class is preserved for backward compatibility with legacy_stars.
  - New code should use UserStar + Pattern exclusively.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

from vectrax.identity import CHANNEL_CREATOR, CHANNEL_USER


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
ROLE_CREATOR = "creator"     # Mario — privileged root star
ROLE_USER = "user"           # regular user star

# Creator privileges
CREATOR_INITIAL_MASS = 0.50  # creator starts with high mass
CREATOR_CENTROID_WEIGHT = 3  # creator patterns weigh 3x in nucleus centroid
USER_INITIAL_MASS = 0.01     # new users start at periphery

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


# ---------------------------------------------------------------------------
# UserStar — one star per user (the new model)
# ---------------------------------------------------------------------------

@dataclass
class UserStar:
    """A user IS a star — a living source of knowledge for Vectrax.

    Each interaction enriches the star (patterns, mass, embedding).
    The star gains gravity with use and moves toward the nucleus.
    Even if the user disappears, their knowledge persists in the system.
    """

    user_id: str = ""                        # PK — unique identity (e.g. "tg:12345")
    role: str = ROLE_USER                    # 'creator' | 'user'
    embedding: Optional[bytes] = None        # centroid of all patterns (float32 BLOB)
    mass: float = USER_INITIAL_MASS          # gravitational mass (0.01 → 1.0)
    distance_to_core: float = 1.0            # 1.0 = periphery, 0.0 = nucleus
    layer: str = LAYER_OUTER                 # outer / mid / core
    pattern_count: int = 0                   # total interactions absorbed
    topic_fingerprint: str = "{}"             # JSON: {"trading": 12, "code": 5, ...}
    activation_count: int = 0                # total activations (queries + ingests)
    last_active: float = field(default_factory=time.time)
    knowledge_contributed: bool = False      # nucleus has absorbed this star's knowledge
    created_at: float = field(default_factory=time.time)

    @property
    def is_creator(self) -> bool:
        return self.role == ROLE_CREATOR

    def to_dict(self) -> dict:
        import json as _json
        return {
            "user_id": self.user_id,
            "role": self.role,
            "mass": round(self.mass, 6),
            "distance_to_core": round(self.distance_to_core, 6),
            "layer": self.layer,
            "pattern_count": self.pattern_count,
            "topic_fingerprint": _json.loads(self.topic_fingerprint)
                                 if isinstance(self.topic_fingerprint, str)
                                 else self.topic_fingerprint,
            "activation_count": self.activation_count,
            "last_active": self.last_active,
            "knowledge_contributed": self.knowledge_contributed,
        }


# ---------------------------------------------------------------------------
# Pattern — individual interaction that feeds a star
# ---------------------------------------------------------------------------

# Pattern status
PATTERN_STORED = "stored"         # confirmed valuable — affects centroid
PATTERN_CANDIDATE = "candidate"   # potentially valuable — pending resolution
PATTERN_DISCARDED = "discarded"   # trivial — kept for audit, ignored by centroid


@dataclass
class Pattern:
    """A single interaction that enriches a UserStar.

    Patterns are the raw material. They have no gravity of their own —
    the gravity belongs to the star they feed.

    Status:
      stored    — confirmed valuable, affects the star's centroid
      candidate — potentially valuable, pending resolution
      discarded — trivial, kept for audit but ignored by centroid
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = ""                        # FK → UserStar.user_id
    content: str = ""                        # the interaction text
    embedding: Optional[bytes] = None        # float32 BLOB
    topic: str = "general"                   # detected topic
    status: str = PATTERN_STORED             # stored / candidate / discarded
    value_score: float = 0.0                 # 0.0–1.0 memory value
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "content": self.content[:200],
            "topic": self.topic,
            "status": self.status,
            "value_score": round(self.value_score, 4),
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Constellation — cluster of user-stars with convergent knowledge
# ---------------------------------------------------------------------------

@dataclass
class Constellation:
    """A cluster of user-stars with convergent knowledge."""

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
