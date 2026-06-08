"""
core/self_observation/universe_observer.py — Observador unificado del universo.

Fusiona el estado gravitacional (estrellas, convergencias, núcleo) con el
estado operacional (heartbeats, cola, memoria, modos, errores) en un solo
snapshot que representa la percepción completa del universo Vectrax en un
instante dado.

Diseño:
  - Cada fuente es defensiva: si falla, aporta defaults y sigue.
  - Nunca hace red, nunca toca Telegram, nunca manda mensajes.
  - Solo observa, no modifica estado.

API pública:
    observe_universe() -> UniverseSnapshot
    UniverseSnapshot.to_dict() -> dict
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.self_observation.universe")


# ===========================================================================
# Snapshot
# ===========================================================================

@dataclass
class UniverseSnapshot:
    """Snapshot unificado: gravitacional + operacional."""

    timestamp: float = 0.0

    # ── Núcleo ────────────────────────────────────────────────────────────
    nucleus_alive: bool = True
    nucleus_initialised: bool = False
    nucleus_has_centroid: bool = False
    core_star_count: int = 0
    total_mass: float = 0.0

    # ── Estrellas ─────────────────────────────────────────────────────────
    star_count: int = 0          # user_stars
    knowledge_star_count: int = 0  # knowledge stars (tabla stars)
    pattern_count: int = 0
    layers: Dict[str, int] = field(default_factory=dict)
    stars: List[Dict[str, Any]] = field(default_factory=list)

    # ── Convergencias ─────────────────────────────────────────────────────
    convergences: List[Dict[str, Any]] = field(default_factory=list)

    # ── Estado operacional ────────────────────────────────────────────────
    gateway_heartbeat_age_s: Optional[float] = None
    worker_heartbeat_age_s: Optional[float] = None
    worker_alive: bool = False
    queue_pending: int = 0
    queue_processing: int = 0
    queue_done: int = 0
    queue_error: int = 0

    # ── Memoria gravitacional ─────────────────────────────────────────────
    deep_memory_count: int = 0
    deep_memory_active: int = 0
    deep_memory_fused: int = 0
    deep_memory_archived: int = 0
    context_identities_count: int = 0
    essential_memories_count: int = 0

    # ── Modos ─────────────────────────────────────────────────────────────
    audio_mode: str = ""
    sovereignty_mode: str = ""
    proactive_engine_enabled: bool = False

    # ── Salud ─────────────────────────────────────────────────────────────
    recent_error_count_24h: int = 0
    recent_error_lines: List[str] = field(default_factory=list)

    # ── Señales de percepción ─────────────────────────────────────────
    signals: List[str] = field(default_factory=list)

    # ── Gravity Engine (estrellas cognitivas + mercado) ───────────────
    gravity_stars: List[Dict[str, Any]] = field(default_factory=list)
    gravity_domains: Dict[str, Any] = field(default_factory=dict)
    gravity_convergences: List[Dict[str, Any]] = field(default_factory=list)
    gravity_total: int = 0

    # ── Word Gravity Index (WGI) ─────────────────────────────
    word_gravity_words: List[Dict[str, Any]] = field(default_factory=list)
    word_gravity_stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_api_dict(self) -> Dict[str, Any]:
        """Formato optimizado para el endpoint /v1/universe."""
        return {
            "heartbeat": self.timestamp,
            "nucleus": {
                "alive": self.nucleus_alive,
                "initialised": self.nucleus_initialised,
                "has_centroid": self.nucleus_has_centroid,
                "core_star_count": self.core_star_count,
                "total_mass": round(self.total_mass, 4),
            },
            "stars": self.stars,
            "star_count": self.star_count,
            "knowledge_star_count": self.knowledge_star_count,
            "pattern_count": self.pattern_count,
            "layers": self.layers,
            "convergences": self.convergences,
            "operational": {
                "gateway_heartbeat_age_s": (
                    round(self.gateway_heartbeat_age_s, 1)
                    if self.gateway_heartbeat_age_s is not None
                    else None
                ),
                "worker_heartbeat_age_s": (
                    round(self.worker_heartbeat_age_s, 1)
                    if self.worker_heartbeat_age_s is not None
                    else None
                ),
                "worker_alive": self.worker_alive,
                "queue": {
                    "pending": self.queue_pending,
                    "processing": self.queue_processing,
                    "done": self.queue_done,
                    "error": self.queue_error,
                },
                "memory": {
                    "deep_total": self.deep_memory_count,
                    "active": self.deep_memory_active,
                    "fused": self.deep_memory_fused,
                    "archived": self.deep_memory_archived,
                    "identities": self.context_identities_count,
                    "essentials": self.essential_memories_count,
                },
                "modes": {
                    "audio": self.audio_mode,
                    "sovereignty": self.sovereignty_mode,
                    "proactive": self.proactive_engine_enabled,
                },
                "health": {
                    "errors_24h": self.recent_error_count_24h,
                    "recent_errors": self.recent_error_lines[:5],
                },
            },
            "signals": self.signals,
            "gravity": {
                "total": self.gravity_total,
                "stars": self.gravity_stars,
                "domains": self.gravity_domains,
                "convergences": self.gravity_convergences,
            },
            "word_gravity": {
                "words": self.word_gravity_words,
                "stats": self.word_gravity_stats,
            },
        }


# ===========================================================================
# Collectors (cada uno defensivo)
# ===========================================================================

def _collect_gravitational(snap: UniverseSnapshot) -> None:
    """Llena campos gravitacionales: estrellas, núcleo, convergencias."""
    try:
        from vectrax.db import get_universe_status, get_all_user_stars
        universe = get_universe_status()
        snap.star_count = universe.get("stars", 0)
        snap.knowledge_star_count = universe.get("knowledge_stars", 0)
        snap.pattern_count = universe.get("patterns", 0)
        snap.total_mass = universe.get("total_mass", 0.0)
        snap.layers = universe.get("layers", {})

        for s in get_all_user_stars():
            snap.stars.append({
                "user_id": s.user_id,
                "role": s.role,
                "mass": round(s.mass, 4),
                "distance_to_core": round(s.distance_to_core, 4),
                "layer": s.layer,
                "pattern_count": s.pattern_count,
                "activation_count": s.activation_count,
                "last_active": s.last_active,
            })
    except Exception as exc:
        logger.debug("gravitational collection failed: %s", exc)
        snap.signals.append("gravitational_db_unavailable")


def _collect_nucleus(snap: UniverseSnapshot) -> None:
    """Llena campos del núcleo."""
    try:
        from vectrax.core_nucleus import get_core_info
        info = get_core_info()
        snap.nucleus_initialised = info.get("initialised", False)
        snap.nucleus_has_centroid = info.get("has_centroid", False)
        snap.core_star_count = info.get("core_star_count", 0)
    except Exception as exc:
        logger.debug("nucleus collection failed: %s", exc)
        snap.signals.append("nucleus_unavailable")


def _collect_convergences(snap: UniverseSnapshot) -> None:
    """Llena convergencias del grafo."""
    try:
        from vectrax.graph import get_graph
        g = get_graph()
        for u, v, data in g.edges(data=True):
            snap.convergences.append({
                "star_a": u,
                "star_b": v,
                "similarity": round(data.get("weight", 0), 4),
            })
    except Exception as exc:
        logger.debug("convergences collection failed: %s", exc)


def _collect_operational(snap: UniverseSnapshot) -> None:
    """Llena campos operacionales desde state_collector."""
    try:
        from core.self_observation.state_collector import collect_state
        state = collect_state()

        snap.gateway_heartbeat_age_s = state.gateway_heartbeat_age_s
        snap.worker_heartbeat_age_s = state.worker_heartbeat_age_s
        snap.queue_pending = state.queue_pending
        snap.queue_processing = state.queue_processing
        snap.queue_done = state.queue_done
        snap.queue_error = state.queue_error
        snap.deep_memory_count = state.deep_memory_count
        snap.deep_memory_active = state.deep_memory_active
        snap.deep_memory_fused = state.deep_memory_fused
        snap.deep_memory_archived = state.deep_memory_archived
        snap.context_identities_count = state.context_identities_count
        snap.essential_memories_count = state.essential_memories_count
        snap.audio_mode = state.audio_mode
        snap.sovereignty_mode = state.sovereignty_mode
        snap.proactive_engine_enabled = state.proactive_engine_enabled
        snap.recent_error_count_24h = state.recent_error_count_24h
        snap.recent_error_lines = state.recent_error_lines

        # Worker alive heuristic: heartbeat < 30s
        if state.worker_heartbeat_age_s is not None:
            snap.worker_alive = state.worker_heartbeat_age_s < 30.0
    except Exception as exc:
        logger.debug("operational collection failed: %s", exc)
        snap.signals.append("operational_state_unavailable")


def _collect_gravity_engine(snap: UniverseSnapshot) -> None:
    """Collect stars from the gravity engine (cognitive + market domains)."""
    try:
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
        records = gi.all_records()
        snap.gravity_total = len(records)

        # Always include market + user_interest stars, then fill with top by weight
        priority_domains = {"market", "user_interest"}
        priority = [r for r in records if r.domain in priority_domains]
        others = sorted(
            [r for r in records if r.domain not in priority_domains],
            key=lambda r: r.hits * max(r.cc_score, 0.01) * max(r.freq, 0.01) * r.decay_factor,
            reverse=True,
        )
        combined = priority + others[:50 - len(priority)]
        for r in combined:
            snap.gravity_stars.append({
                "id": r.fingerprint,
                "domain": r.domain,
                "intent": r.intent,
                "tier": r.tier,
                "hits": r.hits,
                "cc": round(r.cc_score, 3),
                "freq": round(r.freq, 3),
                "weight": round(
                    r.hits * max(r.cc_score, 0.01) * max(r.freq, 0.01) * r.decay_factor, 2
                ),
                "summary": r.summary[:80] if r.summary else "",
            })

        # Domain stats
        snap.gravity_domains = gi.domain_stats()

        # Cross-domain convergences
        convs = gi.cross_domain_convergences()
        for c in convs[:20]:
            snap.gravity_convergences.append(c)

    except Exception as exc:
        logger.debug("gravity engine collection failed: %s", exc)
        snap.signals.append("gravity_engine_unavailable")


def _collect_word_gravity(snap: UniverseSnapshot) -> None:
    """Collect Word Gravity Index data for the universe visualization."""
    try:
        from core.word_gravity import get_top_words, get_index_stats, SCOPE_GLOBAL
        # Global top words (for the universe view)
        top = get_top_words(scope=SCOPE_GLOBAL, limit=30)
        for word, mass, category in top:
            snap.word_gravity_words.append({
                "word": word,
                "mass": round(mass, 4),
                "category": category,
                "scope": "global",
            })
        snap.word_gravity_stats = get_index_stats()
    except Exception as exc:
        logger.debug("word gravity collection failed: %s", exc)


def _derive_signals(snap: UniverseSnapshot) -> None:
    """Genera señales de percepción a partir del estado recolectado."""
    if snap.queue_pending > 10:
        snap.signals.append("queue_pressure_high")
    elif snap.queue_pending > 3:
        snap.signals.append("queue_pressure_moderate")

    if snap.recent_error_count_24h > 50:
        snap.signals.append("error_rate_critical")
    elif snap.recent_error_count_24h > 10:
        snap.signals.append("error_rate_elevated")

    if not snap.worker_alive and snap.worker_heartbeat_age_s is not None:
        snap.signals.append("worker_unresponsive")

    if snap.gateway_heartbeat_age_s is not None and snap.gateway_heartbeat_age_s > 60:
        snap.signals.append("gateway_stale")

    if snap.star_count == 0:
        snap.signals.append("universe_cold_start")

    if snap.nucleus_has_centroid and snap.total_mass > 0:
        snap.signals.append("nucleus_gravitating")


# ===========================================================================
# Public entrypoint
# ===========================================================================

def observe_universe() -> UniverseSnapshot:
    """Recolecta el snapshot completo del universo Vectrax.

    Cada fuente es defensiva; si falla, aporta defaults y registra una
    señal. Nunca raise.
    """
    snap = UniverseSnapshot(timestamp=time.time())

    _collect_gravitational(snap)
    _collect_nucleus(snap)
    _collect_convergences(snap)
    _collect_gravity_engine(snap)
    _collect_word_gravity(snap)
    _collect_operational(snap)
    _derive_signals(snap)

    return snap
