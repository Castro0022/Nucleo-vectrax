"""
Contradiction Detector — finds conflicts between current proposals
and past decisions, active principles, or known patterns.

Queries Motor 3 (MemoryEngine) for historical context.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from cognition.types import CognitiveContext, MemoryContext

logger = logging.getLogger("vectrax.cognition.reasoning.contradictions")


class ContradictionDetector:
    """Detects logical or policy contradictions."""

    def detect(
        self,
        description: str,
        context: CognitiveContext,
        memory: Optional[MemoryContext] = None,
    ) -> List[str]:
        """
        Returns a list of contradiction descriptions (empty = no conflicts).
        """
        contradictions: List[str] = []

        # Check against active principles
        if memory and memory.active_principles:
            contradictions.extend(
                self._check_principles(description, memory.active_principles)
            )

        # Check against past rejected decisions
        if memory and memory.related_decisions:
            contradictions.extend(
                self._check_past_rejections(description, memory.related_decisions)
            )

        # Check system health conflicts
        contradictions.extend(self._check_health(context))

        return contradictions

    # ------------------------------------------------------------------
    # Internal checks
    # ------------------------------------------------------------------

    @staticmethod
    def _check_principles(
        description: str,
        principles: List[Dict[str, Any]],
    ) -> List[str]:
        """Flag if proposal description contradicts known restrictions."""
        conflicts: List[str] = []
        desc_lower = description.lower()

        for p in principles:
            p_name = p.get("name", "")
            p_desc = p.get("description", "").lower()

            # Simple keyword conflict detection
            if "no" in p_desc or "prohib" in p_desc or "restrict" in p_desc:
                # Extract restricted terms
                restricted_terms = [
                    w for w in p_desc.split()
                    if len(w) > 4 and w not in ("should", "could", "would", "cannot")
                ]
                for term in restricted_terms:
                    if term in desc_lower:
                        conflicts.append(
                            f"Potential conflict with principle '{p_name}': "
                            f"term '{term}' appears in both"
                        )
                        break

        return conflicts

    @staticmethod
    def _check_past_rejections(
        description: str,
        decisions: List[Dict[str, Any]],
    ) -> List[str]:
        """Flag if a similar proposal was previously rejected."""
        conflicts: List[str] = []
        desc_lower = description.lower()
        desc_words = set(desc_lower.split())

        for d in decisions:
            if d.get("status") != "rejected":
                continue
            past_desc = d.get("description", "").lower()
            past_words = set(past_desc.split())

            # Check word overlap (simple similarity)
            overlap = desc_words & past_words
            significant = {w for w in overlap if len(w) > 4}
            if len(significant) >= 3:
                conflicts.append(
                    f"Similar proposal was rejected (id={d.get('id', '?')}): "
                    f"shared terms: {', '.join(list(significant)[:5])}"
                )

        return conflicts

    @staticmethod
    def _check_health(context: CognitiveContext) -> List[str]:
        """Flag contradictions with current system state."""
        conflicts: List[str] = []
        if context.system_health == "unhealthy" and context.governor_mode != "recover":
            conflicts.append(
                "System is unhealthy but not in recovery mode — "
                "proposals should be conservative"
            )
        return conflicts
