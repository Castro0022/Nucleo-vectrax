"""
Cognitive Orchestrator — public entry point for the cognitive layer.

Assembles all four motors and provides a unified API for CLI,
daemon, and programmatic usage.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from cognition.types import CognitiveMode, CognitiveProposal
from cognition.perception.perception_engine import PerceptionEngine
from cognition.memory.memory_engine import MemoryEngine
from cognition.reasoning.reasoning_engine import ReasoningEngine
from cognition.reasoning.human_review_gate import HumanReviewGate
from cognition.orchestrator.modes import ModeManager
from cognition.orchestrator.tracer import CognitiveTracer
from cognition.orchestrator.cognitive_loop import CognitiveLoop

logger = logging.getLogger("vectrax.cognition.orchestrator")


class CognitiveOrchestrator:
    """
    Entry point for the Vectrax cognitive layer.

    Usage::

        orch = CognitiveOrchestrator()
        result = orch.run_cycle()          # full cognitive cycle
        orch.set_mode("supervised")        # change mode
        status = orch.status()             # inspect all motors
    """

    def __init__(self) -> None:
        # Motor 1: Perception
        self.perception = PerceptionEngine(persist=True)
        # Motor 3: Memory (before Motor 2 because reasoning queries it)
        self.memory = MemoryEngine()
        # Motor 2: Reasoning
        self.reasoning = ReasoningEngine()
        # Gate
        self.gate = HumanReviewGate()
        # Mode manager
        self.modes = ModeManager()
        # Tracer
        self.tracer = CognitiveTracer()
        # Main loop
        self._loop = CognitiveLoop(
            perception=self.perception,
            memory=self.memory,
            reasoning=self.reasoning,
            gate=self.gate,
            modes=self.modes,
            tracer=self.tracer,
        )

    # ------------------------------------------------------------------
    # Main API
    # ------------------------------------------------------------------

    def run_cycle(self, dry_run: bool = False) -> Dict[str, Any]:
        """Execute one full cognitive cycle."""
        return self._loop.run_cycle(dry_run=dry_run)

    def set_mode(self, mode: str) -> str:
        """
        Set cognitive mode.

        Args:
            mode: "silent" or "supervised"
        """
        mode_map = {
            "silent": CognitiveMode.SILENT_OBSERVATION,
            "silent_observation": CognitiveMode.SILENT_OBSERVATION,
            "supervised": CognitiveMode.SUPERVISED_PROPOSAL,
            "supervised_proposal": CognitiveMode.SUPERVISED_PROPOSAL,
        }
        cog_mode = mode_map.get(mode.lower())
        if cog_mode is None:
            raise ValueError(
                f"Unknown mode '{mode}'. Use 'silent' or 'supervised'."
            )
        self.modes.set_mode(cog_mode)
        return cog_mode.value

    def get_mode(self) -> str:
        """Return current cognitive mode."""
        return self.modes.current.value

    # ------------------------------------------------------------------
    # Proposals
    # ------------------------------------------------------------------

    def approve(self, proposal_id: str) -> Optional[CognitiveProposal]:
        """Approve a cognitive proposal (creator-only)."""
        proposal = self.gate.approve(proposal_id)
        if proposal:
            self.memory.learn(
                proposal_id=proposal_id,
                outcome="approved",
                description=proposal.description,
            )
        return proposal

    def reject(self, proposal_id: str) -> Optional[CognitiveProposal]:
        """Reject a cognitive proposal (creator-only)."""
        proposal = self.gate.reject(proposal_id)
        if proposal:
            self.memory.learn(
                proposal_id=proposal_id,
                outcome="rejected",
                description=proposal.description,
            )
        return proposal

    def list_proposals(
        self,
        status: Optional[str] = None,
    ) -> List[CognitiveProposal]:
        """List cognitive proposals."""
        return self.gate.list_proposals(status=status)

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Full status of all four motors."""
        return {
            "mode": self.modes.current.value,
            "perception": self.perception.status(),
            "memory": self.memory.status(),
            "reasoning": self.reasoning.status(),
            "proposals_pending": len(self.gate.list_proposals(status="pending")),
        }

    def inspect(self) -> Dict[str, Any]:
        """Detailed inspection for debugging."""
        return {
            "status": self.status(),
            "last_context": (
                self.perception.last_context.to_dict()
                if self.perception.last_context else None
            ),
            "recent_traces": self.tracer.recent(10),
            "buffer_stats": self.perception.buffer_stats(),
        }


# ---------------------------------------------------------------------------
# Module-level singleton (lazy)
# ---------------------------------------------------------------------------

_orchestrator: Optional[CognitiveOrchestrator] = None


def get_orchestrator() -> CognitiveOrchestrator:
    """Return the singleton CognitiveOrchestrator."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = CognitiveOrchestrator()
    return _orchestrator
