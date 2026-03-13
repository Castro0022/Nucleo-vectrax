"""
Memory Engine — unified API for the cognitive orchestrator.

Provides a single interface to query all stores and the knowledge graph,
and to record learning outcomes after execution.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Dict, List, Optional

from cognition.types import (
    CognitiveContext,
    CognitiveSignal,
    KnowledgeNodeType,
    MemoryContext,
    MemoryTier,
)
from cognition.memory.stores import (
    DecisionStore,
    ErrorStore,
    PatternStore,
    PrincipleStore,
    SignalStore,
)
from cognition.memory.knowledge_graph import KnowledgeGraph

logger = logging.getLogger("vectrax.cognition.memory")


class MemoryEngine:
    """
    Motor 3 — Deep Structural Memory.

    Usage::

        engine = MemoryEngine()
        mem = engine.query("security")      # search across all stores
        engine.learn(outcome_dict)           # record post-execution result
    """

    def __init__(self) -> None:
        self.decisions = DecisionStore()
        self.patterns = PatternStore()
        self.signals = SignalStore()
        self.errors = ErrorStore()
        self.principles = PrincipleStore()
        self.graph = KnowledgeGraph()

    # ------------------------------------------------------------------
    # Query — unified search across all stores
    # ------------------------------------------------------------------

    def query(self, intent: str = "", context: Optional[CognitiveContext] = None) -> MemoryContext:
        """
        Search all stores for memories relevant to *intent* or *context*.
        Returns a MemoryContext ready for the reasoning engine.
        """
        mem = MemoryContext()

        # Decisions
        if intent:
            mem.related_decisions = self.decisions.search(intent)[:10]
        else:
            mem.related_decisions = self.decisions.recent(10)

        # Patterns (search by tier — HOT first)
        hot_patterns = self.patterns.search_by_tier("HOT")
        warm_patterns = self.patterns.search_by_tier("WARM")
        all_patterns = hot_patterns.get("HOT", []) + warm_patterns.get("WARM", [])
        if intent:
            intent_lower = intent.lower()
            mem.related_patterns = [
                p for p in all_patterns
                if intent_lower in p.get("intent", "").lower()
                or intent_lower in p.get("summary", "").lower()
            ][:10]
        else:
            mem.related_patterns = all_patterns[:10]

        # Errors
        mem.related_errors = self.errors.recent(5)

        # Principles
        mem.active_principles = self.principles.get_active()

        # Knowledge graph
        if intent:
            # Try to find a node matching the intent
            for node_type in KnowledgeNodeType:
                nodes = self.graph.nodes_by_type(node_type)
                for node in nodes:
                    if intent.lower() in node.label.lower():
                        connections = self.graph.related_to(node.id)
                        mem.graph_connections.extend(connections[:5])
                        break
                if mem.graph_connections:
                    break

        # Determine memory tier
        if mem.related_patterns or mem.graph_connections:
            mem.tier = MemoryTier.STRUCTURAL
        elif mem.related_decisions:
            mem.tier = MemoryTier.OPERATIVE
        else:
            mem.tier = MemoryTier.RAW

        return mem

    # ------------------------------------------------------------------
    # Store — record signals from perception
    # ------------------------------------------------------------------

    def store_signals(self, signals: List[CognitiveSignal]) -> int:
        """
        Persist classified signals to the signal store.
        Returns number stored.
        """
        count = 0
        for s in signals:
            self.signals.record(
                signal_id=s.id,
                source=s.source,
                category=s.category.value,
                priority=s.priority,
                intent=s.intent,
            )
            count += 1
        return count

    # ------------------------------------------------------------------
    # Learn — record outcome after execution
    # ------------------------------------------------------------------

    def learn(
        self,
        proposal_id: str,
        outcome: str,
        description: str = "",
        error_info: Optional[str] = None,
    ) -> None:
        """
        Record a learning outcome after proposal execution.

        Updates: decision store, pattern store, error store (if failed),
        and knowledge graph.
        """
        # Record decision outcome
        self.decisions.record(
            decision_id=proposal_id,
            description=description[:200],
            status=outcome,  # "executed", "failed", "rejected"
        )

        # Record pattern
        fingerprint = hashlib.sha256(
            f"{proposal_id}:{description[:50]}".encode()
        ).hexdigest()[:12]
        self.patterns.record_pattern(
            fingerprint=fingerprint,
            domain="cognition",
            intent=description[:50],
            outcome=outcome,
            summary=description[:200],
        )

        # Record error if failed
        if outcome == "failed" and error_info:
            self.errors.record(
                error_type="execution_failure",
                description=error_info[:200],
                source=proposal_id,
            )

        # Update knowledge graph
        self.graph.add_node(
            node_id=proposal_id,
            node_type=KnowledgeNodeType.DECISION,
            label=description[:100],
            metadata={"outcome": outcome},
        )

        logger.info("Learning recorded: %s → %s", proposal_id, outcome)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Status summary for CLI / inspection."""
        return {
            "decisions": len(self.decisions.recent(1000)),
            "patterns": sum(
                len(v) for v in self.patterns.search_by_tier().values()
            ),
            "signals": len(self.signals.recent(1000)),
            "errors": len(self.errors.recent(1000)),
            "principles": len(self.principles.get_active()),
            "graph": self.graph.summary(),
        }
