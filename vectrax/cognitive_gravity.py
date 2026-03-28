"""
Vectrax Cognitive Gravitational Engine
========================================

Dynamic knowledge graph where each node is a star with gravitational mass.
Stars are born from convergence, grow through coherence, and orbit an
immutable nucleus.

Public API:
  create_node          — birth a new star with minimum mass
  connect_nodes        — create/update a gravitational link between two stars
  update_mass          — recalculate a star's mass from connections + coherence + activation
  compute_distance_to_core — distance as inverse function of mass
  detect_convergence   — detect when multiple trajectories converge on the same meaning
  cluster_nodes        — automatic constellation formation via community detection

Principle: nodes are NEVER deleted. Inactive nodes lose mass and drift to
the periphery, but they persist forever.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from vectrax import db
from vectrax import graph as g
from vectrax.core_nucleus import distance_to_centroid, refresh_centroid
from vectrax.embeddings import (
    cosine_similarity,
    decode_embedding,
    embed,
    encode_embedding,
    find_similar,
    mean_embedding,
)
from vectrax.gravity import (
    assign_layer,
    compute_distance_from_mass,
    compute_semantic_gravity,
    compute_star_gravity,
)
from vectrax.identity import (
    CHANNEL_USER,
    assert_no_cross_channel_link,
    validate_channel,
)
from vectrax.models import (
    CONVERGENCE_MIN_PATHS,
    CONVERGENCE_THRESHOLD,
    MIN_MASS,
    Constellation,
    Star,
)


# ---------------------------------------------------------------------------
# 1. create_node
# ---------------------------------------------------------------------------

def create_node(
    content: str,
    channel: str = CHANNEL_USER,
    owner: str = "",
) -> Star:
    """
    Birth a new star with minimum mass.

    Steps:
      1. Generate embedding
      2. Create Star with mass = MIN_MASS
      3. Persist to DB and add to in-memory graph
      4. Compute initial distance to core
      5. Refresh nucleus centroid if star is core-layer
    """
    validate_channel(channel)
    db.init_db()

    vec = embed(content)
    star = Star(
        content=content,
        embedding=encode_embedding(vec),
        mass=MIN_MASS,
        activation_count=1,
        last_activated=time.time(),
        channel=channel,
        owner=owner,
    )
    star.gravity_score = compute_star_gravity(star)
    star.layer = assign_layer(star.gravity_score)
    star.distance_to_core = compute_distance_from_mass(star.mass)

    db.insert_star(star)
    g.add_star(
        star.id,
        layer=star.layer,
        gravity=star.gravity_score,
        mass=star.mass,
        channel=channel,
        owner=owner,
    )

    # Refresh nucleus if this star entered the core
    if star.layer == "core":
        refresh_centroid()

    return star


# ---------------------------------------------------------------------------
# 2. connect_nodes
# ---------------------------------------------------------------------------

def connect_nodes(
    star_a_id: str,
    star_b_id: str,
    similarity: Optional[float] = None,
) -> Dict:
    """
    Create or update a gravitational link between two stars.

    If similarity is not provided, it is computed from their embeddings.
    After linking, both stars' masses are recalculated.
    Cross-channel linking is forbidden.
    """
    db.init_db()
    star_a = db.get_star(star_a_id)
    star_b = db.get_star(star_b_id)

    if not star_a or not star_b:
        missing = star_a_id if not star_a else star_b_id
        raise ValueError(f"Star not found: {missing}")

    # Enforce channel separation
    assert_no_cross_channel_link(star_a.channel, star_b.channel)

    # Compute similarity if not given
    if similarity is None and star_a.embedding and star_b.embedding:
        vec_a = decode_embedding(star_a.embedding)
        vec_b = decode_embedding(star_b.embedding)
        similarity = cosine_similarity(vec_a, vec_b)
    elif similarity is None:
        similarity = 0.0

    g.link_stars(star_a_id, star_b_id, similarity)

    # Recalculate mass for both nodes
    mass_a = update_mass(star_a_id)
    mass_b = update_mass(star_b_id)

    return {
        "star_a": star_a_id,
        "star_b": star_b_id,
        "similarity": round(similarity, 6),
        "mass_a": mass_a,
        "mass_b": mass_b,
    }


# ---------------------------------------------------------------------------
# 3. update_mass
# ---------------------------------------------------------------------------

def update_mass(star_id: str) -> float:
    """
    Recalculate a star's gravitational mass based on:
      1. Number of incoming connections (graph degree)
      2. Average semantic coherence with neighbours
      3. Activation frequency

    Persists the new mass and distance_to_core to the DB.
    Returns the new mass value.
    """
    db.init_db()
    star = db.get_star(star_id)
    if not star:
        raise ValueError(f"Star not found: {star_id}")

    # Get graph degree
    neighbors = g.get_neighbors(star_id)
    in_degree = len(neighbors)

    # Compute average coherence with neighbours
    neighbor_coherence = 0.0
    if neighbors and star.embedding:
        star_vec = decode_embedding(star.embedding)
        coherence_sum = 0.0
        valid = 0
        for nbr_id, edge_weight in neighbors:
            nbr = db.get_star(nbr_id)
            if nbr and nbr.embedding:
                nbr_vec = decode_embedding(nbr.embedding)
                coherence_sum += cosine_similarity(star_vec, nbr_vec)
                valid += 1
        if valid > 0:
            neighbor_coherence = coherence_sum / valid

    # Compute new mass
    new_mass = compute_semantic_gravity(
        star,
        in_degree=in_degree,
        neighbor_coherence=neighbor_coherence,
    )
    new_distance = compute_distance_from_mass(new_mass)

    # Update star
    star.mass = new_mass
    star.distance_to_core = new_distance
    star.layer = assign_layer(star.gravity_score)
    db.update_star(star)

    # Update graph node attrs
    g.update_star_attrs(star_id, mass=new_mass, distance=new_distance)

    return new_mass


# ---------------------------------------------------------------------------
# 4. compute_distance_to_core
# ---------------------------------------------------------------------------

def compute_distance_to_core(star_id: str) -> Dict:
    """
    Compute and return the distance of a star to the nucleus.

    Two measures:
      - mass_distance: 1.0 - mass (structural)
      - semantic_distance: cosine distance to nucleus centroid (semantic)
      - combined: weighted average (0.5 each)
    """
    db.init_db()
    star = db.get_star(star_id)
    if not star:
        raise ValueError(f"Star not found: {star_id}")

    mass_dist = compute_distance_from_mass(star.mass)

    semantic_dist = 1.0
    if star.embedding:
        vec = decode_embedding(star.embedding)
        semantic_dist = distance_to_centroid(vec)

    combined = round(0.5 * mass_dist + 0.5 * semantic_dist, 6)

    # Persist the combined distance
    star.distance_to_core = combined
    db.update_star_mass(star_id, star.mass, combined)

    return {
        "star_id": star_id,
        "mass": round(star.mass, 6),
        "mass_distance": round(mass_dist, 6),
        "semantic_distance": round(semantic_dist, 6),
        "combined_distance": combined,
        "layer": star.layer,
    }


# ---------------------------------------------------------------------------
# 5. detect_convergence
# ---------------------------------------------------------------------------

def detect_convergence(
    text: str,
    channel: str = CHANNEL_USER,
    owner: str = "",
    threshold: float = CONVERGENCE_THRESHOLD,
    min_paths: int = CONVERGENCE_MIN_PATHS,
) -> Dict:
    """
    Detect if multiple information trajectories converge on the same meaning.

    Process:
      1. Generate embedding for the input text
      2. Find all similar existing stars (above threshold)
      3. Group them by constellation membership
      4. If ≥ min_paths distinct constellations/isolated stars contribute,
         a convergence event is detected → birth a new convergence node
         linked to all converging stars
    """
    validate_channel(channel)
    db.init_db()

    vec = embed(text)

    # Find similar stars within the same channel
    channel_stars = db.get_all_stars(channel=channel, owner=owner)
    star_embeddings: List[Tuple[str, object]] = [
        (s.id, decode_embedding(s.embedding))
        for s in channel_stars
        if s.embedding is not None
    ]
    similar = find_similar(vec, star_embeddings, threshold=threshold)

    if len(similar) < min_paths:
        return {
            "convergence_detected": False,
            "similar_count": len(similar),
            "required": min_paths,
            "node": None,
        }

    # Group by constellation (or "isolated" for stars not in any constellation)
    constellations = db.get_all_constellations(channel=channel, owner=owner)
    star_to_constellation: Dict[str, str] = {}
    for c in constellations:
        for sid in c.star_ids:
            star_to_constellation[sid] = c.id

    distinct_sources: Dict[str, List[str]] = {}
    for sid, sim in similar:
        source = star_to_constellation.get(sid, f"isolated_{sid}")
        distinct_sources.setdefault(source, []).append(sid)

    if len(distinct_sources) < min_paths:
        return {
            "convergence_detected": False,
            "similar_count": len(similar),
            "distinct_sources": len(distinct_sources),
            "required": min_paths,
            "node": None,
        }

    # Convergence detected! Create a convergence node
    convergence_node = create_node(
        content=f"[CONVERGENCE] {text}",
        channel=channel,
        owner=owner,
    )

    # Link convergence node to all converging stars
    for sid, sim in similar:
        connect_nodes(convergence_node.id, sid, similarity=sim)

    # Activate all contributing stars
    for sid, _ in similar:
        db.activate_star(sid)

    return {
        "convergence_detected": True,
        "similar_count": len(similar),
        "distinct_sources": len(distinct_sources),
        "convergence_node_id": convergence_node.id,
        "convergence_mass": convergence_node.mass,
        "connected_stars": [sid for sid, _ in similar],
        "node": convergence_node.to_dict(),
    }


# ---------------------------------------------------------------------------
# 6. cluster_nodes
# ---------------------------------------------------------------------------

def cluster_nodes(
    channel: str = CHANNEL_USER,
    owner: str = "",
    min_cluster_size: int = 3,
) -> List[Dict]:
    """
    Detect dense communities in the graph and create/update Constellations.

    Uses the Louvain community detection algorithm from NetworkX for
    weighted graphs, falling back to connected components if Louvain
    is not available.

    Returns a list of cluster summaries.
    """
    validate_channel(channel)
    db.init_db()

    graph = g.get_graph()
    if graph.number_of_nodes() == 0:
        return []

    # Filter to only stars in this channel
    channel_stars = db.get_all_stars(channel=channel, owner=owner)
    channel_ids = {s.id for s in channel_stars}
    star_map = {s.id: s for s in channel_stars}

    # Build subgraph of this channel's stars
    subgraph_nodes = [n for n in graph.nodes if n in channel_ids]
    if len(subgraph_nodes) < min_cluster_size:
        return []

    subgraph = graph.subgraph(subgraph_nodes)

    # Try Louvain community detection
    try:
        from networkx.algorithms.community import louvain_communities
        communities = louvain_communities(subgraph, weight="weight", seed=42)
    except (ImportError, AttributeError):
        # Fallback: connected components
        import networkx as nx
        communities = list(nx.connected_components(subgraph))

    # Filter by minimum size
    communities = [c for c in communities if len(c) >= min_cluster_size]

    # Get existing constellations for deduplication
    existing = {
        frozenset(c.star_ids): c
        for c in db.get_all_constellations(channel=channel, owner=owner)
    }

    results: List[Dict] = []
    for community in communities:
        key = frozenset(community)

        # Compute coherence for this cluster
        vecs = [
            decode_embedding(star_map[sid].embedding)
            for sid in community
            if sid in star_map and star_map[sid].embedding
        ]

        from vectrax.embeddings import cluster_coherence
        coherence = cluster_coherence(vecs) if len(vecs) >= 2 else 1.0

        avg_success = (
            sum(star_map[sid].success_rate for sid in community if sid in star_map)
            / len(community)
        )
        avg_mass = (
            sum(star_map[sid].mass for sid in community if sid in star_map)
            / len(community)
        )

        if key in existing:
            c = existing[key]
            c.repetition_count += 1
            c.coherence_score = coherence
            c.success_rate = avg_success
            c.updated_at = time.time()
        else:
            c = Constellation(
                star_ids=list(community),
                coherence_score=coherence,
                success_rate=avg_success,
                channel=channel,
                owner=owner,
            )

        from vectrax.gravity import compute_constellation_gravity
        c.gravity_score = compute_constellation_gravity(c)
        db.upsert_constellation(c)

        results.append({
            "constellation_id": c.id,
            "member_count": c.member_count,
            "coherence_score": round(coherence, 4),
            "gravity_score": round(c.gravity_score, 4),
            "avg_mass": round(avg_mass, 4),
            "star_ids": list(community),
        })

    # Refresh nucleus after clustering (core stars may have been affected)
    refresh_centroid()

    return results


# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------

def recompute_all_masses(
    channel: str = CHANNEL_USER,
    owner: str = "",
) -> Dict:
    """
    Recalculate mass and distance for all stars in a channel.
    Returns a summary with counts and layer distribution.
    """
    db.init_db()
    stars = db.get_all_stars(channel=channel, owner=owner)
    updated = 0
    for star in stars:
        try:
            update_mass(star.id)
            updated += 1
        except Exception:
            continue

    refresh_centroid()
    return {
        "stars_processed": len(stars),
        "stars_updated": updated,
    }
