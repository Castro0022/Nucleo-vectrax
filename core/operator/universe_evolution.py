"""
Vectrax Core — Universe Evolution Engine
==========================================
Motor de evolución autónoma del universo cognitivo.

Reacciona a cada evento que entra al sistema para reorganizar el universo:
  - Actualiza masa gravitacional y distancia al núcleo de las estrellas afectadas
  - Reasigna capas (outer → mid → core) cuando la evidencia lo justifica
  - Propaga cambios a vecinos directos en el grafo semántico
  - Detecta y forma nuevas constelaciones al acumularse densidad suficiente
  - Aplica decaimiento gravitacional a patrones inactivos (HOT→WARM→COLD→DEEP)
  - Compacta patrones repetidos en constellaciones del learning layer

El operador percibe, procesa, reorganiza y escribe de vuelta en memoria.
El universo no solo registra conocimiento: se reordena y evoluciona por
consecuencia natural de lo que entra.

Restricciones absolutas:
  - NUNCA modifica estrellas en CHANNEL_CREATOR (núcleo inmutable)
  - NUNCA elimina estrellas ni constelaciones
  - Toda excepción es no fatal — el sistema continúa activo
  - Escribe de vuelta en DB todos los cambios de masa, capa y distancia

Flujo cerrado:
  evento live → BusReactor → operator.out
                    ↓
  UniverseEvolutionEngine reacciona:
    star event      → gravity + layer + mass + neighbors → DB
    convergence     → mass + distance + vecinos → DB
    operator.out    → dispara decay cycle (throttled)
                    ↓
  emit live.universe → BusReactor._on_universe → operator.out

Capa: 3 — Memoria y Gravedad Cognitiva
Creado: 2026-03-15
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("vectrax.operator.universe_evolution")

# ---------------------------------------------------------------------------
# Constantes de throttle y límites
# ---------------------------------------------------------------------------

# Intervalo mínimo entre ciclos de decay (segundos)
DECAY_INTERVAL_SECONDS = 300          # 5 minutos

# Intervalo mínimo entre comprobaciones de constellaciones por canal
CONSTELLATION_INTERVAL_SECONDS = 60   # 1 minuto

# Máximo de vecinos directos a recomputar por evento de convergencia
MAX_NEIGHBOR_RECOMPUTE = 5

# Mínimo de vecinos directos para propagar reorganización
MIN_NEIGHBORS_FOR_PROPAGATION = 1

# Mínimo de cambio en gravity_score para considerar una transición de capa
LAYER_CHANGE_EPSILON = 0.001


# ---------------------------------------------------------------------------
# Universe Evolution Engine
# ---------------------------------------------------------------------------

class UniverseEvolutionEngine:
    """
    Motor autónomo de evolución del universo cognitivo.

    Se suscribe a los canales live del bus y actúa como reaccionador
    de gravedad: cada evento provoca recómputos locales y, cuando el
    sistema acumula suficiente actividad, ciclos de decay y compactación.

    Es non-blocking: todos los handlers son síncronos y ligeros.
    Los errores son no fatales — el bus y el sistema continúan activos.
    """

    def __init__(self, bus=None) -> None:
        from core.operator.universal_bus import get_universal_bus
        self._bus = bus or get_universal_bus()
        self._wired: bool = False
        self._sub_ids: List[str] = []

        # Contadores observables
        self._evolution_count: int = 0
        self._layer_transitions: int = 0
        self._mass_updates: int = 0
        self._decay_runs: int = 0
        self._constellation_checks: int = 0
        self._start_time: float = time.time()

        # Throttle timestamps por canal (canal → last_check_ts)
        self._last_decay_ts: float = 0.0
        self._last_constellation_ts: Dict[str, float] = {}

        # Conjunto de star_ids ya procesados en el ciclo actual (anti-cascade)
        self._processing: Set[str] = set()

        logger.info("UniverseEvolutionEngine initialized")

    # ── Conexión al bus ───────────────────────────────────────────────────

    def wire(self) -> "UniverseEvolutionEngine":
        """
        Suscribe el motor a los canales vivos relevantes.
        Idempotente: una segunda llamada no hace nada.
        """
        if self._wired:
            return self

        from core.operator.bus_reactor import LiveChannels, EventTypes

        subscriptions = [
            (LiveChannels.STARS,        "evolution.stars",     self._on_star),
            (LiveChannels.CONVERGENCE,  "evolution.convergence", self._on_convergence),
            (LiveChannels.OPERATOR_OUT, "evolution.operator",  self._on_operator_out),
        ]

        for channel, name, handler in subscriptions:
            sub_id = self._bus.subscribe(channel, handler, subscriber_name=name)
            self._sub_ids.append(sub_id)

        self._wired = True
        logger.info(
            "UniverseEvolutionEngine wired to %d channels", len(subscriptions)
        )
        return self

    # ── Handlers del bus ──────────────────────────────────────────────────

    def _on_star(self, event) -> None:
        """
        Reacciona a star.created y star.updated.

        Acciones:
          1. Recomputa gravity_score y layer de la estrella
          2. Recomputa masa semántica (in_degree + coherencia con vecinos)
          3. Persiste cambios en DB
          4. Si cambió de capa → emite universe.layer_transitioned
          5. Con throttle, verifica si hay nuevas constelaciones emergentes
        """
        from core.operator.bus_reactor import EventTypes
        payload = event.payload
        star_id = payload.get("id", "")
        channel = payload.get("channel", "")

        # Proteger núcleo inmutable
        if not star_id or channel == "creator":
            return

        # Anti-cascade: no reprocesar una estrella que ya estamos procesando
        if star_id in self._processing:
            return

        self._processing.add(star_id)
        try:
            owner = payload.get("owner", "")
            self._evolve_star(star_id, channel, owner)
            self._evolution_count += 1

            # Verificar constelaciones emergentes (throttled por canal)
            self._maybe_check_constellations(channel, owner)
        finally:
            self._processing.discard(star_id)

    def _on_convergence(self, event) -> None:
        """
        Reacciona a convergence.created y convergence.collective.

        Acciones:
          1. Recalcula masa de la estrella de convergencia
          2. Propaga reorganización a vecinos directos (máx. MAX_NEIGHBOR_RECOMPUTE)
          3. Emite universe.gravity_recomputed (HIGH si es colectiva)
        """
        from core.operator.bus_reactor import EventTypes
        from core.operator.universal_bus import EventPriority
        payload = event.payload
        star_id = payload.get("star_id", "")
        channel = payload.get("channel", "")

        if not star_id or channel == "creator":
            return

        if star_id in self._processing:
            return

        is_collective = (event.event_type == EventTypes.CONVERGENCE_COLLECTIVE)

        self._processing.add(star_id)
        try:
            # Recalcular masa del nodo de convergencia
            new_mass = self._recompute_mass(star_id)

            # Propagar a vecinos directos (reorganización local)
            neighbor_masses = self._propagate_to_neighbors(star_id, limit=MAX_NEIGHBOR_RECOMPUTE)

            self._evolution_count += 1

            # Emitir evento de universo
            priority = EventPriority.HIGH if is_collective else EventPriority.NORMAL
            self._emit_gravity_recomputed(
                trigger=event.event_type,
                stars_updated=1 + len(neighbor_masses),
                constellations_updated=0,
                priority=priority,
            )
        finally:
            self._processing.discard(star_id)

    def _on_operator_out(self, event) -> None:
        """
        Reacciona a eventos de salida del reactor.

        Ante operator.memory_updated → comprueba si es hora del ciclo de decay.
        """
        from core.operator.bus_reactor import EventTypes
        if event.event_type == EventTypes.OPERATOR_MEMORY_UPDATED:
            self._maybe_run_decay()

    # ── Evolución de estrellas ────────────────────────────────────────────

    def _evolve_star(self, star_id: str, channel: str, owner: str) -> None:
        """
        Evoluciona una estrella: gravity, layer, mass, distance.
        Escribe de vuelta en DB. No fatal.
        """
        try:
            from vectrax import db
            from vectrax.gravity import compute_star_gravity, assign_layer
            from vectrax.identity import CHANNEL_CREATOR

            star = db.get_star(star_id)
            if not star or star.channel == CHANNEL_CREATOR:
                return

            old_layer = star.layer

            # 1. Recomputar gravity score (repetición + éxito)
            new_gravity = compute_star_gravity(star)
            new_layer = assign_layer(new_gravity)

            changed = abs(new_gravity - star.gravity_score) > LAYER_CHANGE_EPSILON
            layer_changed = (new_layer != old_layer)

            if changed:
                star.gravity_score = new_gravity
                star.layer = new_layer

            # 2. Recomputar masa semántica (conexiones + coherencia)
            new_mass = self._recompute_mass(star_id)  # persiste internamente
            self._mass_updates += 1

            # 3. Si la gravity también cambió, persistir (update_mass persiste masa ya)
            if changed and not layer_changed:
                # Solo necesitamos actualizar gravity/layer — update_mass ya hizo la masa
                star = db.get_star(star_id)  # releer post-update_mass
                if star:
                    star.gravity_score = new_gravity
                    star.layer = new_layer
                    db.update_star(star)

            # 4. Emitir si hubo transición de capa
            if layer_changed:
                self._layer_transitions += 1
                self._emit_layer_transitioned(
                    star_id=star_id,
                    old_layer=old_layer,
                    new_layer=new_layer,
                    gravity=new_gravity,
                    channel=channel,
                    owner=owner,
                )

        except Exception as exc:
            logger.debug("_evolve_star(%s) error: %s", star_id[:8], exc)

    def _recompute_mass(self, star_id: str) -> float:
        """
        Recalcula masa semántica de una estrella usando cognitive_gravity.update_mass.
        Retorna la nueva masa, o MIN_MASS si falla.
        """
        try:
            from vectrax.cognitive_gravity import update_mass
            from vectrax.models import MIN_MASS
            return update_mass(star_id)
        except Exception as exc:
            logger.debug("_recompute_mass(%s) error: %s", star_id[:8], exc)
            from vectrax.models import MIN_MASS
            return MIN_MASS

    def _propagate_to_neighbors(
        self, star_id: str, limit: int = MAX_NEIGHBOR_RECOMPUTE
    ) -> List[float]:
        """
        Recomputa masa de vecinos directos de una estrella.
        Retorna lista de nuevas masas de los vecinos actualizados.
        """
        masses: List[float] = []
        try:
            from vectrax import graph as g
            from vectrax import db
            from vectrax.identity import CHANNEL_CREATOR

            neighbors = g.get_neighbors(star_id)[:limit]
            for nbr_id, _ in neighbors:
                if nbr_id in self._processing:
                    continue
                nbr = db.get_star(nbr_id)
                if nbr and nbr.channel != CHANNEL_CREATOR:
                    new_mass = self._recompute_mass(nbr_id)
                    masses.append(new_mass)
        except Exception as exc:
            logger.debug("_propagate_to_neighbors(%s) error: %s", star_id[:8], exc)
        return masses

    # ── Verificación de constelaciones (throttled) ────────────────────────

    def _maybe_check_constellations(self, channel: str, owner: str) -> None:
        """
        Verifica constelaciones emergentes para un canal.
        Throttled por canal: máx. una vez por CONSTELLATION_INTERVAL_SECONDS.
        """
        key = f"{channel}:{owner}"
        now = time.time()
        if now - self._last_constellation_ts.get(key, 0.0) < CONSTELLATION_INTERVAL_SECONDS:
            return

        self._last_constellation_ts[key] = now
        self._constellation_checks += 1

        try:
            from vectrax.engine import detect_patterns
            updated = detect_patterns(channel=channel, owner=owner)
            if updated:
                self._emit_constellation_evolved(
                    channel=channel,
                    owner=owner,
                    count=len(updated),
                )
                logger.debug(
                    "UniverseEvolution: %d constellations evolved in %s/%s",
                    len(updated), channel, owner,
                )
        except Exception as exc:
            logger.debug("_maybe_check_constellations(%s) error: %s", key, exc)

    # ── Ciclo de decay (throttled) ─────────────────────────────────────────

    def _maybe_run_decay(self) -> None:
        """
        Ejecuta el ciclo de decay si ha pasado suficiente tiempo.
        Throttled: máx. una vez por DECAY_INTERVAL_SECONDS.
        """
        now = time.time()
        if now - self._last_decay_ts < DECAY_INTERVAL_SECONDS:
            return

        self._last_decay_ts = now
        self._run_decay_cycle()

    def _run_decay_cycle(self) -> None:
        """
        Ciclo completo de evolución natural del universo:

        1. Aplica decay gravitacional al GravityIndex (core.learn):
           HOT → WARM → COLD → DEEP para patrones inactivos.

        2. Refresca capas en el universo vectrax (stars DB):
           Las estrellas cuyos patrones decayeron se recomputan.

        3. Compacta constellaciones densas en el learning layer.

        4. Emite universe.decay_applied al bus.
        """
        logger.info("UniverseEvolution: starting decay cycle")
        self._decay_runs += 1

        demoted_count = 0
        compacted_count = 0

        # ── Paso 1: Decay en GravityIndex (core.learn) ──────────────────
        try:
            from core.learn.gravity_engine import get_gravity_index
            from core.learn.gravity_decay import apply_decay
            grav_idx = get_gravity_index()
            demoted = apply_decay(grav_idx)
            demoted_count = len(demoted)
            if demoted:
                logger.info(
                    "UniverseEvolution: %d GravityIndex records decayed", demoted_count
                )
        except Exception as exc:
            logger.debug("Decay step 1 (GravityIndex) error: %s", exc)

        # ── Paso 2: Compactación de constellaciones (core.learn) ─────────
        try:
            from core.learn.gravity_engine import get_gravity_index
            from core.learn.constellation import compact
            grav_idx = get_gravity_index()
            new_constellations = compact(grav_idx)
            compacted_count = len(new_constellations)
            if new_constellations:
                logger.info(
                    "UniverseEvolution: %d constellations compacted", compacted_count
                )
        except Exception as exc:
            logger.debug("Decay step 2 (compaction) error: %s", exc)

        # ── Paso 3: Refresco de capas en universo vectrax ─────────────────
        stars_refreshed = self._refresh_universe_layers()

        # ── Paso 4: Emitir evento de decay ────────────────────────────────
        try:
            from core.operator.bus_reactor import LiveChannels, EventTypes
            self._bus.emit(
                channel=LiveChannels.UNIVERSE,
                event_type=EventTypes.UNIVERSE_DECAY_APPLIED,
                source_layer=3,
                payload={
                    "gravity_records_demoted": demoted_count,
                    "constellations_compacted": compacted_count,
                    "stars_layer_refreshed": stars_refreshed,
                    "decay_run": self._decay_runs,
                },
            )
        except Exception as exc:
            logger.debug("Decay step 4 (emit) error: %s", exc)

        logger.info(
            "UniverseEvolution: decay cycle complete — "
            "demoted=%d compacted=%d stars_refreshed=%d",
            demoted_count, compacted_count, stars_refreshed,
        )

    def _refresh_universe_layers(self) -> int:
        """
        Recomputa gravity y layer para todas las estrellas no-creator del universo.
        Escribe de vuelta en DB. Retorna el número de estrellas actualizadas.
        No fatal.
        """
        updated = 0
        try:
            from vectrax import db
            from vectrax.gravity import compute_star_gravity, assign_layer
            from vectrax.identity import CHANNEL_CREATOR

            stars = db.get_all_stars()
            for star in stars:
                if star.channel == CHANNEL_CREATOR:
                    continue
                try:
                    new_gravity = compute_star_gravity(star)
                    new_layer = assign_layer(new_gravity)
                    if (
                        abs(new_gravity - star.gravity_score) > LAYER_CHANGE_EPSILON
                        or new_layer != star.layer
                    ):
                        star.gravity_score = new_gravity
                        star.layer = new_layer
                        db.update_star(star)
                        updated += 1
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("_refresh_universe_layers error: %s", exc)
        return updated

    # ── Emisores de eventos ───────────────────────────────────────────────

    def _emit_gravity_recomputed(
        self,
        trigger: str,
        stars_updated: int,
        constellations_updated: int,
        priority=None,
    ) -> None:
        """Emite universe.gravity_recomputed a live.universe."""
        try:
            from core.operator.bus_reactor import LiveChannels, EventTypes
            from core.operator.universal_bus import EventPriority
            self._bus.emit(
                channel=LiveChannels.UNIVERSE,
                event_type=EventTypes.UNIVERSE_GRAVITY_RECOMPUTED,
                source_layer=3,
                priority=priority or EventPriority.NORMAL,
                payload={
                    "trigger": trigger,
                    "stars_updated": stars_updated,
                    "constellations_updated": constellations_updated,
                    "evolution_count": self._evolution_count,
                },
            )
        except Exception as exc:
            logger.debug("_emit_gravity_recomputed error: %s", exc)

    def _emit_layer_transitioned(
        self,
        *,
        star_id: str,
        old_layer: str,
        new_layer: str,
        gravity: float,
        channel: str,
        owner: str,
    ) -> None:
        """Emite universe.layer_transitioned cuando una estrella cambia de capa."""
        try:
            from core.operator.bus_reactor import LiveChannels, EventTypes
            from core.operator.universal_bus import EventPriority
            self._bus.emit(
                channel=LiveChannels.UNIVERSE,
                event_type=EventTypes.UNIVERSE_LAYER_TRANSITIONED,
                source_layer=3,
                priority=EventPriority.HIGH,
                payload={
                    "star_id": star_id,
                    "old_layer": old_layer,
                    "new_layer": new_layer,
                    "gravity_score": round(gravity, 4),
                    "channel": channel,
                    "owner": owner,
                },
            )
            logger.info(
                "UniverseEvolution: layer transition %s → %s for star %s (gravity=%.4f)",
                old_layer, new_layer, star_id[:8], gravity,
            )
        except Exception as exc:
            logger.debug("_emit_layer_transitioned error: %s", exc)

    def _emit_constellation_evolved(
        self, *, channel: str, owner: str, count: int
    ) -> None:
        """Emite universe.constellation_evolved cuando nuevas constelaciones emergen."""
        try:
            from core.operator.bus_reactor import LiveChannels, EventTypes
            self._bus.emit(
                channel=LiveChannels.UNIVERSE,
                event_type=EventTypes.UNIVERSE_CONSTELLATION_EVOLVED,
                source_layer=3,
                payload={
                    "channel": channel,
                    "owner": owner,
                    "constellations_count": count,
                    "evolution_count": self._evolution_count,
                },
            )
        except Exception as exc:
            logger.debug("_emit_constellation_evolved error: %s", exc)

    # ── Estadísticas ──────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Estadísticas del motor de evolución del universo."""
        return {
            "wired": self._wired,
            "subscriptions": len(self._sub_ids),
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "evolution_count": self._evolution_count,
            "layer_transitions": self._layer_transitions,
            "mass_updates": self._mass_updates,
            "decay_runs": self._decay_runs,
            "constellation_checks": self._constellation_checks,
            "last_decay_ts": self._last_decay_ts,
            "channels_throttled": len(self._last_constellation_ts),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_engine: Optional[UniverseEvolutionEngine] = None


def get_universe_evolution() -> UniverseEvolutionEngine:
    """
    Obtiene el singleton del UniverseEvolutionEngine.
    Se auto-conecta al bus en el primer acceso (wire() es idempotente).
    """
    global _engine
    if _engine is None:
        _engine = UniverseEvolutionEngine()
        _engine.wire()
    return _engine


def reset_universe_evolution() -> None:
    """Reset para testing. NO usar en producción."""
    global _engine
    _engine = None
