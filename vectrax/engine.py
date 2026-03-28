"""
Vectrax Engine — orchestrates the full pipeline:

  ingest(text, success) →
    1. Embed the event
    2. Find similar existing stars
    3. Create/update the Star record
    4. Link to similar stars in the graph
    5. Detect/update constellations in dense components
    6. Recompute gravity scores
    7. Check if any constellation qualifies for a structural proposal
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

from vectrax import db, graph as g
from vectrax.embeddings import (
    cluster_coherence,
    decode_embedding,
    encode_embedding,
    embed,
    find_similar,
)
from vectrax.models import STAR_TYPE_PRIMARY
from vectrax.gravity import (
    assign_layer,
    compute_constellation_gravity,
    compute_star_gravity,
)
from vectrax.identity import (
    CHANNEL_CREATOR,
    CHANNEL_USER,
    CREATOR_OWNER,
    assert_no_cross_channel_link,
    validate_channel,
    validate_creator_ownership,
)
from vectrax.models import (
    COLLECTIVE_CONSTELLATION_MIN_MASS,
    COLLECTIVE_CONSTELLATION_MIN_STARS,
    COLLECTIVE_OWNER,
    CONSTELLATION_MIN_STARS,
    PROPOSAL_GRAVITY_THRESHOLD,
    PROPOSAL_MIN_MEMBERS,
    SIMILARITY_THRESHOLD,
    STAR_TYPE_CONVERGENCE,
    Constellation,
    Proposal,
    Star,
)


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def ingest(
    text: str,
    success: bool = False,
    channel: str = CHANNEL_USER,
    owner: str = "",
) -> Star:
    """
    Capture a new event as a star.

    Channel separation is enforced:
      - Creator channel: only 'mario' may write here
      - User channel: scoped to the given owner
      - Cross-channel similarity links are NEVER created

    Near-duplicate check is performed only within the same channel + owner.
    """
    validate_channel(channel)
    if channel == CHANNEL_CREATOR:
        validate_creator_ownership(owner)

    db.init_db()
    vec = embed(text)

    # Check for near-duplicate WITHIN the same channel and owner only
    channel_stars = db.get_all_stars(channel=channel, owner=owner)
    existing_embeddings: List[Tuple[str, object]] = [
        (s.id, decode_embedding(s.embedding))
        for s in channel_stars
        if s.embedding is not None
    ]
    near_dupes = find_similar(vec, existing_embeddings, threshold=0.95)

    if near_dupes:
        top_id, _ = near_dupes[0]
        star = db.get_star(top_id)
        star.repetition_count += 1
        star.total_count += 1
        if success:
            star.success_count += 1
        # Creator stars never leave the core
        if channel == CHANNEL_CREATOR:
            star.gravity_score = 1.0
        else:
            star.gravity_score = compute_star_gravity(star)
        star.layer = assign_layer(star.gravity_score)
        db.update_star(star)
        _post_ingest(star, vec, channel=channel, owner=owner)
        return star

    # Create new star
    star = Star(
        content=text,
        embedding=encode_embedding(vec),
        success_count=1 if success else 0,
        total_count=1,
        repetition_count=1,
        channel=channel,
        owner=owner,
    )
    if channel == CHANNEL_CREATOR:
        star.gravity_score = 1.0  # foundational — always maximum gravity
    else:
        star.gravity_score = compute_star_gravity(star)
    star.layer = assign_layer(star.gravity_score)
    db.insert_star(star)
    g.add_star(star.id, layer=star.layer, gravity=star.gravity_score,
               channel=channel, owner=owner)
    _post_ingest(star, vec, channel=channel, owner=owner)
    return star


def _post_ingest(star: Star, vec, channel: str, owner: str) -> None:
    """
    Post-ingest pipeline:
      1. Emit star event to the Universal Bus (live circuit)
      2. Link the star to similar neighbours WITHIN ITS OWN CHANNEL ONLY
      3. Record trajectory point (user's path through the knowledge graph)
      4. Auto-detect convergence (create convergence coordinates)
      5. Detect constellation patterns
    """
    # --- 0. Emit star event to bus ---
    try:
        from core.operator.universal_bus import get_universal_bus
        from core.operator.bus_reactor import LiveChannels, EventTypes
        _bus = get_universal_bus()
        _event_type = (
            EventTypes.STAR_UPDATED
            if star.repetition_count > 1
            else EventTypes.STAR_CREATED
        )
        _bus.emit(
            channel=LiveChannels.STARS,
            event_type=_event_type,
            source_layer=3,
            payload={
                "id": star.id,
                "content": star.content[:120],
                "gravity_score": star.gravity_score,
                "layer": star.layer,
                "repetition_count": star.repetition_count,
                "channel": channel,
                "owner": owner,
            },
        )
    except Exception:
        pass  # bus hook is non-fatal
    # --- 1. Link to similar neighbours ---
    channel_stars = db.get_all_stars(channel=channel, owner=owner)
    existing_embeddings = [
        (s.id, decode_embedding(s.embedding))
        for s in channel_stars
        if s.embedding is not None and s.id != star.id
    ]
    similar = find_similar(vec, existing_embeddings, threshold=SIMILARITY_THRESHOLD)
    for other_id, sim in similar:
        # Double-check: only link stars in the same channel (defence in depth)
        other = db.get_star(other_id)
        if other and other.channel == channel:
            g.link_stars(star.id, other_id, sim)

    # --- 2. Record trajectory point ---
    try:
        from vectrax.trajectory import record_point
        record_point(star.id, channel, owner)
    except Exception:
        pass  # trajectory recording is non-fatal

    # --- 3. Auto-detect convergence ---
    if star.star_type == STAR_TYPE_PRIMARY:
        try:
            from vectrax.convergence_engine import auto_check_convergence
            auto_check_convergence(star, vec, channel, owner)
        except Exception:
            pass  # convergence detection is non-fatal

        # --- 3b. Cross-user (collective) convergence ---
        try:
            from vectrax.convergence_engine import auto_check_cross_user_convergence
            auto_check_cross_user_convergence(star, vec, channel, owner)
        except Exception:
            pass  # collective convergence detection is non-fatal

    # --- 4. Detect constellation patterns ---
    detect_patterns(channel=channel, owner=owner)

    # --- 4b. Collective constellation emergence ---
    try:
        detect_collective_patterns(channel=channel)
    except Exception:
        pass  # collective pattern detection is non-fatal


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------

def detect_patterns(
    channel: str = CHANNEL_USER,
    owner: str = "",
) -> List[Constellation]:
    """
    Identify dense connected components WITHIN a single channel and create/update
    Constellation records. Cross-channel components are never formed.
    """
    channel_stars = db.get_all_stars(channel=channel, owner=owner)
    star_map = {s.id: s for s in channel_stars}
    channel_ids = set(star_map.keys())

    dense = g.get_dense_components(min_size=CONSTELLATION_MIN_STARS)
    # Filter: only components where ALL stars belong to this channel
    dense = [
        comp for comp in dense
        if comp.issubset(channel_ids)
    ]

    existing = {
        frozenset(c.star_ids): c
        for c in db.get_all_constellations(channel=channel, owner=owner)
    }

    updated: List[Constellation] = []
    for component in dense:
        key = frozenset(component)

        vecs = [
            decode_embedding(star_map[sid].embedding)
            for sid in component
            if sid in star_map and star_map[sid].embedding
        ]
        coherence = cluster_coherence(vecs) if len(vecs) >= 2 else 1.0

        avg_success = (
            sum(star_map[sid].success_rate for sid in component if sid in star_map)
            / len(component)
        )

        if key in existing:
            c = existing[key]
            c.repetition_count += 1
            c.coherence_score = coherence
            c.success_rate = avg_success
            c.updated_at = time.time()
        else:
            c = Constellation(
                star_ids=list(component),
                coherence_score=coherence,
                success_rate=avg_success,
                channel=channel,
                owner=owner,
            )

        c.gravity_score = compute_constellation_gravity(c)
        db.upsert_constellation(c)
        updated.append(c)

    # Proposals only route to the creator channel
    if channel == CHANNEL_CREATOR:
        check_proposals(updated)
    return updated


# ---------------------------------------------------------------------------
# Collective constellation emergence
# ---------------------------------------------------------------------------

def detect_collective_patterns(
    channel: str = CHANNEL_USER,
) -> List[Constellation]:
    """
    Detect dense components that span multiple owners and contain
    collective convergence stars with sufficient accumulated mass.

    Unlike detect_patterns (scoped to a single owner), this searches
    across ALL owners in the channel. Constellations only emerge when
    the average mass of the component exceeds the collective threshold
    — meaning enough distinct users have independently validated the
    knowledge cluster for it to crystallise into a constellation.

    Created constellations have owner=__collective__.
    """
    db.init_db()

    # Get ALL stars in the channel (cross-owner)
    all_stars = db.get_all_stars(channel=channel)
    star_map = {s.id: s for s in all_stars}
    channel_ids = set(star_map.keys())

    if len(channel_ids) < COLLECTIVE_CONSTELLATION_MIN_STARS:
        return []

    # Find dense components in the full graph that belong to this channel
    dense = g.get_dense_components(min_size=COLLECTIVE_CONSTELLATION_MIN_STARS)
    dense = [comp for comp in dense if comp.issubset(channel_ids)]

    # Filter: only components containing ≥1 collective convergence star
    collective_components = []
    for comp in dense:
        has_collective = any(
            star_map[sid].owner == COLLECTIVE_OWNER
            and star_map[sid].star_type == STAR_TYPE_CONVERGENCE
            for sid in comp
            if sid in star_map
        )
        if not has_collective:
            continue

        # Check mass threshold: avg mass of all stars in the component
        masses = [
            star_map[sid].mass for sid in comp if sid in star_map
        ]
        avg_mass = sum(masses) / len(masses) if masses else 0.0

        if avg_mass >= COLLECTIVE_CONSTELLATION_MIN_MASS:
            collective_components.append(comp)

    if not collective_components:
        return []

    # Dedup against existing collective constellations
    existing = {
        frozenset(c.star_ids): c
        for c in db.get_all_constellations(channel=channel, owner=COLLECTIVE_OWNER)
    }

    updated: List[Constellation] = []
    for component in collective_components:
        key = frozenset(component)

        vecs = [
            decode_embedding(star_map[sid].embedding)
            for sid in component
            if sid in star_map and star_map[sid].embedding
        ]
        coherence = cluster_coherence(vecs) if len(vecs) >= 2 else 1.0

        # Compute avg success across all contributing stars
        avg_success = (
            sum(star_map[sid].success_rate for sid in component if sid in star_map)
            / len(component)
        )

        # Count distinct owners
        owners = {
            star_map[sid].owner for sid in component
            if sid in star_map and star_map[sid].owner != COLLECTIVE_OWNER
        }

        if key in existing:
            c = existing[key]
            c.repetition_count += 1
            c.coherence_score = coherence
            c.success_rate = avg_success
            c.updated_at = time.time()
        else:
            c = Constellation(
                star_ids=list(component),
                coherence_score=coherence,
                success_rate=avg_success,
                channel=channel,
                owner=COLLECTIVE_OWNER,
            )

        c.gravity_score = compute_constellation_gravity(c)
        db.upsert_constellation(c)
        updated.append(c)

    return updated


# ---------------------------------------------------------------------------
# Proposal engine
# ---------------------------------------------------------------------------

def check_proposals(constellations: List[Constellation]) -> List[Proposal]:
    """
    If a constellation crosses the density and gravity thresholds,
    auto-generate a structural improvement proposal (requires creator approval).
    """
    new_proposals: List[Proposal] = []
    pending_ids = {p.constellation_id for p in db.get_proposals(status="pending")}

    for c in constellations:
        if (
            c.member_count >= PROPOSAL_MIN_MEMBERS
            and c.gravity_score >= PROPOSAL_GRAVITY_THRESHOLD
            and c.id not in pending_ids
        ):
            proposal = _build_proposal(c)
            db.insert_proposal(proposal)
            new_proposals.append(proposal)

    return new_proposals


def _build_proposal(c: Constellation) -> Proposal:
    description = (
        f"Constellation [{c.id[:8]}] has reached critical density "
        f"({c.member_count} stars, gravity={c.gravity_score:.3f}, "
        f"coherence={c.coherence_score:.3f}). "
        f"Proposed action: promote this cluster to a named sub-nucleus "
        f"and assign it a dedicated index layer."
    )
    evidence = {
        "constellation_id": c.id,
        "member_count": c.member_count,
        "gravity_score": c.gravity_score,
        "coherence_score": c.coherence_score,
        "success_rate": c.success_rate,
        "repetition_count": c.repetition_count,
        "star_ids": c.star_ids[:10],  # first 10 for brevity
    }
    return Proposal(
        constellation_id=c.id,
        description=description,
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Reorganise
# ---------------------------------------------------------------------------

def reorganize() -> dict:
    """
    Trigger a full gravity recompute and layer reassignment for all
    stars and constellations. Returns a summary of changes.
    """
    from vectrax.gravity import recompute_all
    return recompute_all()
