"""
Pattern Refinement — El universo se auto-organiza
====================================================
Cada N interacciones, el universo revisa sus estrellas:
  - Lo útil gana masa y se acerca al centro
  - Lo inútil pierde masa y se aleja
  - Convergencias débiles se disuelven

No es limpieza. Es evolución.

Ciclo (run_refinement_cycle):
  1. REINFORCE — estrellas activas con masa alta → +10% masa
  2. DEGRADE   — estrellas inactivas (7d sin activación) → -15% masa
  3. DISSOLVE  — convergencias con cc <0.3 → eliminar enlace
  4. RELAYER   — recalcular capa según nueva masa

Se integra en meta_loop cada 50 interacciones.

Creador: Mario Bravo Castro
Fecha: 2026-06-14
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List

logger = logging.getLogger("vectrax.learn.pattern_refinement")

# Configuration
INACTIVE_DAYS = 7           # days without activation = inactive
REINFORCE_MASS_BOOST = 0.10 # +10% mass for active high-mass stars
DEGRADE_MASS_PENALTY = 0.15 # -15% mass for inactive stars
MIN_MASS_FLOOR = 0.01       # never degrade below this
DISSOLVE_CC_THRESHOLD = 0.3 # convergences below this cc are dissolved
CYCLE_INTERVAL = 50         # run every N interactions


@dataclass
class RefinementResult:
    """Summary of one refinement cycle."""
    reinforced: int = 0
    degraded: int = 0
    dissolved: int = 0
    relayered: int = 0
    elapsed_ms: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "reinforced": self.reinforced,
            "degraded": self.degraded,
            "dissolved": self.dissolved,
            "relayered": self.relayered,
            "elapsed_ms": self.elapsed_ms,
        }


def run_refinement_cycle() -> RefinementResult:
    """
    Execute one refinement cycle. The universe evolves.

    Returns RefinementResult with counts of changes made.
    """
    t0 = time.perf_counter()
    result = RefinementResult()

    try:
        from vectrax import db
        from vectrax.gravity import assign_layer, compute_distance_from_mass
        from vectrax.embeddings import decode_embedding

        db.init_db()
        now = time.time()
        inactive_cutoff = now - (INACTIVE_DAYS * 86400)

        all_stars = db.get_all_user_stars()
        if not all_stars:
            return result

        for star in all_stars:
            original_mass = star.mass
            original_layer = star.layer
            changed = False

            # === REINFORCE: active + high mass → grow ===
            if (star.mass >= 0.3
                    and star.last_active
                    and star.last_active > inactive_cutoff
                    and star.activation_count >= 5):
                star.mass = min(star.mass * (1 + REINFORCE_MASS_BOOST), 1.0)
                if star.mass != original_mass:
                    result.reinforced += 1
                    changed = True

            # === DEGRADE: inactive → shrink ===
            elif (star.last_active
                  and star.last_active < inactive_cutoff
                  and star.mass > MIN_MASS_FLOOR
                  and not star.is_creator):
                star.mass = max(star.mass * (1 - DEGRADE_MASS_PENALTY), MIN_MASS_FLOOR)
                if star.mass != original_mass:
                    result.degraded += 1
                    changed = True

            # === RELAYER: recalculate layer from new mass ===
            if changed:
                star.distance_to_core = compute_distance_from_mass(star.mass)
                star.layer = assign_layer(star.mass)
                if star.layer != original_layer:
                    result.relayered += 1
                db.upsert_user_star(star)

        # === DISSOLVE: weak convergences ===
        try:
            from vectrax import graph as g
            graph = g.get_graph()
            edges_to_remove = []
            for u, v, data in graph.edges(data=True):
                weight = data.get("weight", 0)
                if weight < DISSOLVE_CC_THRESHOLD:
                    edges_to_remove.append((u, v))

            for u, v in edges_to_remove:
                graph.remove_edge(u, v)
                result.dissolved += 1
        except Exception as exc:
            logger.debug("dissolve edges failed: %s", exc)

    except Exception as exc:
        logger.error("refinement cycle failed: %s", exc)

    result.elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

    if result.reinforced or result.degraded or result.dissolved:
        logger.info(
            "[REFINE] reinforced=%d degraded=%d dissolved=%d relayered=%d | %.0fms",
            result.reinforced, result.degraded, result.dissolved,
            result.relayered, result.elapsed_ms,
        )
    else:
        logger.debug("[REFINE] no changes | %.0fms", result.elapsed_ms)

    return result


# ---------------------------------------------------------------------------
# Integration counter — tracks interactions to trigger refinement
# ---------------------------------------------------------------------------

_interaction_count = 0


def tick() -> bool:
    """Call after each interaction. Returns True if refinement ran."""
    global _interaction_count
    _interaction_count += 1
    if _interaction_count >= CYCLE_INTERVAL:
        _interaction_count = 0
        result = run_refinement_cycle()

        # After refinement, update observation bias
        # This closes the loop: learning → observation → better learning
        try:
            from core.learn.observation_bias import compute_bias
            compute_bias()
        except Exception:
            pass

        return True
    return False
