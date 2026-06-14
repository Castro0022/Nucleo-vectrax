"""
Vectrax Engine — orchestrates the full pipeline.

v2 (gravitational):
  ingest_v2(text, user_id, topic) →
    1. Embed the text
    2. Insert as Pattern linked to user_id
    3. Get or create the UserStar
    4. Recalculate star centroid (mean of pattern embeddings)
    5. Recalculate mass (activity × diversity × convergence)
    6. Recalculate distance to core
    7. Detect convergence with other user-stars
    8. Update nucleus centroid

Legacy ingest() is preserved for backward compatibility.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

import json as _json
import logging

from vectrax import db, graph as g
from vectrax.embeddings import (
    cluster_coherence,
    decode_embedding,
    encode_embedding,
    embed,
    find_similar,
    mean_embedding,
)
from vectrax.models import STAR_TYPE_PRIMARY
from vectrax.gravity import (
    assign_layer,
    compute_constellation_gravity,
    compute_distance_from_mass,
    compute_star_gravity,
    compute_user_star_mass,
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
    CREATOR_CENTROID_WEIGHT,
    PROPOSAL_GRAVITY_THRESHOLD,
    PROPOSAL_MIN_MEMBERS,
    ROLE_CREATOR,
    ROLE_USER,
    SIMILARITY_THRESHOLD,
    STAR_TYPE_CONVERGENCE,
    Constellation,
    Pattern,
    Proposal,
    Star,
    UserStar,
)

logger = logging.getLogger("vectrax.engine")

# Max patterns used to compute centroid (recent window)
_CENTROID_WINDOW = 200


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


# ═══════════════════════════════════════════════════════════════════════════
# v2 GRAVITATIONAL INGEST — 1 star per user
# ═══════════════════════════════════════════════════════════════════════════

def ingest_v2(
    text: str,
    user_id: str,
    topic: str = "general",
    is_creator: bool = False,
) -> UserStar:
    """
    Gravitational ingest: feed a user's star with a new pattern.

    Pipeline:
      1. Embed the text
      2. Insert as Pattern
      3. Get or create the UserStar
      4. Recalculate centroid (mean of recent pattern embeddings)
      5. Recalculate mass (activity × diversity × convergence)
      6. Recalculate distance to core + layer
      7. Detect convergence with other user-stars
      8. Update nucleus centroid

    Returns the updated UserStar.
    """
    db.init_db()
    t0 = time.time()

    # ── 1. Evaluate memory value ───────────────────────────────
    from vectrax.memory_evaluator import evaluate_memory_value
    from vectrax.models import PATTERN_DISCARDED, PATTERN_STORED
    mem_status, mem_score = evaluate_memory_value(text, user_id)

    # Discarded patterns still activate the star but don't get embedded
    if mem_status == PATTERN_DISCARDED:
        # Just activate the star (track engagement) without embedding
        existing = db.get_user_star(user_id)
        if existing:
            db.activate_user_star(user_id)
        logger.debug("ingest_v2: DISCARDED (score=%.2f) | %s", mem_score, text[:40])
        return existing or UserStar(user_id=user_id)

    # ── 2. Embed ─────────────────────────────────────────────────
    vec = embed(text)

    # ── 3. Get or create UserStar ──────────────────────────────────
    star = db.get_user_star(user_id)
    if star is None:
        role = ROLE_CREATOR if is_creator else ROLE_USER
        star = UserStar(
            user_id=user_id,
            role=role,
            created_at=time.time(),
        )
        logger.info("New star born: %s (role=%s)", user_id[:20], role)

    # ── 3.5 LEARNING GATE — does this transform the universe? ───
    # Evaluates novelty, coherence, impact BEFORE deciding whether
    # to actively incorporate into the gravitational universe.
    _learning_active = True
    _learning_reason = "default"
    try:
        from core.learn.learning_gate import evaluate as _lg_evaluate
        # Gather existing state for evaluation
        _centroid = decode_embedding(star.embedding) if star.embedding else None
        _existing_patterns = db.get_patterns_for_user(
            user_id, limit=20, status=PATTERN_STORED,
        )
        _existing_vecs = [
            decode_embedding(p.embedding)
            for p in _existing_patterns
            if p.embedding
        ]
        _conv_links = len(g.get_neighbors(user_id)) if g.get_graph().has_node(user_id) else 0
        _topic_dist = db.get_topic_distribution(user_id)

        _lg_decision = _lg_evaluate(
            embedding=vec,
            user_id=user_id,
            centroid_embedding=_centroid,
            existing_embeddings=_existing_vecs,
            current_mass=star.mass,
            pattern_count=star.pattern_count,
            topic_diversity=len(_topic_dist),
            convergence_links=_conv_links,
            is_creator=is_creator,
        )
        _learning_active = _lg_decision.is_active
        _learning_reason = _lg_decision.reason
    except Exception as _lg_exc:
        logger.debug("Learning gate failed (passthrough): %s", _lg_exc)

    # ── 4. Insert pattern ────────────────────────────────────────
    # Always stored — but learning_type determines if it changes the star
    pattern = Pattern(
        user_id=user_id,
        content=text,
        embedding=encode_embedding(vec),
        topic=topic,
        status=mem_status,
        value_score=mem_score,
    )
    db.insert_pattern(pattern)

    star.pattern_count = db.get_pattern_count(user_id)
    star.last_active = time.time()
    star.activation_count += 1

    # ── PASSIVE PATH: record but don't transform gravitational state ──
    if not _learning_active:
        db.upsert_user_star(star)
        g.add_star(user_id, layer=star.layer, mass=star.mass)
        elapsed = time.time() - t0
        logger.info(
            "ingest_v2: %s | PASSIVE(%s) | mass=%.4f | layer=%s | patterns=%d | %.0fms",
            user_id[:20], _learning_reason,
            star.mass, star.layer, star.pattern_count, elapsed * 1000,
        )
        return star

    # ── ACTIVE PATH: full gravitational transformation ─────────────

    # ── 5. Recalculate centroid (STORED patterns only) ────────
    patterns = db.get_patterns_for_user(
        user_id, limit=_CENTROID_WINDOW, status=PATTERN_STORED,
    )
    pattern_vecs = [
        decode_embedding(p.embedding)
        for p in patterns
        if p.embedding is not None
    ]
    if pattern_vecs:
        centroid = mean_embedding(pattern_vecs)
        star.embedding = encode_embedding(centroid)

    # ── 6. Recalculate mass ───────────────────────────────────────
    topic_dist = db.get_topic_distribution(user_id)
    star.topic_fingerprint = _json.dumps(topic_dist, ensure_ascii=False)
    convergence_links = len(g.get_neighbors(user_id)) if g.get_graph().has_node(user_id) else 0

    star.mass = compute_user_star_mass(
        pattern_count=star.pattern_count,
        topic_diversity=len(topic_dist),
        convergence_connections=convergence_links,
        is_creator=star.is_creator,
    )

    # ── 7. Distance + layer ───────────────────────────────────────
    star.distance_to_core = compute_distance_from_mass(star.mass)
    star.layer = assign_layer(star.mass)

    # ── 8. Persist star ─────────────────────────────────────────
    db.upsert_user_star(star)

    # ── 9. Graph: ensure node exists ──────────────────────────────
    g.add_star(user_id, layer=star.layer, mass=star.mass)

    # ── 9b. Feed Word Gravity Index ─────────────────────────
    try:
        from core.word_gravity import extract_keywords, upsert_word
        _wgi_keywords = extract_keywords(text)
        _wgi_delta = max(0.02, mem_score * 0.15)
        _wgi_scope = user_id
        for _kw in _wgi_keywords:
            upsert_word(
                _kw, scope=_wgi_scope, delta=_wgi_delta,
                category="concept",
                constellation_id=pattern.id if hasattr(pattern, 'id') else None,
            )
    except Exception as _wgi_exc:
        logger.debug("WGI feed failed: %s", _wgi_exc)

    # ── 10. Convergence with other user-stars ──────────────────
    _detect_user_convergence(star, user_id)

    # ── 11. Update nucleus ──────────────────────────────────────
    try:
        from vectrax.core_nucleus import refresh_centroid_v2
        refresh_centroid_v2()
    except Exception:
        pass

    elapsed = time.time() - t0
    logger.info(
        "ingest_v2: %s | ACTIVE(%s) | mass=%.4f | dist=%.4f | layer=%s | patterns=%d | %.0fms",
        user_id[:20], _learning_reason,
        star.mass, star.distance_to_core,
        star.layer, star.pattern_count, elapsed * 1000,
    )
    return star


def _detect_user_convergence(star: UserStar, user_id: str) -> None:
    """Check if this user-star converges with any other user-star."""
    if star.embedding is None:
        return

    from vectrax.embeddings import cosine_similarity

    my_vec = decode_embedding(star.embedding)
    all_stars = db.get_all_user_stars()

    for other in all_stars:
        if other.user_id == user_id or other.embedding is None:
            continue
        other_vec = decode_embedding(other.embedding)
        sim = cosine_similarity(my_vec, other_vec)
        if sim >= SIMILARITY_THRESHOLD:
            # Convergence! Link in graph
            if not g.get_graph().has_edge(user_id, other.user_id):
                g.link_stars(user_id, other.user_id, sim)
                logger.info(
                    "CONVERGENCE: %s ↔ %s (sim=%.3f)",
                    user_id[:15], other.user_id[:15], sim,
                )
