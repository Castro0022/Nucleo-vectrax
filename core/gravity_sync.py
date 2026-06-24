"""
core/gravity_sync.py — Bidirectional bridge between domain learning and visual universe.

Problem solved
--------------
Two star systems existed in complete isolation:
  1. vectrax.db.stars  — the visual cognitive universe (what the user sees)
  2. gravity_index.json — domain pattern learning (freight, market, etc.)

Mature domain patterns (hits >= MIN_HITS_TO_SYNC) are now promoted as
CONVERGENCE stars in vectrax.db.  This makes the universe grow organically
with every domain learning cycle, reflecting real system knowledge.

Additionally, this module re-runs mass accumulation for all existing stars
whose mass has stagnated at MIN_MASS, using their current graph connections.

Runs periodically from pipeline_worker (every 6h, same cadence as freight).

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List

logger = logging.getLogger("vectrax.gravity_sync")

# Minimum hits in gravity_index before a pattern becomes a universe star
MIN_HITS_TO_SYNC = 5
# Channel for domain-pattern stars (they belong to the global system, not a user)
_SYNC_CHANNEL = "user"
_SYNC_OWNER   = "vectrax_system"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_sync() -> Dict[str, Any]:
    """
    Synchronise mature gravity_index patterns → vectrax.db.stars.
    Also recomputes mass for stale stars (mass == MIN_MASS with links).

    Returns a structured summary.
    """
    t0 = time.time()
    summary: Dict[str, Any] = {
        "patterns_promoted": 0,
        "stars_mass_updated": 0,
        "errors": 0,
        "elapsed_s": 0.0,
    }
    try:
        summary["patterns_promoted"] = _promote_mature_patterns()
    except Exception as exc:
        summary["errors"] += 1
        logger.warning("gravity_sync: promote_patterns failed: %s", exc)

    try:
        summary["stars_mass_updated"] = _recompute_stale_masses()
    except Exception as exc:
        summary["errors"] += 1
        logger.warning("gravity_sync: recompute_masses failed: %s", exc)

    summary["elapsed_s"] = round(time.time() - t0, 2)
    logger.info(
        "gravity_sync | promoted=%d | mass_updated=%d | errors=%d | %.1fs",
        summary["patterns_promoted"],
        summary["stars_mass_updated"],
        summary["errors"],
        summary["elapsed_s"],
    )
    _record_ledger(summary)
    return summary


# ---------------------------------------------------------------------------
# Internal: promote mature patterns as convergence stars
# ---------------------------------------------------------------------------

def _promote_mature_patterns() -> int:
    """
    Read gravity_index.json, find patterns with hits >= MIN_HITS_TO_SYNC,
    and create/update convergence stars in vectrax.db.
    """
    try:
        from core.learn.gravity_engine import GravityIndex
        gi = GravityIndex()
        all_records = gi.all_records()
    except Exception as exc:
        logger.debug("gravity_sync: GravityIndex load failed: %s", exc)
        return 0

    mature = [r for r in all_records if getattr(r, "hits", 0) >= MIN_HITS_TO_SYNC]
    if not mature:
        return 0

    try:
        from vectrax.engine import ingest
        from vectrax.models import STAR_TYPE_CONVERGENCE
    except Exception as exc:
        logger.debug("gravity_sync: engine import failed: %s", exc)
        return 0

    promoted = 0
    for rec in mature:
        try:
            # Build a text representation of the pattern for the star content
            summary_text = getattr(rec, "summary", "") or ""
            intent = getattr(rec, "intent", "") or ""
            domain = getattr(rec, "domain", "unknown")
            fp = getattr(rec, "fingerprint", "")
            hits = getattr(rec, "hits", 0)
            cc = getattr(rec, "cc_score", 0.0)

            if not summary_text and not intent:
                continue

            text = summary_text or f"[{domain}] {intent or fp[:60]}"

            # success=True boosts gravity; hits as repetition proxy
            # We call ingest with success proportional to cc_score
            star = ingest(
                text=text,
                success=(cc >= 0.7),
                channel=_SYNC_CHANNEL,
                owner=_SYNC_OWNER,
            )
            # Upgrade star_type to convergence for domain patterns
            try:
                from vectrax.db import update_star
                from vectrax.models import STAR_TYPE_CONVERGENCE
                star.star_type = STAR_TYPE_CONVERGENCE
                # Amplify mass by hit count (bounded)
                import math
                hit_boost = min(math.log10(hits + 1) / 2.0, 0.5)
                star.mass = min(1.0, star.mass + hit_boost)
                update_star(star)
            except Exception:
                pass

            promoted += 1
        except Exception as exc:
            logger.debug("gravity_sync: promote %r failed: %s", fp[:20], exc)

    return promoted


# ---------------------------------------------------------------------------
# Internal: recompute stale masses
# ---------------------------------------------------------------------------

def _recompute_stale_masses() -> int:
    """
    Find stars with mass == MIN_MASS that have graph connections,
    recompute their mass from actual connections.
    """
    from vectrax.models import MIN_MASS

    try:
        from vectrax.db import get_all_stars, update_star_mass
        from vectrax.gravity import compute_semantic_gravity, compute_distance_from_mass
        from vectrax.graph import get_graph, get_neighbors
    except Exception as exc:
        logger.debug("gravity_sync: stale_mass imports failed: %s", exc)
        return 0

    # Only update stars at exactly MIN_MASS (stale)
    stale = [
        s for s in get_all_stars()
        if abs((s.mass or 0.0) - MIN_MASS) < 0.001
    ]

    updated = 0
    G = get_graph()
    for star in stale:
        try:
            in_deg = len(get_neighbors(star.id)) if G.has_node(star.id) else 0
            if in_deg == 0:
                continue  # no connections yet, leave at MIN_MASS
            new_mass = compute_semantic_gravity(
                star, in_degree=in_deg, neighbor_coherence=0.0
            )
            if new_mass > MIN_MASS:
                dist = compute_distance_from_mass(new_mass)
                update_star_mass(star.id, new_mass, dist)
                updated += 1
        except Exception:
            pass

    return updated


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

def _record_ledger(summary: Dict[str, Any]) -> None:
    try:
        from core.operator import ledger_bridge as ledger
        ledger.record_event(
            action="gravity_sync",
            category=ledger.EventCategory.LEARNING,
            risk_zone=ledger.RiskZone.GREEN,
            reason=(
                f"promoted={summary.get('patterns_promoted')} "
                f"mass_updated={summary.get('stars_mass_updated')}"
            ),
            details=summary,
        )
    except Exception:
        pass
