"""
Vectrax Convergence Engine
============================

Automatic convergence detection and convergence-star coordinate system.

When multiple semantic routes (trajectories, constellations, or isolated
stars) independently arrive at the same meaning, Vectrax automatically
creates a **convergence star** — a special node that acts as an internal
coordinate for organising, finding and reusing knowledge.

Convergence stars are the landmarks of the cognitive universe:
  - They are created automatically, never manually
  - They mark points where knowledge has been independently confirmed
  - They serve as coordinates: "find everything near convergence X"
  - They are ordered by gravitational distance to the nucleus
  - Higher-mass convergence stars are closer to the nucleus = more reliable

Public API:
  auto_check_convergence — hook for the ingest pipeline
  list_coordinates       — all convergence stars ordered by distance to nucleus
  find_nearest_coordinate — find the closest convergence coordinate to a query
  get_knowledge_around   — retrieve all knowledge within a coordinate's radius
  navigate_by_coordinates — given text, return relevant knowledge via coordinates
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

from vectrax import db
from vectrax import graph as g
from vectrax.core_nucleus import refresh_centroid
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
    compute_collective_gravity,
    compute_distance_from_mass,
    compute_semantic_gravity,
    compute_star_gravity,
)
from vectrax.identity import CHANNEL_USER, validate_channel
from vectrax.models import (
    COLLECTIVE_OWNER,
    COLLECTIVE_PREFIX,
    CONVERGENCE_MIN_PATHS,
    CONVERGENCE_PREFIX,
    CONVERGENCE_THRESHOLD,
    COORDINATE_SEARCH_RADIUS,
    CROSS_USER_CONVERGENCE_THRESHOLD,
    CROSS_USER_MIN_OWNERS,
    MIN_MASS,
    STAR_TYPE_CONVERGENCE,
    STAR_TYPE_PRIMARY,
    Star,
)

logger = logging.getLogger("vectrax.convergence_engine")


# ---------------------------------------------------------------------------
# 1. Automatic convergence detection (pipeline hook)
# ---------------------------------------------------------------------------

def auto_check_convergence(
    star: Star,
    vec: np.ndarray,
    channel: str,
    owner: str,
) -> Optional[Star]:
    """
    Called automatically after every primary star is ingested.

    Checks if this new star causes a convergence event:
      1. Find all existing stars similar to this one (above threshold)
      2. Exclude other convergence stars (only primary stars count as paths)
      3. Group similar primaries by their source (constellation or isolated)
      4. If ≥ CONVERGENCE_MIN_PATHS distinct sources converge,
         create a convergence star and link it to all converging primaries

    Returns the convergence star if created, None otherwise.
    """
    db.init_db()

    # Skip if the star itself is a convergence star (avoid cascading)
    if star.star_type == STAR_TYPE_CONVERGENCE:
        return None

    # Find similar PRIMARY stars in this channel
    channel_stars = db.get_all_stars(channel=channel, owner=owner)
    primary_embeddings: List[Tuple[str, np.ndarray]] = [
        (s.id, decode_embedding(s.embedding))
        for s in channel_stars
        if (s.embedding is not None
            and s.id != star.id
            and s.star_type == STAR_TYPE_PRIMARY)
    ]

    similar = find_similar(vec, primary_embeddings, threshold=CONVERGENCE_THRESHOLD)
    if len(similar) < CONVERGENCE_MIN_PATHS:
        return None

    # Group by constellation membership to count distinct "routes"
    constellations = db.get_all_constellations(channel=channel, owner=owner)
    star_to_source: Dict[str, str] = {}
    for c in constellations:
        for sid in c.star_ids:
            star_to_source[sid] = c.id

    # The new star itself is also a distinct source
    distinct_sources: Dict[str, List[str]] = {}
    new_star_source = star_to_source.get(star.id, f"isolated_{star.id}")
    distinct_sources[new_star_source] = [star.id]

    for sid, sim in similar:
        source = star_to_source.get(sid, f"isolated_{sid}")
        distinct_sources.setdefault(source, []).append(sid)

    if len(distinct_sources) < CONVERGENCE_MIN_PATHS:
        return None

    # Check we haven't already created a convergence star for this meaning
    existing_convergences = db.get_convergence_stars(channel=channel, owner=owner)
    for conv in existing_convergences:
        if conv.embedding:
            conv_vec = decode_embedding(conv.embedding)
            if cosine_similarity(vec, conv_vec) >= 0.95:
                # Already have a convergence coordinate here — activate it
                db.activate_star(conv.id)
                conv.activation_count += 1
                _update_convergence_mass(conv.id)
                return conv

    # === CONVERGENCE EVENT ===
    # Build content label from the converging meaning
    content_label = star.content[:200]
    convergence_content = f"{CONVERGENCE_PREFIX} {content_label}"

    # Create the convergence star
    convergence_star = Star(
        content=convergence_content,
        embedding=encode_embedding(vec),
        mass=MIN_MASS,
        activation_count=1,
        last_activated=time.time(),
        star_type=STAR_TYPE_CONVERGENCE,
        channel=channel,
        owner=owner,
    )
    convergence_star.gravity_score = compute_star_gravity(convergence_star)
    convergence_star.layer = assign_layer(convergence_star.gravity_score)
    convergence_star.distance_to_core = compute_distance_from_mass(convergence_star.mass)

    db.insert_star(convergence_star)
    g.add_star(
        convergence_star.id,
        layer=convergence_star.layer,
        gravity=convergence_star.gravity_score,
        mass=convergence_star.mass,
        star_type=STAR_TYPE_CONVERGENCE,
        channel=channel,
        owner=owner,
    )

    # Link the convergence star to ALL converging primary stars
    all_converging_ids = [star.id] + [sid for sid, _ in similar]
    for sid in all_converging_ids:
        primary = db.get_star(sid)
        if primary and primary.embedding:
            primary_vec = decode_embedding(primary.embedding)
            sim = cosine_similarity(vec, primary_vec)
            g.link_stars(convergence_star.id, sid, sim)
        # Activate each contributing primary
        db.activate_star(sid)

    # Recalculate convergence star mass (it now has connections)
    _update_convergence_mass(convergence_star.id)

    logger.info(
        "CONVERGENCE: created coordinate %s with %d routes from %d sources",
        convergence_star.id[:8], len(all_converging_ids), len(distinct_sources),
    )

    # Emit convergence event to Universal Bus
    try:
        from core.operator.universal_bus import get_universal_bus
        from core.operator.bus_reactor import LiveChannels, EventTypes
        get_universal_bus().emit(
            channel=LiveChannels.CONVERGENCE,
            event_type=EventTypes.CONVERGENCE_CREATED,
            source_layer=5,
            payload={
                "star_id": convergence_star.id,
                "content": convergence_content[:120],
                "route_count": len(all_converging_ids),
                "source_count": len(distinct_sources),
                "channel": channel,
                "owner": owner,
            },
        )
    except Exception:
        pass  # bus hook is non-fatal

    return convergence_star


def _update_convergence_mass(star_id: str) -> None:
    """Recalculate mass for a convergence star from its graph connections."""
    from vectrax.cognitive_gravity import update_mass
    try:
        update_mass(star_id)
    except Exception:
        pass  # non-fatal


def _update_collective_mass(star_id: str) -> None:
    """
    Recalculate mass for a COLLECTIVE convergence star.

    Unlike standard mass, collective gravity is amplified by the number
    of distinct owners contributing to this convergence point.
    More users = stronger gravitational pull = closer to nucleus.
    """
    try:
        star = db.get_star(star_id)
        if not star:
            return

        # Count distinct owners from neighbours
        neighbors = g.get_neighbors(star_id)
        in_degree = len(neighbors)

        contributing_owners: set = set()
        coherence_sum = 0.0
        valid = 0
        star_vec = decode_embedding(star.embedding) if star.embedding else None

        for nbr_id, _ in neighbors:
            nbr = db.get_star(nbr_id)
            if nbr:
                if nbr.owner and nbr.owner != COLLECTIVE_OWNER:
                    contributing_owners.add(nbr.owner)
                if star_vec is not None and nbr.embedding:
                    nbr_vec = decode_embedding(nbr.embedding)
                    coherence_sum += cosine_similarity(star_vec, nbr_vec)
                    valid += 1

        neighbor_coherence = coherence_sum / valid if valid > 0 else 0.0
        owner_count = max(len(contributing_owners), 1)

        new_mass = compute_collective_gravity(
            star,
            in_degree=in_degree,
            neighbor_coherence=neighbor_coherence,
            owner_count=owner_count,
        )
        new_distance = compute_distance_from_mass(new_mass)

        star.mass = new_mass
        star.distance_to_core = new_distance
        star.layer = assign_layer(star.gravity_score)
        db.update_star(star)
        g.update_star_attrs(star_id, mass=new_mass, distance=new_distance)

        logger.debug(
            "COLLECTIVE MASS: %s → mass=%.4f dist=%.4f (%d owners)",
            star_id[:8], new_mass, new_distance, owner_count,
        )
    except Exception:
        pass  # non-fatal


# ---------------------------------------------------------------------------
# 1b. Cross-user (collective) convergence detection
# ---------------------------------------------------------------------------

def auto_check_cross_user_convergence(
    star: Star,
    vec: np.ndarray,
    channel: str,
    owner: str,
) -> Optional[Star]:
    """
    Called automatically after every primary star is ingested.

    Detects convergence ACROSS different users within the same channel:
      1. Find primary stars from OTHER owners that are semantically similar
      2. Group by owner to count distinct users
      3. If ≥ CROSS_USER_MIN_OWNERS distinct owners converge on the same
         meaning, create a collective convergence star (owner=__collective__)
         and link it to all contributing primaries

    Cross-channel convergence is NEVER allowed.
    Returns the collective convergence star if created, None otherwise.
    """
    db.init_db()

    if star.star_type == STAR_TYPE_CONVERGENCE:
        return None

    # Find similar PRIMARY stars from ALL owners in this channel (not just ours)
    all_channel_stars = db.get_all_stars(channel=channel)
    other_owner_embeddings: List[Tuple[str, np.ndarray]] = [
        (s.id, decode_embedding(s.embedding))
        for s in all_channel_stars
        if (s.embedding is not None
            and s.id != star.id
            and s.star_type == STAR_TYPE_PRIMARY
            and s.owner != owner)  # ONLY other users
    ]

    if not other_owner_embeddings:
        return None

    similar = find_similar(
        vec, other_owner_embeddings, threshold=CROSS_USER_CONVERGENCE_THRESHOLD,
    )
    if not similar:
        return None

    # Group by owner to count distinct users
    star_map = {s.id: s for s in all_channel_stars}
    owners_contributing: Dict[str, List[str]] = {owner: [star.id]}  # self
    for sid, sim in similar:
        s = star_map.get(sid)
        if s:
            owners_contributing.setdefault(s.owner, []).append(sid)

    if len(owners_contributing) < CROSS_USER_MIN_OWNERS:
        return None

    # Dedup: check if we already have a collective convergence for this meaning
    existing_collective = db.get_collective_convergence_stars(channel=channel)
    for conv in existing_collective:
        if conv.embedding:
            conv_vec = decode_embedding(conv.embedding)
            if cosine_similarity(vec, conv_vec) >= 0.95:
                # Already exists — activate and strengthen
                db.activate_star(conv.id)
                # Also link the new star if not already linked
                if not g.get_graph().has_edge(conv.id, star.id):
                    g.link_stars(conv.id, star.id, cosine_similarity(
                        vec, conv_vec,
                    ))
                _update_collective_mass(conv.id)
                logger.info(
                    "COLLECTIVE: strengthened coordinate %s (now %d owners)",
                    conv.id[:8], len(owners_contributing),
                )
                return conv

    # === COLLECTIVE CONVERGENCE EVENT ===
    owner_list = sorted(owners_contributing.keys())
    content_label = star.content[:150]
    collective_content = (
        f"{COLLECTIVE_PREFIX} {content_label} "
        f"(users: {', '.join(owner_list[:5])})"
    )

    collective_star = Star(
        content=collective_content,
        embedding=encode_embedding(vec),
        mass=MIN_MASS,
        activation_count=len(owners_contributing),
        last_activated=time.time(),
        star_type=STAR_TYPE_CONVERGENCE,
        channel=channel,
        owner=COLLECTIVE_OWNER,
    )
    collective_star.gravity_score = compute_star_gravity(collective_star)
    collective_star.layer = assign_layer(collective_star.gravity_score)
    collective_star.distance_to_core = compute_distance_from_mass(collective_star.mass)

    db.insert_star(collective_star)
    g.add_star(
        collective_star.id,
        layer=collective_star.layer,
        gravity=collective_star.gravity_score,
        mass=collective_star.mass,
        star_type=STAR_TYPE_CONVERGENCE,
        channel=channel,
        owner=COLLECTIVE_OWNER,
    )

    # Link to ALL contributing primary stars across owners
    all_contributing = [star.id] + [sid for sid, _ in similar]
    for sid in all_contributing:
        primary = db.get_star(sid)
        if primary and primary.embedding:
            primary_vec = decode_embedding(primary.embedding)
            sim = cosine_similarity(vec, primary_vec)
            g.link_stars(collective_star.id, sid, sim)
        db.activate_star(sid)

    _update_collective_mass(collective_star.id)

    logger.info(
        "COLLECTIVE: created coordinate %s — %d users, %d stars converged",
        collective_star.id[:8], len(owners_contributing), len(all_contributing),
    )

    # Emit collective convergence event to Universal Bus
    try:
        from core.operator.universal_bus import get_universal_bus
        from core.operator.bus_reactor import LiveChannels, EventTypes
        get_universal_bus().emit(
            channel=LiveChannels.CONVERGENCE,
            event_type=EventTypes.CONVERGENCE_COLLECTIVE,
            source_layer=5,
            payload={
                "star_id": collective_star.id,
                "content": collective_content[:120],
                "owner_count": len(owners_contributing),
                "star_count": len(all_contributing),
                "channel": channel,
            },
        )
    except Exception:
        pass  # bus hook is non-fatal

    return collective_star


# ---------------------------------------------------------------------------
# 2. List convergence coordinates
# ---------------------------------------------------------------------------

def list_coordinates(
    channel: str = CHANNEL_USER,
    owner: str = "",
) -> List[Dict]:
    """
    Return all convergence stars as navigational coordinates,
    ordered by distance to the nucleus (closest first).

    Each coordinate includes:
      - id, content, mass, distance_to_core, layer
      - connected_count: how many primary stars it links to
      - activation_count: how many times this coordinate was confirmed
    """
    validate_channel(channel)
    db.init_db()

    coordinates = db.get_convergence_stars(channel=channel, owner=owner)
    result: List[Dict] = []

    for coord in coordinates:
        neighbors = g.get_neighbors(coord.id)
        result.append({
            "id": coord.id,
            "content": coord.content.replace(CONVERGENCE_PREFIX, "").strip(),
            "mass": round(coord.mass, 6),
            "distance_to_core": round(coord.distance_to_core, 6),
            "layer": coord.layer,
            "activation_count": coord.activation_count,
            "connected_stars": len(neighbors),
            "gravity_score": round(coord.gravity_score, 4),
        })

    return result


def list_collective_coordinates(
    channel: str = CHANNEL_USER,
) -> List[Dict]:
    """
    Return all COLLECTIVE convergence stars (cross-user) ordered by
    distance to the nucleus.

    These are coordinates where multiple distinct users independently
    arrived at the same meaning.
    """
    validate_channel(channel)
    db.init_db()

    coordinates = db.get_collective_convergence_stars(channel=channel)
    result: List[Dict] = []

    for coord in coordinates:
        neighbors = g.get_neighbors(coord.id)
        # Determine contributing owners
        contributing_owners: set = set()
        for nid, _ in neighbors:
            nbr = db.get_star(nid)
            if nbr and nbr.owner and nbr.owner != COLLECTIVE_OWNER:
                contributing_owners.add(nbr.owner)

        result.append({
            "id": coord.id,
            "content": coord.content
                .replace(COLLECTIVE_PREFIX, "")
                .replace(CONVERGENCE_PREFIX, "")
                .strip(),
            "mass": round(coord.mass, 6),
            "distance_to_core": round(coord.distance_to_core, 6),
            "layer": coord.layer,
            "activation_count": coord.activation_count,
            "connected_stars": len(neighbors),
            "contributing_owners": sorted(contributing_owners),
            "owner_count": len(contributing_owners),
            "gravity_score": round(coord.gravity_score, 4),
        })

    return result


# ---------------------------------------------------------------------------
# 3. Find nearest convergence coordinate to a query
# ---------------------------------------------------------------------------

def find_nearest_coordinate(
    text: str,
    channel: str = CHANNEL_USER,
    owner: str = "",
    limit: int = 5,
) -> List[Dict]:
    """
    Given a text query, find the nearest convergence coordinates.

    Returns up to `limit` convergence stars ranked by semantic similarity
    to the query, each with its distance_to_core.
    """
    validate_channel(channel)
    db.init_db()

    query_vec = embed(text)
    coordinates = db.get_convergence_stars(channel=channel, owner=owner)

    if not coordinates:
        return []

    coord_embeddings: List[Tuple[str, np.ndarray]] = [
        (c.id, decode_embedding(c.embedding))
        for c in coordinates
        if c.embedding is not None
    ]

    similar = find_similar(query_vec, coord_embeddings, threshold=0.3)
    coord_map = {c.id: c for c in coordinates}

    results: List[Dict] = []
    for cid, sim in similar[:limit]:
        coord = coord_map[cid]
        results.append({
            "coordinate_id": cid,
            "content": coord.content.replace(CONVERGENCE_PREFIX, "").strip(),
            "similarity": round(sim, 6),
            "distance_to_core": round(coord.distance_to_core, 6),
            "mass": round(coord.mass, 6),
            "layer": coord.layer,
        })

    return results


# ---------------------------------------------------------------------------
# 4. Get knowledge around a convergence coordinate
# ---------------------------------------------------------------------------

def get_knowledge_around(
    coordinate_id: str,
    radius: float = COORDINATE_SEARCH_RADIUS,
    limit: int = 50,
) -> Dict:
    """
    Retrieve all knowledge organised around a convergence coordinate.

    Returns:
      - The coordinate itself
      - Directly connected primary stars (graph neighbours)
      - Semantically nearby stars within the radius
      - All results ordered by distance to the nucleus
    """
    db.init_db()

    coord = db.get_star(coordinate_id)
    if not coord or coord.star_type != STAR_TYPE_CONVERGENCE:
        raise ValueError(f"Not a convergence coordinate: {coordinate_id}")

    coord_vec = decode_embedding(coord.embedding) if coord.embedding else None

    # 1. Directly connected stars (graph neighbours)
    neighbors = g.get_neighbors(coordinate_id)
    connected_ids = {nid for nid, _ in neighbors}

    # 2. Semantically nearby (within radius), if we have the coordinate's embedding
    #    For collective coordinates, search ALL owners in the channel
    nearby_ids: set = set()
    if coord_vec is not None:
        if coord.owner == COLLECTIVE_OWNER:
            all_stars = db.get_all_stars(channel=coord.channel)
        else:
            all_stars = db.get_all_stars(channel=coord.channel, owner=coord.owner)
        for s in all_stars:
            if s.id != coordinate_id and s.embedding:
                s_vec = decode_embedding(s.embedding)
                if cosine_similarity(coord_vec, s_vec) >= radius:
                    nearby_ids.add(s.id)

    # Merge all relevant star IDs
    all_relevant_ids = connected_ids | nearby_ids
    stars: List[Dict] = []
    for sid in all_relevant_ids:
        s = db.get_star(sid)
        if s:
            stars.append({
                "id": s.id,
                "content": s.content,
                "star_type": s.star_type,
                "owner": s.owner,
                "mass": round(s.mass, 6),
                "distance_to_core": round(s.distance_to_core, 6),
                "layer": s.layer,
                "activation_count": s.activation_count,
                "directly_connected": sid in connected_ids,
                "semantically_nearby": sid in nearby_ids,
            })

    # Order by distance to nucleus (closest first)
    stars.sort(key=lambda x: x["distance_to_core"])

    # Determine contributing owners for collective coordinates
    is_collective = coord.owner == COLLECTIVE_OWNER
    contributing_owners = sorted({
        s["owner"] for s in stars
        if s["owner"] and s["owner"] != COLLECTIVE_OWNER
    }) if is_collective else []

    return {
        "coordinate": {
            "id": coord.id,
            "content": coord.content
                .replace(COLLECTIVE_PREFIX, "")
                .replace(CONVERGENCE_PREFIX, "")
                .strip(),
            "mass": round(coord.mass, 6),
            "distance_to_core": round(coord.distance_to_core, 6),
            "layer": coord.layer,
            "activation_count": coord.activation_count,
            "is_collective": is_collective,
            "contributing_owners": contributing_owners,
        },
        "stars": stars[:limit],
        "total_connected": len(connected_ids),
        "total_nearby": len(nearby_ids),
        "total_unique": len(all_relevant_ids),
    }


# ---------------------------------------------------------------------------
# 5. Navigate by coordinates (full knowledge retrieval pipeline)
# ---------------------------------------------------------------------------

def navigate_by_coordinates(
    text: str,
    channel: str = CHANNEL_USER,
    owner: str = "",
    max_coordinates: int = 3,
    stars_per_coordinate: int = 10,
) -> Dict:
    """
    Given a text query, navigate the knowledge universe using convergence
    coordinates as landmarks.

    Pipeline:
      1. Find nearest convergence coordinates to the query
      2. For each coordinate, retrieve connected/nearby knowledge
      3. Merge and deduplicate results
      4. Order everything by distance to the nucleus

    Returns a structured navigation result with coordinates and knowledge.
    """
    validate_channel(channel)
    db.init_db()

    # Step 1: Find nearest coordinates
    nearest = find_nearest_coordinate(
        text, channel=channel, owner=owner, limit=max_coordinates,
    )

    if not nearest:
        # No convergence coordinates yet — fall back to direct similarity search
        return _fallback_search(text, channel, owner, limit=stars_per_coordinate)

    # Step 2: Gather knowledge around each coordinate
    coordinate_results: List[Dict] = []
    all_star_ids: set = set()
    merged_stars: List[Dict] = []

    for coord_info in nearest:
        try:
            knowledge = get_knowledge_around(
                coord_info["coordinate_id"],
                limit=stars_per_coordinate,
            )
            coordinate_results.append({
                "coordinate": knowledge["coordinate"],
                "similarity_to_query": coord_info["similarity"],
                "stars_found": len(knowledge["stars"]),
            })
            for s in knowledge["stars"]:
                if s["id"] not in all_star_ids:
                    all_star_ids.add(s["id"])
                    merged_stars.append(s)
        except (ValueError, Exception) as exc:
            logger.warning("Could not navigate coordinate %s: %s",
                           coord_info.get("coordinate_id"), exc)

    # Step 3: Order by distance to nucleus
    merged_stars.sort(key=lambda x: x["distance_to_core"])

    return {
        "query": text,
        "coordinates_used": len(coordinate_results),
        "coordinates": coordinate_results,
        "knowledge": merged_stars,
        "total_stars": len(merged_stars),
    }


def _fallback_search(
    text: str,
    channel: str,
    owner: str,
    limit: int = 10,
) -> Dict:
    """Direct similarity search when no convergence coordinates exist yet."""
    vec = embed(text)
    all_stars = db.get_all_stars(channel=channel, owner=owner)
    star_embeddings = [
        (s.id, decode_embedding(s.embedding))
        for s in all_stars
        if s.embedding is not None
    ]
    similar = find_similar(vec, star_embeddings, threshold=0.5)
    star_map = {s.id: s for s in all_stars}

    results: List[Dict] = []
    for sid, sim in similar[:limit]:
        s = star_map.get(sid)
        if s:
            results.append({
                "id": s.id,
                "content": s.content,
                "star_type": s.star_type,
                "mass": round(s.mass, 6),
                "distance_to_core": round(s.distance_to_core, 6),
                "layer": s.layer,
                "activation_count": s.activation_count,
                "similarity": round(sim, 6),
            })

    results.sort(key=lambda x: x["distance_to_core"])

    return {
        "query": text,
        "coordinates_used": 0,
        "coordinates": [],
        "knowledge": results,
        "total_stars": len(results),
        "fallback": True,
    }
