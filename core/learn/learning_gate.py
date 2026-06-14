"""
Learning Gate — Aprendizaje Selectivo
=======================================
Antes de incorporar experiencia al universo, evalúa:
¿esto transforma el universo o es ruido?

Criterios de transformación:
  1. NOVEDAD  — ¿es significativamente diferente de lo que ya sé?
                Distancia semántica al centroide del usuario >0.3
  2. COHERENCIA — ¿conecta con algo existente de forma no trivial?
                   Al menos 1 patrón cercano con similitud >0.5
  3. IMPACTO — ¿cambiaría la masa, capa o convergencias del usuario?
               Compara masa antes/después hipotéticamente

Si ningún criterio se cumple → PASSIVE (registra pero no transforma)
Si al menos uno se cumple → ACTIVE (incorpora al universo)

Principio: Vectrax no aprende todo. Aprende lo que lo transforma.

Creador: Mario Bravo Castro
Fecha: 2026-06-14
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List

logger = logging.getLogger("vectrax.learn.learning_gate")

# Thresholds
NOVELTY_THRESHOLD = 0.3      # cosine distance from centroid
COHERENCE_THRESHOLD = 0.5    # similarity to at least one existing pattern
IMPACT_MASS_DELTA = 0.005    # minimum mass change to consider impactful


class LearningType(str, Enum):
    ACTIVE = "active"      # transforms the universe
    PASSIVE = "passive"    # recorded but doesn't change gravitational state


@dataclass
class LearningDecision:
    """Result of the learning gate evaluation."""
    learning_type: LearningType = LearningType.PASSIVE
    novelty: float = 0.0        # distance from centroid (0=identical, 1=completely new)
    coherence: float = 0.0      # max similarity to existing patterns
    impact: float = 0.0         # estimated mass delta
    reason: str = ""

    @property
    def is_active(self) -> bool:
        return self.learning_type == LearningType.ACTIVE


def evaluate(
    embedding,
    user_id: str,
    centroid_embedding=None,
    existing_embeddings: Optional[List] = None,
    current_mass: float = 0.0,
    pattern_count: int = 0,
    topic_diversity: int = 1,
    convergence_links: int = 0,
    is_creator: bool = False,
) -> LearningDecision:
    """
    Evaluate if this input should actively transform the universe.

    Args:
        embedding: vector of the new text
        user_id: who sent it
        centroid_embedding: current star centroid (None if first pattern)
        existing_embeddings: list of recent pattern embeddings
        current_mass: current star mass
        pattern_count: current pattern count
        topic_diversity: number of distinct topics
        convergence_links: number of convergence connections
        is_creator: creator always learns actively

    Returns:
        LearningDecision with type, scores, and reason.
    """
    # Creator always learns actively — foundational input
    if is_creator:
        return LearningDecision(
            learning_type=LearningType.ACTIVE,
            novelty=1.0,
            reason="creator_input",
        )

    # First pattern for this user — always active (birth of a star)
    if centroid_embedding is None or pattern_count == 0:
        return LearningDecision(
            learning_type=LearningType.ACTIVE,
            novelty=1.0,
            reason="first_pattern",
        )

    from vectrax.embeddings import cosine_similarity

    # === CRITERION 1: NOVELTY ===
    # How different is this from what I already know about this user?
    sim_to_centroid = cosine_similarity(embedding, centroid_embedding)
    novelty = 1.0 - sim_to_centroid  # higher = more novel
    has_novelty = novelty >= NOVELTY_THRESHOLD

    # === CRITERION 2: COHERENCE ===
    # Does it connect meaningfully with existing knowledge?
    coherence = 0.0
    if existing_embeddings:
        sims = [cosine_similarity(embedding, e) for e in existing_embeddings]
        coherence = max(sims) if sims else 0.0
    has_coherence = coherence >= COHERENCE_THRESHOLD

    # === CRITERION 3: IMPACT ===
    # Would incorporating this change the star's gravitational properties?
    from vectrax.gravity import compute_user_star_mass
    hypothetical_mass = compute_user_star_mass(
        pattern_count=pattern_count + 1,
        topic_diversity=topic_diversity,
        convergence_connections=convergence_links,
        is_creator=is_creator,
    )
    impact = abs(hypothetical_mass - current_mass)
    has_impact = impact >= IMPACT_MASS_DELTA

    # === DECISION ===
    if has_novelty or has_coherence or has_impact:
        reasons = []
        if has_novelty:
            reasons.append(f"novel({novelty:.2f})")
        if has_coherence:
            reasons.append(f"coherent({coherence:.2f})")
        if has_impact:
            reasons.append(f"impact({impact:.4f})")

        decision = LearningDecision(
            learning_type=LearningType.ACTIVE,
            novelty=novelty,
            coherence=coherence,
            impact=impact,
            reason="+".join(reasons),
        )
        logger.info(
            "[LEARN] ACTIVE | user=%s | %s | nov=%.2f coh=%.2f imp=%.4f",
            user_id[:20], decision.reason, novelty, coherence, impact,
        )
        return decision

    # No criterion met — passive learning
    decision = LearningDecision(
        learning_type=LearningType.PASSIVE,
        novelty=novelty,
        coherence=coherence,
        impact=impact,
        reason="no_transformation",
    )
    logger.info(
        "[LEARN] PASSIVE | user=%s | nov=%.2f coh=%.2f imp=%.4f",
        user_id[:20], novelty, coherence, impact,
    )
    return decision
