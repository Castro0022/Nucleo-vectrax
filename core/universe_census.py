"""
Universe Census — Single Source of Truth
==========================================
ONE function that counts everything. Every API, dashboard, self_context,
and visual panel reads from here. No more discrepancies.

API:
  get_census() → UniverseCensus

Every endpoint that needs universe totals calls get_census().
Results are TTL-cached (10s) so rapid multi-endpoint refreshes
share one computation cycle.

Used by:
  - /v1/census (direct)
  - /v1/universe (universe_observer.to_api_dict)
  - /v1/dashboard/observatory
  - /v1/market/patterns
  - vectrax/self_context.py
  - observatory.html / universe.html / app.js (via APIs)

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("vectrax.universe_census")

_CACHE_TTL = 10  # seconds
_cache: Dict[str, Any] = {"census": None, "ts": 0.0}


@dataclass
class UniverseCensus:
    """Complete universe count — single source of truth."""
    timestamp: float = 0.0

    # Three star populations (never double-counted)
    gravitational: int = 0   # gravity engine records
    knowledge: int = 0       # vectrax.db stars table
    users: int = 0           # vectrax.db user_stars count

    # Derived
    total: int = 0           # gravitational + knowledge + users

    # Structural metrics
    convergences: int = 0
    patterns: int = 0
    constellations: int = 0
    mass_total: float = 0.0
    word_gravity_count: int = 0

    # Domain breakdown (gravitational only)
    domains: Dict[str, int] = field(default_factory=dict)

    # Layers (knowledge stars)
    layers: Dict[str, int] = field(default_factory=dict)

    # Market
    market_signals: int = 0
    market_patterns: int = 0
    market_win_rate: float = 0.0
    market_usable_patterns: int = 0
    executor_mode: str = "off"

    # Users (Telegram)
    users_total: int = 0         # distinct Telegram users
    interactions: int = 0        # total interactions
    user_facts: int = 0
    core_memory: int = 0
    teams: int = 0

    # Quality / test / code / runtime phenomena (from the observation ledger).
    # Counts only — detailed entities live in /v1/universe (universe_observer).
    quality_failures: int = 0
    quality_active_failures: int = 0
    quality_recoveries: int = 0
    quality_code_changes: int = 0
    quality_convergences: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "total": self.total,
            "star_breakdown": {
                "gravitational": self.gravitational,
                "knowledge": self.knowledge,
                "users": self.users,
            },
            "convergences": self.convergences,
            "patterns": self.patterns,
            "constellations": self.constellations,
            "mass_total": round(self.mass_total, 4),
            "word_gravity_count": self.word_gravity_count,
            "domains": self.domains,
            "layers": self.layers,
            "market": {
                "signals": self.market_signals,
                "patterns": self.market_patterns,
                "win_rate": self.market_win_rate,
                "usable_patterns": self.market_usable_patterns,
                "executor_mode": self.executor_mode,
            },
            "users_telegram": {
                "total": self.users_total,
                "interactions": self.interactions,
                "facts": self.user_facts,
                "core_memory": self.core_memory,
                "teams": self.teams,
            },
            "quality": {
                "failures": self.quality_failures,
                "active_failures": self.quality_active_failures,
                "recoveries": self.quality_recoveries,
                "code_changes": self.quality_code_changes,
                "convergences": self.quality_convergences,
            },
        }


# ---------------------------------------------------------------------------
# User memory DB path (same as dashboard.py uses)
# ---------------------------------------------------------------------------
_USER_DB = Path(__file__).resolve().parents[1] / "vault" / "user_memory.db"


def _collect_users(c: UniverseCensus) -> None:
    """Telegram user counts from user_memory.db."""
    try:
        conn = sqlite3.connect(str(_USER_DB), timeout=2)
        conn.row_factory = sqlite3.Row
        c.users_total = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM profiles WHERE user_id NOT LIKE 'test:%'"
        ).fetchone()[0]
        c.interactions = conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
        try:
            c.user_facts = conn.execute("SELECT COUNT(*) FROM user_facts").fetchone()[0]
        except Exception:
            pass
        try:
            c.core_memory = conn.execute("SELECT COUNT(*) FROM core_memory").fetchone()[0]
        except Exception:
            pass
        try:
            c.teams = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
        except Exception:
            pass
        conn.close()
    except Exception as exc:
        logger.debug("census users failed: %s", exc)


def _collect_market(c: UniverseCensus) -> None:
    """Market signal/pattern counts from eToro modules."""
    try:
        from connectors.etoro.signal_recorder import get_signal_stats
        stats = get_signal_stats()
        c.market_signals = stats.get("total", 0)
        c.market_win_rate = stats.get("win_rate", 0.0)
    except Exception:
        pass
    try:
        from connectors.etoro.pattern_memory import get_patterns
        pats = get_patterns()
        c.market_patterns = len(pats)
        c.market_usable_patterns = sum(1 for p in pats if p.is_usable)
    except Exception:
        pass
    try:
        from connectors.etoro.auto_executor import get_config
        c.executor_mode = get_config().get("mode", "off")
    except Exception:
        pass


def _collect_quality(c: UniverseCensus) -> None:
    """Quality/test/code/runtime phenomena counts from the observation ledger.

    Read-only and fully defensive: a missing ledger or any error leaves the
    counts at zero and never blocks the census computation.
    """
    try:
        from core.self_observation.quality_entities import get_quality_summary
        s = get_quality_summary()
        c.quality_failures = int(s.get("failures", 0))
        c.quality_active_failures = int(s.get("active_failures", 0))
        c.quality_recoveries = int(s.get("recoveries", 0))
        c.quality_code_changes = int(s.get("code_changes", 0))
        c.quality_convergences = int(s.get("convergences", 0))
    except Exception as exc:
        logger.debug("census quality failed: %s", exc)


def _build_census() -> UniverseCensus:
    """Internal: compute the full census from all sources."""
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
        c.users = universe.get("stars", 0)
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

    # === SOURCE 4: Market ===
    _collect_market(c)

    # === SOURCE 5: Users ===
    _collect_users(c)

    # === SOURCE 6: Quality phenomena (read-only ledger summary) ===
    _collect_quality(c)

    # Total — always consistent
    c.total = c.gravitational + c.knowledge + c.users

    return c


def get_census() -> UniverseCensus:
    """
    Count everything in the universe. ONE call, ONE truth.
    TTL-cached for 10s so rapid multi-endpoint refreshes share one computation.
    """
    now = time.time()
    if _cache["census"] and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["census"]
    c = _build_census()
    _cache["census"] = c
    _cache["ts"] = now
    return c
