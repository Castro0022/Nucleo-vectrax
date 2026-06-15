"""
Universe Census — Single Source of Truth
==========================================
ONE function that counts all stars. Every API, dashboard, self_context,
and visual panel reads from here. No more discrepancies.

API:
  get_census() → UniverseCensus
  {total, gravitational, knowledge, users, convergences, patterns,
   domains, word_gravity_count}

Used by:
  - /v1/universe (universe_observer.to_api_dict)
  - /v1/dashboard/observatory
  - vectrax/self_context.py (_read_universe_state)
  - observatory.html (via API)
  - universe.html (via API)

Creador: Mario Bravo Castro
Fecha: 2026-06-15
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Any

logger = logging.getLogger("vectrax.universe_census")


@dataclass
class UniverseCensus:
    """Complete universe count — single source of truth."""
    timestamp: float = 0.0

    # Three populations (never double-counted)
    gravitational: int = 0   # gravity engine records
    knowledge: int = 0       # vectrax.db stars table
    users: int = 0           # vectrax.db user_stars count (from get_universe_status)

    # Derived
    total: int = 0           # gravitational + knowledge + users

    # Additional metrics
    convergences: int = 0
    patterns: int = 0
    constellations: int = 0
    mass_total: float = 0.0
    word_gravity_count: int = 0

    # Domain breakdown (gravitational only)
    domains: Dict[str, int] = field(default_factory=dict)

    # Layers (knowledge stars)
    layers: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "gravitational": self.gravitational,
            "knowledge": self.knowledge,
            "users": self.users,
            "convergences": self.convergences,
            "patterns": self.patterns,
            "constellations": self.constellations,
            "mass_total": round(self.mass_total, 4),
            "word_gravity_count": self.word_gravity_count,
            "domains": self.domains,
            "layers": self.layers,
        }


def get_census() -> UniverseCensus:
    """
    Count everything in the universe. ONE call, ONE truth.

    Sources:
      1. gravity_index.json → gravitational stars + domains
      2. vectrax.db → knowledge stars + patterns + constellations + user count
      3. word_gravity table → word count
    """
    c = UniverseCensus(timestamp=time.time())

    # === SOURCE 1: Gravity Engine ===
    try:
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
        records = gi.all_records()
        c.gravitational = len(records)
        domain_stats = gi.domain_stats()
        c.domains = {d: s.get("count", 0) for d, s in domain_stats.items()}
        c.convergences = len(gi.cross_domain_convergences())
    except Exception as exc:
        logger.debug("census gravity failed: %s", exc)

    # === SOURCE 2: vectrax.db ===
    try:
        from vectrax.db import get_counts, get_universe_status
        counts = get_counts()
        c.knowledge = counts.get("stars", 0)
        c.constellations = counts.get("constellations", 0)
        c.layers = counts.get("layers", {})

        universe = get_universe_status()
        c.users = universe.get("stars", 0)  # user_stars count
        c.patterns = universe.get("patterns", 0)
        c.mass_total = universe.get("total_mass", 0.0)
    except Exception as exc:
        logger.debug("census db failed: %s", exc)

    # === SOURCE 3: Word Gravity ===
    try:
        from core.word_gravity import get_all_words
        c.word_gravity_count = len(get_all_words(scope="global"))
    except Exception:
        pass

    # Total — always consistent
    c.total = c.gravitational + c.knowledge + c.users

    return c
