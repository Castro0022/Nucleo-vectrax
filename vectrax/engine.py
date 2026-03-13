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
    CONSTELLATION_MIN_STARS,
    PROPOSAL_GRAVITY_THRESHOLD,
    PROPOSAL_MIN_MEMBERS,
    SIMILARITY_THRESHOLD,
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
    Link the star to similar neighbours WITHIN ITS OWN CHANNEL ONLY,
    then detect patterns for that channel.
    """
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

    detect_patterns(channel=channel, owner=owner)


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
