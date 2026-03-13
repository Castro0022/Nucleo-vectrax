"""
Proposal Generator — converts ReasoningResult into CognitiveProposal.

Only generates proposals when the reasoning engine recommends "proceed"
or "review".  Blocked results never become proposals.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from cognition.types import (
    CognitiveContext,
    CognitiveProposal,
    ProposalStatus,
    ReasoningResult,
)

logger = logging.getLogger("vectrax.cognition.orchestrator.proposal_gen")


class CognitiveProposalGenerator:
    """Converts reasoning output into a structured cognitive proposal."""

    def generate(
        self,
        context: CognitiveContext,
        reasoning: ReasoningResult,
        description: str = "",
        actions: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[CognitiveProposal]:
        """
        Generate a proposal from reasoning results.

        Returns None if the reasoning recommends "block".
        """
        if reasoning.recommendation == "block":
            logger.warning(
                "Proposal generation blocked: %s",
                reasoning.constitutional_reasons or "CRITICAL risk",
            )
            return None

        # Build description
        if not description:
            description = self._auto_description(context, reasoning)

        proposal = CognitiveProposal(
            status=ProposalStatus.DRAFT,
            description=description[:200],
            context_id=context.id,
            reasoning=reasoning,
            actions=actions or [],
        )

        logger.info(
            "Proposal generated: %s (rec=%s, risk=%s)",
            proposal.id, reasoning.recommendation, reasoning.risk_level,
        )
        return proposal

    @staticmethod
    def _auto_description(
        context: CognitiveContext,
        reasoning: ReasoningResult,
    ) -> str:
        """Generate an automatic description from context + reasoning."""
        parts = []
        if context.dominant_category:
            parts.append(f"Dominant: {context.dominant_category.value}")
        parts.append(f"Signals: {context.signal_count}")
        parts.append(f"Risk: {reasoning.risk_level} ({reasoning.risk_score:.3f})")
        if reasoning.impact:
            parts.append(f"Impact: {reasoning.impact.overall:.3f}")
        parts.append(f"Rec: {reasoning.recommendation}")
        return " | ".join(parts)
