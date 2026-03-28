"""
Vectrax Core — Bus Reactor
============================
Reactor central del UniversalBus. Se suscribe a todos los canales live
del sistema y activa los motores del operador en respuesta a cada evento.

Circuito cerrado:
  Fuente emite evento → BUS → BusReactor clasifica y enruta
  → motor reacciona (percepción | hipótesis | acción)
  → nuevo evento emitido a operator.out (observable)

Canales live de entrada:
  live.chat        — mensajes y respuestas del chat núcleo (CoreCommEngine)
  live.voice       — transcripciones e inputs del pipeline de voz
  live.stars       — creación y actualización de estrellas (engine.ingest)
  live.convergence — eventos de convergencia individual y colectiva
  live.watchdog    — cambios de archivos detectados por el watchdog
  live.universe    — recomputes gravitacionales del universo

Canal de salida del operador:
  operator.out     — respuestas del reactor: hipótesis, señales, propuestas

El reactor es NO BLOQUEANTE: todos los handlers son síncronos y ligeros.
Los errores en handlers son no fatales — el bus continúa activo.

Capa: 9 — Bus Universal de Integración
Creado: 2026-03-15
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from core.operator.universal_bus import (
    BusEvent,
    Channels,
    EventPriority,
    UniversalBus,
    get_universal_bus,
)

logger = logging.getLogger("vectrax.operator.bus_reactor")


# ---------------------------------------------------------------------------
# Canales live (fuentes vivas del sistema)
# ---------------------------------------------------------------------------

class LiveChannels:
    """Canales del bus para las fuentes vivas del sistema."""
    CHAT         = "live.chat"          # CoreCommEngine: chat núcleo
    VOICE        = "live.voice"         # Pipeline de voz
    STARS        = "live.stars"         # engine.ingest: estrellas
    CONVERGENCE  = "live.convergence"   # Motor de convergencias
    WATCHDOG     = "live.watchdog"      # Watchdog de archivos
    UNIVERSE     = "live.universe"      # Universo / gravedad
    OPERATOR_OUT = "operator.out"       # Salida observable del reactor


# ---------------------------------------------------------------------------
# Tipos de evento por canal
# ---------------------------------------------------------------------------

class EventTypes:
    """Tipos de evento estándar por canal."""

    # Chat núcleo
    CHAT_MESSAGE  = "chat.message"
    CHAT_RESPONSE = "chat.response"

    # Voz
    VOICE_INPUT    = "voice.input"
    VOICE_RESPONSE = "voice.response"

    # Estrellas
    STAR_CREATED = "star.created"
    STAR_UPDATED = "star.updated"

    # Convergencias
    CONVERGENCE_CREATED    = "convergence.created"
    CONVERGENCE_COLLECTIVE = "convergence.collective"
    CONVERGENCE_ACTIVATED  = "convergence.activated"

    # Watchdog
    WATCHDOG_FILE_CHANGED = "watchdog.file_changed"
    WATCHDOG_INGESTED     = "watchdog.ingested"

    # Universo
    UNIVERSE_GRAVITY_RECOMPUTED    = "universe.gravity_recomputed"
    UNIVERSE_DECAY_APPLIED         = "universe.decay_applied"
    UNIVERSE_LAYER_TRANSITIONED    = "universe.layer_transitioned"
    UNIVERSE_CONSTELLATION_EVOLVED = "universe.constellation_evolved"
    UNIVERSE_MASS_INJECTED         = "universe.mass_injected"

    # Salidas del reactor
    OPERATOR_MEMORY_UPDATED      = "operator.memory_updated"
    OPERATOR_HYPOTHESIS_PROPOSED = "operator.hypothesis_proposed"
    OPERATOR_ACTION_PROPOSED     = "operator.action_proposed"
    OPERATOR_SIGNAL_INJECTED     = "operator.signal_injected"

    # Gateway externo
    EXTERNAL_MESSAGE_RECEIVED  = "external.message_received"
    EXTERNAL_MESSAGE_RESPONSE  = "external.message_response"


# ---------------------------------------------------------------------------
# Bus Reactor
# ---------------------------------------------------------------------------

class BusReactor:
    """
    Reactor central del bus universal.

    Suscribe todos los canales live y activa los motores del operador
    (percepción, hipótesis, acción) en respuesta a cada evento.

    Las respuestas se emiten como nuevos eventos al canal operator.out,
    cerrando el circuito: percepción → memoria → decisión → salida.

    Es non-blocking: todos los handlers son síncronos y ligeros.
    Los errores en handlers son no fatales — el bus sigue activo.
    """

    def __init__(self, bus: Optional[UniversalBus] = None) -> None:
        self._bus = bus or get_universal_bus()
        self._processed: int = 0
        self._sub_ids: List[str] = []
        self._active: bool = False
        self._start_time: float = time.time()
        logger.info("BusReactor initialized")

    # ── Conexión ──────────────────────────────────────────────────────────

    def wire(self) -> "BusReactor":
        """
        Conecta el reactor a todos los canales live.
        Idempotente: una segunda llamada no hace nada.
        Retorna self para encadenamiento.
        """
        if self._active:
            return self

        _subscriptions = [
            (LiveChannels.CHAT,        "reactor.chat",        self._on_chat),
            (LiveChannels.VOICE,       "reactor.voice",       self._on_voice),
            (LiveChannels.STARS,       "reactor.stars",       self._on_star),
            (LiveChannels.CONVERGENCE, "reactor.convergence", self._on_convergence),
            (LiveChannels.WATCHDOG,    "reactor.watchdog",    self._on_watchdog),
            (LiveChannels.UNIVERSE,    "reactor.universe",    self._on_universe),
            (Channels.EXTERNAL,        "reactor.external",    self._on_external),
        ]

        for channel, name, handler in _subscriptions:
            sub_id = self._bus.subscribe(
                channel,
                handler,
                subscriber_name=name,
            )
            self._sub_ids.append(sub_id)

        self._active = True
        logger.info("BusReactor wired to %d live channels", len(_subscriptions))
        return self

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_chat(self, event: BusEvent) -> None:
        """Reacciona a eventos del chat núcleo."""
        self._processed += 1
        payload = event.payload
        et = event.event_type

        if et == EventTypes.CHAT_MESSAGE:
            # Input de chat → señal de percepción
            self._inject_perception_signal(
                summary=f"Chat: {payload.get('text', '')[:100]}",
                intent="chat_input",
                metadata={"source": "chat", "channel": payload.get("channel", "")},
            )
        elif et == EventTypes.CHAT_RESPONSE:
            # Respuesta de chat → notificación de actualización de memoria
            self._bus.emit(
                channel=LiveChannels.OPERATOR_OUT,
                event_type=EventTypes.OPERATOR_MEMORY_UPDATED,
                source_layer=3,
                payload={
                    "trigger": "chat_response",
                    "star_id": payload.get("star_id", ""),
                    "resolve_mode": payload.get("resolve_mode", ""),
                },
            )

    def _on_voice(self, event: BusEvent) -> None:
        """Reacciona a eventos del pipeline de voz."""
        self._processed += 1
        payload = event.payload

        if event.event_type == EventTypes.VOICE_INPUT:
            # Input de voz → señal de percepción de alta prioridad
            self._inject_perception_signal(
                summary=f"Voz: {payload.get('transcript', '')[:100]}",
                intent="voice_input",
                metadata={"source": "voice"},
                priority=EventPriority.HIGH,
            )
        elif event.event_type == EventTypes.VOICE_RESPONSE:
            # Respuesta de voz → notificación de memoria (igual que chat)
            self._bus.emit(
                channel=LiveChannels.OPERATOR_OUT,
                event_type=EventTypes.OPERATOR_MEMORY_UPDATED,
                source_layer=3,
                payload={
                    "trigger": "voice_response",
                    "star_id": payload.get("star_id", ""),
                    "resolve_mode": payload.get("resolve_mode", ""),
                },
            )

    def _on_star(self, event: BusEvent) -> None:
        """Reacciona a eventos de creación/actualización de estrellas."""
        self._processed += 1
        payload = event.payload

        if event.event_type != EventTypes.STAR_CREATED:
            return  # solo nuevas estrellas generan hipótesis

        gravity = payload.get("gravity_score", 0.0)
        layer   = payload.get("layer", "COLD")

        # Solo generar hipótesis para estrellas con masa gravitacional significativa
        if gravity >= 0.6 or layer in ("HOT", "WARM"):
            self._propose_hypothesis(
                description=(
                    f"Estrella significativa detectada: «{payload.get('content', '')[:80]}» "
                    f"(gravity={gravity:.3f}, layer={layer}). "
                    "¿Es un patrón recurrente con impacto estructural?"
                ),
                context=f"star_id={payload.get('id', '')}",
                confidence=min(0.40 + gravity * 0.40, 0.85),
                tags=["star_pattern", layer.lower()],
                metadata={
                    "star_id": payload.get("id", ""),
                    "gravity_score": gravity,
                    "layer": layer,
                },
            )

    def _on_convergence(self, event: BusEvent) -> None:
        """Reacciona a eventos de convergencia semántica."""
        self._processed += 1
        payload = event.payload
        et = event.event_type

        if et == EventTypes.CONVERGENCE_CREATED:
            # Convergencia individual → hipótesis sobre patrón semántico emergente
            self._propose_hypothesis(
                description=(
                    f"Convergencia semántica detectada: "
                    f"«{payload.get('content', '')[:80]}» "
                    f"— {payload.get('source_count', 0)} rutas distintas convergen. "
                    "¿Debe este punto promover a nodo estructural del grafo?"
                ),
                context=f"coordinate={payload.get('star_id', '')}",
                confidence=0.65,
                tags=["convergence", "semantic_pattern"],
                metadata=payload,
            )

        elif et == EventTypes.CONVERGENCE_COLLECTIVE:
            # Convergencia colectiva (multi-usuario) → propuesta de acción de prioridad alta
            owner_count = payload.get("owner_count", 0)
            self._bus.emit(
                channel=LiveChannels.OPERATOR_OUT,
                event_type=EventTypes.OPERATOR_ACTION_PROPOSED,
                source_layer=5,
                priority=EventPriority.HIGH,
                payload={
                    "trigger": "collective_convergence",
                    "coordinate_id": payload.get("star_id", ""),
                    "owner_count": owner_count,
                    "description": (
                        f"Convergencia colectiva: {owner_count} usuarios llegaron "
                        "independientemente al mismo punto semántico. "
                        "Candidato a promover como núcleo compartido del universo."
                    ),
                },
            )

        elif et == EventTypes.CONVERGENCE_ACTIVATED:
            # Coordenada re-activada → reforzar como señal de percepción
            self._inject_perception_signal(
                summary=f"Coordenada reactivada: {payload.get('content', '')[:80]}",
                intent="convergence_reactivated",
                metadata=payload,
            )

    def _on_watchdog(self, event: BusEvent) -> None:
        """Reacciona a eventos del watchdog de archivos."""
        self._processed += 1
        payload = event.payload
        path   = payload.get("path", "")
        intent = payload.get("intent", "watchdog_event")

        # Inyectar cambio de archivo como señal de percepción
        self._inject_perception_signal(
            summary=f"Watchdog: {path}",
            intent=intent,
            metadata=payload,
        )

    def _on_universe(self, event: BusEvent) -> None:
        """Reacciona a eventos gravitacionales del universo."""
        self._processed += 1
        payload = event.payload

        self._bus.emit(
            channel=LiveChannels.OPERATOR_OUT,
            event_type=EventTypes.OPERATOR_MEMORY_UPDATED,
            source_layer=3,
            payload={
                "trigger": "gravity_recompute",
                "stars_updated": payload.get("stars_updated", 0),
                "constellations_updated": payload.get("constellations_updated", 0),
            },
        )

    def _on_external(self, event: BusEvent) -> None:
        """Reacciona a mensajes externos recibidos por el gateway."""
        if event.event_type != EventTypes.EXTERNAL_MESSAGE_RECEIVED:
            return
        self._processed += 1
        payload = event.payload

        # Inyectar como señal de percepción (igual que chat/voz)
        self._inject_perception_signal(
            summary=f"External [{payload.get('channel', '')}]: "
                    f"{payload.get('content', '')[:100]}",
            intent="external_input",
            metadata={
                "source": "external_gateway",
                "channel": payload.get("channel", ""),
                "user_id": payload.get("user_id", ""),
            },
        )

    # ── Helpers internos

    def _inject_perception_signal(
        self,
        summary: str,
        *,
        intent: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        priority: EventPriority = EventPriority.NORMAL,
    ) -> None:
        """Inyecta una señal en el motor de percepción del operador."""
        try:
            bridge = _get_perception_bridge()
            bridge.inject_operator_signal(
                summary,
                intent=intent,
                metadata=metadata,
            )
            self._bus.emit(
                channel=LiveChannels.OPERATOR_OUT,
                event_type=EventTypes.OPERATOR_SIGNAL_INJECTED,
                source_layer=2,
                priority=priority,
                payload={"summary": summary[:100], "intent": intent},
            )
        except Exception as exc:
            logger.debug("Could not inject perception signal: %s", exc)

    def _propose_hypothesis(
        self,
        description: str,
        *,
        context: str = "",
        confidence: float = 0.5,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Propone una hipótesis al motor de hipótesis del operador."""
        try:
            from core.operator.hypothesis_engine import get_hypothesis_engine
            engine = get_hypothesis_engine()
            hyp = engine.propose(
                description=description,
                context=context,
                initial_confidence=confidence,
                tags=tags or [],
                metadata=metadata or {},
            )
            self._bus.emit(
                channel=LiveChannels.OPERATOR_OUT,
                event_type=EventTypes.OPERATOR_HYPOTHESIS_PROPOSED,
                source_layer=5,
                payload={
                    "hypothesis_id": hyp.id,
                    "description": hyp.description[:120],
                    "confidence": hyp.confidence,
                    "tags": hyp.tags,
                },
            )
        except Exception as exc:
            logger.debug("Could not propose hypothesis: %s", exc)

    # ── Estadísticas ──────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """Estadísticas del reactor y del bus subyacente."""
        return {
            "active": self._active,
            "subscriptions": len(self._sub_ids),
            "events_processed": self._processed,
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "bus_stats": self._bus.get_stats(),
        }

    def get_stats(self) -> Dict[str, Any]:
        """Alias de stats() con campos extendidos de evolución del universo."""
        base = self.stats()
        try:
            from core.operator.universe_evolution import get_universe_evolution
            evo = get_universe_evolution()
            base["evolution_stats"] = evo.get_stats()
        except Exception:
            base["evolution_stats"] = {}
        try:
            from core.operator.hypothesis_engine import get_hypothesis_engine
            hyp = get_hypothesis_engine()
            hyp_stats = hyp.stats()
            base["hypotheses_total"] = hyp_stats.get("total", "n/a")
            base["hypotheses_active"] = hyp_stats.get("active", "n/a")
        except Exception:
            base["hypotheses_total"] = "n/a"
            base["hypotheses_active"] = "n/a"
        base["events_processed"] = self._processed
        return base


# ---------------------------------------------------------------------------
# Perception Bridge singleton (lazy)
# ---------------------------------------------------------------------------

_perception_bridge = None


def _get_perception_bridge():
    """Lazy singleton del PerceptionBridge."""
    global _perception_bridge
    if _perception_bridge is None:
        from core.operator.perception_bridge import PerceptionBridge
        _perception_bridge = PerceptionBridge()
    return _perception_bridge


# ---------------------------------------------------------------------------
# Singleton del reactor
# ---------------------------------------------------------------------------

_reactor: Optional[BusReactor] = None


def get_bus_reactor() -> BusReactor:
    """
    Obtiene el singleton del BusReactor.
    Se auto-conecta al primer acceso (wire() es idempotente).
    También inicializa el UniverseEvolutionEngine para cerrar el circuito.
    """
    global _reactor
    if _reactor is None:
        _reactor = BusReactor()
        _reactor.wire()
        # Cerrar el circuito: conectar el motor de evolución del universo
        try:
            from core.operator.universe_evolution import get_universe_evolution
            get_universe_evolution()  # auto-wires on first access
        except Exception as exc:
            logger.debug("Could not wire UniverseEvolutionEngine: %s", exc)
    return _reactor


def reset_reactor() -> None:
    """Reset para testing. NO usar en producción."""
    global _reactor, _perception_bridge
    _reactor = None
    _perception_bridge = None
