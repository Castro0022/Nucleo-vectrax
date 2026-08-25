"""
Vectrax Gravity Schemas
========================
Data models for the layered gravity memory system.

Tiers: HOT → WARM → COLD → DEEP.  Nothing is ever deleted (Law 4).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Tier enum
# ---------------------------------------------------------------------------

class Tier(str, Enum):
    HOT = "HOT"
    WARM = "WARM"
    COLD = "COLD"
    DEEP = "DEEP"

    @property
    def weight(self) -> float:
        """Resonance weight for this tier (higher = more relevant)."""
        return {"HOT": 1.0, "WARM": 0.7, "COLD": 0.4, "DEEP": 0.2}[self.value]

    @property
    def order(self) -> int:
        """Numeric order (0=HOT, 3=DEEP)."""
        return {"HOT": 0, "WARM": 1, "COLD": 2, "DEEP": 3}[self.value]


TIER_ORDER = [Tier.HOT, Tier.WARM, Tier.COLD, Tier.DEEP]


# ---------------------------------------------------------------------------
# Gravity Record
# ---------------------------------------------------------------------------

@dataclass
class GravityRecord:
    """Per-pattern gravity metadata."""
    fingerprint: str
    tier: str = Tier.HOT.value
    hits: int = 1
    last_seen: str = ""        # ISO timestamp
    first_seen: str = ""       # ISO timestamp
    cc_score: float = 0.0
    impact: str = "low"
    freq: float = 0.0          # hits per day since first_seen
    domain: str = "unknown"
    intent: str = ""
    outcome_history: List[str] = field(default_factory=list)  # last N outcomes
    activation_history: List[str] = field(default_factory=list)  # ISO timestamps of activation, bounded (see decimate_history)
    decay_factor: float = 1.0  # 1.0 normal, 3.0 for high-impact (anti-amnesia)
    summary: str = ""          # human-readable summary
    constellation_id: Optional[str] = None  # set when compacted

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GravityRecord":
        # Handle extra keys gracefully
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid}
        return cls(**filtered)


# ---------------------------------------------------------------------------
# Bounded, span-preserving history decimation
# ---------------------------------------------------------------------------

def decimate_history(history: List[str], max_size: int) -> List[str]:
    """Bound a chronological history list without losing its temporal span.

    Unlike ``outcome_history`` (which simply drops the oldest entries), a
    temporal activation history must keep observing long-run structure (e.g.
    an annual cycle) even for a HOT star that accumulates hits daily. A pure
    count-based cutoff would eventually contain only the last few days/weeks
    and could never resolve a long period again.

    Strategy: always preserve the first and last entries; when the list
    exceeds ``max_size``, thin the interior by dropping every second element
    (halving its resolution) and repeat until the list fits. This keeps
    O(max_size) storage forever while the represented span keeps growing.
    """
    if max_size <= 0:
        return []
    if len(history) <= max_size:
        return list(history)
    if max_size == 1:
        return [history[-1]]
    if max_size == 2:
        return [history[0], history[-1]]

    result = list(history)
    while len(result) > max_size:
        first, last = result[0], result[-1]
        middle = result[1:-1]
        if not middle:
            break
        middle = middle[::2]
        result = [first, *middle, last]
    return result


# ---------------------------------------------------------------------------
# Constellation Record
# ---------------------------------------------------------------------------

@dataclass
class ConstellationRecord:
    """A compressed group of similar patterns."""
    constellation_id: str
    intent: str
    domain: str
    pattern_fingerprints: List[str] = field(default_factory=list)
    frequency: float = 0.0       # aggregate hits per day
    variance: float = 0.0        # score variance across members
    top_solutions: List[str] = field(default_factory=list)      # best outcomes
    conditions_signature: str = ""  # common conditions hash
    anchor_examples: List[Dict[str, Any]] = field(default_factory=list)  # 3-10 examples
    total_events: int = 0
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ConstellationRecord":
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid}
        return cls(**filtered)
