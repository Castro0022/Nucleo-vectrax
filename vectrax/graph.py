"""
In-memory NetworkX graph for managing star nodes and their similarity edges.

The graph is rebuilt from the SQLite ledger on startup and kept in sync
during a session. It is not persisted separately — the ledger is the source
of truth.
"""
from __future__ import annotations

from typing import Dict, List, Set, Tuple

import networkx as nx

# Module-level graph instance (lazy-initialised)
_graph: nx.Graph | None = None


def get_graph() -> nx.Graph:
    global _graph
    if _graph is None:
        _graph = nx.Graph()
    return _graph


def reset_graph() -> None:
    global _graph
    _graph = nx.Graph()


# ---------------------------------------------------------------------------
# Node (star) operations
# ---------------------------------------------------------------------------

def add_star(star_id: str, **attrs) -> None:
    """Add a star node to the graph, optionally with metadata attributes."""
    get_graph().add_node(star_id, **attrs)


def remove_star(star_id: str) -> None:
    g = get_graph()
    if g.has_node(star_id):
        g.remove_node(star_id)


def update_star_attrs(star_id: str, **attrs) -> None:
    g = get_graph()
    if g.has_node(star_id):
        g.nodes[star_id].update(attrs)


# ---------------------------------------------------------------------------
# Edge (similarity link) operations
# ---------------------------------------------------------------------------

def link_stars(star_id_a: str, star_id_b: str, similarity: float) -> None:
    """Create or update a weighted similarity edge between two stars."""
    g = get_graph()
    if not g.has_node(star_id_a):
        g.add_node(star_id_a)
    if not g.has_node(star_id_b):
        g.add_node(star_id_b)
    g.add_edge(star_id_a, star_id_b, weight=similarity)


def get_neighbors(star_id: str) -> List[Tuple[str, float]]:
    """Return (neighbor_id, similarity_weight) pairs for a star."""
    g = get_graph()
    if not g.has_node(star_id):
        return []
    return [
        (nbr, g[star_id][nbr].get("weight", 0.0))
        for nbr in g.neighbors(star_id)
    ]


# ---------------------------------------------------------------------------
# Component / cluster detection
# ---------------------------------------------------------------------------

def get_components() -> List[Set[str]]:
    """Return all connected components as sets of star IDs."""
    return [set(c) for c in nx.connected_components(get_graph())]


def get_component_of(star_id: str) -> Set[str]:
    """Return the connected component that contains the given star."""
    g = get_graph()
    if not g.has_node(star_id):
        return {star_id}
    return set(nx.node_connected_component(g, star_id))


def get_dense_components(min_size: int = 3) -> List[Set[str]]:
    """Return connected components with at least `min_size` members."""
    return [c for c in get_components() if len(c) >= min_size]


# ---------------------------------------------------------------------------
# Graph stats
# ---------------------------------------------------------------------------

def graph_summary() -> Dict:
    g = get_graph()
    components = list(nx.connected_components(g))
    return {
        "nodes": g.number_of_nodes(),
        "edges": g.number_of_edges(),
        "components": len(components),
        "largest_component": max((len(c) for c in components), default=0),
        "isolated_stars": sum(1 for c in components if len(c) == 1),
    }


def ascii_map(max_nodes: int = 40) -> str:
    """
    Return a text-based summary of the graph structure.
    Each line represents one connected component.
    """
    g = get_graph()
    components = sorted(
        nx.connected_components(g), key=len, reverse=True
    )
    lines = []
    for i, comp in enumerate(components[:max_nodes], 1):
        size = len(comp)
        star_char = "★"
        bar = star_char * min(size, 30)
        lines.append(f"  [{i:>3}] {bar} ({size} stars)")
    if not lines:
        lines.append("  (empty — no stars ingested yet)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Seed graph from DB on startup
# ---------------------------------------------------------------------------

def load_from_db() -> None:
    """Rebuild the in-memory graph from all stars and their connections."""
    from vectrax import db
    from vectrax.embeddings import decode_embedding, find_similar
    from vectrax.models import SIMILARITY_THRESHOLD

    reset_graph()
    stars = db.get_all_stars()

    # Add all nodes
    for s in stars:
        add_star(s.id, layer=s.layer, gravity=s.gravity_score)

    # Rebuild edges from embeddings
    embeddings: List[Tuple[str, object]] = []
    for s in stars:
        if s.embedding:
            embeddings.append((s.id, decode_embedding(s.embedding)))

    # For each star, find similar and add edges (O(n²) — acceptable for <10k stars)
    for i, (sid, vec) in enumerate(embeddings):
        others = embeddings[:i] + embeddings[i + 1:]
        similar = find_similar(vec, others, threshold=SIMILARITY_THRESHOLD)
        for other_id, sim in similar:
            if not get_graph().has_edge(sid, other_id):
                link_stars(sid, other_id, sim)
