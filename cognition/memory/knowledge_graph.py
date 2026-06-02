"""
Knowledge Graph — directed in-memory graph with JSON persistence.

Nodes represent cognitive concepts (decisions, patterns, signals, errors,
principles, connectors).  Edges represent relationships with weights.

Persisted in vault/knowledge_graph.json.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set, Tuple

from cognition.types import KnowledgeNodeType

logger = logging.getLogger("vectrax.cognition.memory.knowledge_graph")

VAULT_DIR = os.environ.get("VECTRAX_VAULT_DIR", os.path.join(os.path.expanduser("~"), "Vectrax", "vault"))
GRAPH_PATH = os.path.join(VAULT_DIR, "knowledge_graph.json")


@dataclass
class KnowledgeNode:
    """A node in the knowledge graph."""
    id: str
    node_type: KnowledgeNodeType
    label: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "node_type": self.node_type.value,
            "label": self.label,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "KnowledgeNode":
        return cls(
            id=d["id"],
            node_type=KnowledgeNodeType(d["node_type"]),
            label=d.get("label", ""),
            metadata=d.get("metadata", {}),
            created_at=d.get("created_at", 0.0),
        )


@dataclass
class KnowledgeEdge:
    """A directed edge between two nodes."""
    source: str
    target: str
    relation: str = "related_to"
    weight: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "KnowledgeEdge":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class KnowledgeGraph:
    """
    Directed graph for cognitive knowledge.

    Thread-safe for single-writer usage (typical for Vectrax daemon).
    """

    def __init__(self, path: str = GRAPH_PATH) -> None:
        self._path = path
        self._nodes: Dict[str, KnowledgeNode] = {}
        self._edges: List[KnowledgeEdge] = []
        self._adjacency: Dict[str, List[KnowledgeEdge]] = {}  # outgoing
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not os.path.isfile(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for nd in data.get("nodes", []):
                node = KnowledgeNode.from_dict(nd)
                self._nodes[node.id] = node
            for ed in data.get("edges", []):
                edge = KnowledgeEdge.from_dict(ed)
                self._edges.append(edge)
                self._adjacency.setdefault(edge.source, []).append(edge)
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("Knowledge graph load failed: %s", exc)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        data = {
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "edges": [e.to_dict() for e in self._edges],
        }
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

    # ------------------------------------------------------------------
    # Node operations
    # ------------------------------------------------------------------

    def add_node(
        self,
        node_id: str,
        node_type: KnowledgeNodeType,
        label: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> KnowledgeNode:
        if node_id in self._nodes:
            # Update existing
            node = self._nodes[node_id]
            if label:
                node.label = label
            if metadata:
                node.metadata.update(metadata)
            return node
        node = KnowledgeNode(
            id=node_id,
            node_type=node_type,
            label=label[:200],
            metadata=metadata or {},
        )
        self._nodes[node_id] = node
        self.save()
        return node

    def get_node(self, node_id: str) -> Optional[KnowledgeNode]:
        return self._nodes.get(node_id)

    def remove_node(self, node_id: str) -> bool:
        if node_id not in self._nodes:
            return False
        del self._nodes[node_id]
        # Remove all edges involving this node
        self._edges = [
            e for e in self._edges
            if e.source != node_id and e.target != node_id
        ]
        self._adjacency.pop(node_id, None)
        for src in list(self._adjacency):
            self._adjacency[src] = [
                e for e in self._adjacency[src] if e.target != node_id
            ]
        self.save()
        return True

    # ------------------------------------------------------------------
    # Edge operations
    # ------------------------------------------------------------------

    def add_edge(
        self,
        source: str,
        target: str,
        relation: str = "related_to",
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> KnowledgeEdge:
        edge = KnowledgeEdge(
            source=source,
            target=target,
            relation=relation,
            weight=weight,
            metadata=metadata or {},
        )
        self._edges.append(edge)
        self._adjacency.setdefault(source, []).append(edge)
        self.save()
        return edge

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def related_to(self, node_id: str, max_depth: int = 1) -> List[Dict[str, Any]]:
        """Find all nodes connected to *node_id* (outgoing + incoming)."""
        related: List[Dict[str, Any]] = []
        visited: Set[str] = {node_id}
        frontier = [node_id]

        for _ in range(max_depth):
            next_frontier: List[str] = []
            for nid in frontier:
                # Outgoing
                for edge in self._adjacency.get(nid, []):
                    if edge.target not in visited:
                        visited.add(edge.target)
                        next_frontier.append(edge.target)
                        node = self._nodes.get(edge.target)
                        if node:
                            related.append({
                                "node": node.to_dict(),
                                "relation": edge.relation,
                                "weight": edge.weight,
                                "direction": "outgoing",
                            })
                # Incoming
                for edge in self._edges:
                    if edge.target == nid and edge.source not in visited:
                        visited.add(edge.source)
                        next_frontier.append(edge.source)
                        node = self._nodes.get(edge.source)
                        if node:
                            related.append({
                                "node": node.to_dict(),
                                "relation": edge.relation,
                                "weight": edge.weight,
                                "direction": "incoming",
                            })
            frontier = next_frontier

        return related

    def strongest_connections(self, n: int = 10) -> List[Dict[str, Any]]:
        """Return the *n* highest-weight edges."""
        sorted_edges = sorted(self._edges, key=lambda e: e.weight, reverse=True)
        return [e.to_dict() for e in sorted_edges[:n]]

    def nodes_by_type(self, node_type: KnowledgeNodeType) -> List[KnowledgeNode]:
        return [n for n in self._nodes.values() if n.node_type == node_type]

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def summary(self) -> Dict[str, Any]:
        type_counts: Dict[str, int] = {}
        for n in self._nodes.values():
            type_counts[n.node_type.value] = type_counts.get(n.node_type.value, 0) + 1
        return {
            "nodes": self.node_count,
            "edges": self.edge_count,
            "types": type_counts,
        }
