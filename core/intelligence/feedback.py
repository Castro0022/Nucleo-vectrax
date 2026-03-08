"""
Vectrax Performance Feedback
===============================
Records model performance (latency, tokens, success rate, synthesis rank)
as nodes and edges in the KnowledgeGraph.

This creates a feedback loop: the system learns which models perform best
for which task types over time, enabling progressive routing optimisation.

Privacy: only aggregated operational metrics are stored — no message content,
prompts, or personal data.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

from cognition.memory.knowledge_graph import KnowledgeGraph, GRAPH_PATH
from cognition.types import KnowledgeNodeType
from core.intelligence.parallel import ParallelQueryResult
from core.intelligence.synthesizer import SynthesizedResult

logger = logging.getLogger("vectrax.intelligence.feedback")


class PerformanceFeedback:
    """
    Records model performance metrics into the KnowledgeGraph.

    Nodes: MODEL_PERFORMANCE per provider:model pair
    Edges: performance_feedback linking task_type → model nodes with weight

    Usage::

        fb = PerformanceFeedback()
        fb.record(parallel_result, synthesized_result, task_type="code")
    """

    def __init__(self, graph: Optional[KnowledgeGraph] = None) -> None:
        self._graph = graph

    def _get_graph(self) -> KnowledgeGraph:
        if self._graph is None:
            self._graph = KnowledgeGraph()
        return self._graph

    def record(
        self,
        parallel_result: ParallelQueryResult,
        synthesized: Optional[SynthesizedResult] = None,
        task_type: str = "unknown",
    ) -> None:
        """
        Record performance data from a multi-model query.

        Creates/updates a MODEL_PERFORMANCE node for each provider:model,
        and adds edges linking the task_type to each model with weighted
        performance scores.

        Args:
            parallel_result: Raw parallel query results.
            synthesized: Optional synthesis result with rankings.
            task_type: The classified task type (e.g. "code", "reasoning").
        """
        graph = self._get_graph()

        # Ensure a task_type anchor node exists
        task_node_id = f"task:{task_type}"
        graph.add_node(
            node_id=task_node_id,
            node_type=KnowledgeNodeType.PATTERN,
            label=f"Task type: {task_type}",
        )

        # Build rank lookup from synthesis
        rank_map: Dict[str, int] = {}
        score_map: Dict[str, float] = {}
        if synthesized:
            for ps in synthesized.provider_scores:
                rank_map[ps.provider_name] = ps.rank
                score_map[ps.provider_name] = ps.total_score

        now = time.time()

        for pr in parallel_result.results:
            model_node_id = f"model:{pr.provider_name}:{pr.model}"

            # Aggregate metrics (abstract, no content)
            metrics = {
                "latency_ms": round(pr.latency_ms, 2),
                "success": pr.success,
                "tokens": (pr.response.total_tokens if pr.response else 0),
                "task_type": task_type,
                "rank": rank_map.get(pr.provider_name, 0),
                "synthesis_score": round(score_map.get(pr.provider_name, 0.0), 4),
                "last_updated": now,
            }

            # Create or update the model performance node
            existing = graph.get_node(model_node_id)
            if existing:
                # Merge with running stats
                meta = existing.metadata
                count = meta.get("query_count", 0) + 1
                total_latency = meta.get("total_latency_ms", 0.0) + pr.latency_ms
                total_success = meta.get("success_count", 0) + (1 if pr.success else 0)
                total_tokens = meta.get("total_tokens", 0) + metrics["tokens"]

                meta.update({
                    "query_count": count,
                    "total_latency_ms": round(total_latency, 2),
                    "avg_latency_ms": round(total_latency / count, 2),
                    "success_count": total_success,
                    "success_rate": round(total_success / count, 4),
                    "total_tokens": total_tokens,
                    "last_task_type": task_type,
                    "last_rank": metrics["rank"],
                    "last_synthesis_score": metrics["synthesis_score"],
                    "last_updated": now,
                })
                graph.add_node(
                    node_id=model_node_id,
                    node_type=KnowledgeNodeType.MODEL_PERFORMANCE,
                    label=f"{pr.provider_name}:{pr.model}",
                    metadata=meta,
                )
            else:
                graph.add_node(
                    node_id=model_node_id,
                    node_type=KnowledgeNodeType.MODEL_PERFORMANCE,
                    label=f"{pr.provider_name}:{pr.model}",
                    metadata={
                        "provider": pr.provider_name,
                        "model": pr.model,
                        "query_count": 1,
                        "total_latency_ms": round(pr.latency_ms, 2),
                        "avg_latency_ms": round(pr.latency_ms, 2),
                        "success_count": 1 if pr.success else 0,
                        "success_rate": 1.0 if pr.success else 0.0,
                        "total_tokens": metrics["tokens"],
                        "last_task_type": task_type,
                        "last_rank": metrics["rank"],
                        "last_synthesis_score": metrics["synthesis_score"],
                        "last_updated": now,
                    },
                )

            # Edge: task_type → model performance (weight = synthesis score)
            weight = score_map.get(pr.provider_name, 0.5 if pr.success else 0.1)
            graph.add_edge(
                source=task_node_id,
                target=model_node_id,
                relation="performance_feedback",
                weight=round(weight, 4),
                metadata={
                    "task_type": task_type,
                    "latency_ms": round(pr.latency_ms, 2),
                    "success": pr.success,
                    "timestamp": now,
                },
            )

        # Save once (all nodes and edges already auto-save, but explicit save
        # ensures consistency)
        graph.save()

        logger.debug(
            "Recorded performance feedback for %d providers on task=%s",
            len(parallel_result.results),
            task_type,
        )

    def get_model_stats(self, provider: str, model: str) -> Optional[Dict]:
        """Retrieve accumulated stats for a specific model."""
        graph = self._get_graph()
        node = graph.get_node(f"model:{provider}:{model}")
        if node:
            return node.metadata
        return None

    def get_best_model_for_task(self, task_type: str) -> Optional[Dict]:
        """
        Query the graph for the best-performing model for a task type.

        Returns the model node metadata with the highest synthesis score
        among recent feedback edges for this task type.
        """
        graph = self._get_graph()
        task_node_id = f"task:{task_type}"
        related = graph.related_to(task_node_id, max_depth=1)

        if not related:
            return None

        # Filter to performance_feedback edges, pick highest weight
        perf_nodes = [
            r for r in related
            if r.get("relation") == "performance_feedback"
            and r.get("direction") == "outgoing"
        ]
        if not perf_nodes:
            return None

        best = max(perf_nodes, key=lambda r: r.get("weight", 0))
        return best.get("node")

    def summary(self) -> Dict:
        """Return summary of all MODEL_PERFORMANCE nodes in the graph."""
        graph = self._get_graph()
        nodes = graph.nodes_by_type(KnowledgeNodeType.MODEL_PERFORMANCE)
        return {
            "total_models_tracked": len(nodes),
            "models": {
                n.id: {
                    "label": n.label,
                    "query_count": n.metadata.get("query_count", 0),
                    "success_rate": n.metadata.get("success_rate", 0.0),
                    "avg_latency_ms": n.metadata.get("avg_latency_ms", 0.0),
                }
                for n in nodes
            },
        }
