"""
Cognitive Loop — the main perceive → reason → propose → learn cycle.

Implements the full cognitive flow:
  1. perceive()  → CognitiveContext
  2. memory.query() → MemoryContext
  3. reasoning.reason() → ReasoningResult
  4. If SUPERVISED and viable → generate CognitiveProposal
  5. Submit for creator approval
  6. If approved → execute via UCE
  7. memory.learn() → record outcome

In SILENT_OBSERVATION mode, only steps 1, 2, 7 run.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, Optional

from cognition.types import (
    CognitiveContext,
    CognitiveMode,
    CognitiveProposal,
)
from cognition.perception.perception_engine import PerceptionEngine
from cognition.memory.memory_engine import MemoryEngine
from cognition.reasoning.reasoning_engine import ReasoningEngine
from cognition.reasoning.human_review_gate import HumanReviewGate
from cognition.orchestrator.modes import ModeManager
from cognition.orchestrator.proposal_generator import CognitiveProposalGenerator
from cognition.orchestrator.tracer import CognitiveTracer

logger = logging.getLogger("vectrax.cognition.orchestrator.loop")


class CognitiveLoop:
    """
    The main cognitive cycle that ties all four motors together.

    Usage::

        loop = CognitiveLoop(perception, memory, reasoning, gate, modes, tracer)
        result = loop.run_cycle()
    """

    def __init__(
        self,
        perception: PerceptionEngine,
        memory: MemoryEngine,
        reasoning: ReasoningEngine,
        gate: HumanReviewGate,
        modes: ModeManager,
        tracer: CognitiveTracer,
    ) -> None:
        self._perception = perception
        self._memory = memory
        self._reasoning = reasoning
        self._gate = gate
        self._modes = modes
        self._tracer = tracer
        self._proposal_gen = CognitiveProposalGenerator()

    def run_cycle(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Execute one full cognitive cycle.

        Args:
            dry_run: if True, do not submit proposals or execute.

        Returns a dict with cycle results.
        """
        cycle_id = uuid.uuid4().hex[:12]
        self._tracer.begin_cycle(cycle_id)
        t0 = time.time()
        result: Dict[str, Any] = {
            "cycle_id": cycle_id,
            "mode": self._modes.current.value,
            "dry_run": dry_run,
        }

        try:
            # 1. Perceive
            t_step = time.time()
            context = self._perception.perceive()
            self._tracer.record(
                "perceive",
                output_summary=f"{context.signal_count} signals",
                duration_ms=(time.time() - t_step) * 1000,
            )
            result["context"] = context.to_dict()

            # Auto-adjust mode based on governor
            self._modes.auto_adjust(context.governor_mode)

            # 2. Memory query
            t_step = time.time()
            intent = context.dominant_category.value if context.dominant_category else ""
            mem_ctx = self._memory.query(intent=intent, context=context)
            self._tracer.record(
                "memory_query",
                input_summary=f"intent={intent}",
                output_summary=f"tier={mem_ctx.tier.value}",
                duration_ms=(time.time() - t_step) * 1000,
            )
            result["memory_tier"] = mem_ctx.tier.value

            # Store signals in memory
            self._memory.store_signals(context.signals)

            # Silent mode: stop here
            if self._modes.current == CognitiveMode.SILENT_OBSERVATION:
                self._tracer.record("silent_mode", output_summary="observation only")
                result["outcome"] = "observed"
                self._tracer.end_cycle()
                result["duration_ms"] = round((time.time() - t0) * 1000, 2)
                return result

            # 3. Reason
            t_step = time.time()
            reasoning_result = self._reasoning.reason(context, mem_ctx)
            self._tracer.record(
                "reason",
                output_summary=f"rec={reasoning_result.recommendation}, risk={reasoning_result.risk_level}",
                duration_ms=(time.time() - t_step) * 1000,
            )
            result["reasoning"] = reasoning_result.to_dict()

            # 4. Generate proposal (if viable)
            proposal: Optional[CognitiveProposal] = None
            if reasoning_result.recommendation in ("proceed", "review"):
                proposal = self._proposal_gen.generate(context, reasoning_result)
                if proposal:
                    self._tracer.record(
                        "proposal_generated",
                        output_summary=f"id={proposal.id}",
                    )
                    result["proposal_id"] = proposal.id

            if proposal is None:
                self._tracer.record("no_proposal", output_summary="blocked or not viable")
                result["outcome"] = "no_proposal"
                self._tracer.end_cycle()
                result["duration_ms"] = round((time.time() - t0) * 1000, 2)
                return result

            # 5. Submit for approval (unless dry_run)
            if dry_run:
                self._tracer.record("dry_run", output_summary="proposal not submitted")
                result["outcome"] = "dry_run"
                result["proposal"] = proposal.to_dict()
                self._tracer.end_cycle()
                result["duration_ms"] = round((time.time() - t0) * 1000, 2)
                return result

            self._gate.submit(proposal)
            self._tracer.record(
                "submitted",
                output_summary=f"awaiting approval: {proposal.id}",
            )
            result["outcome"] = "submitted"
            result["proposal"] = proposal.to_dict()

        except Exception as exc:
            self._tracer.record(
                "error",
                error=str(exc),
                success=False,
            )
            result["outcome"] = "error"
            result["error"] = str(exc)[:200]
            logger.error("Cognitive cycle error: %s", exc)

        self._tracer.end_cycle()
        result["duration_ms"] = round((time.time() - t0) * 1000, 2)
        return result
