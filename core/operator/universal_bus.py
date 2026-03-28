"""
Vectrax Core — Universal Bus
================================
Bus centralizado de eventos entre todas las capas del operador.
Implementa pub/sub por canales (identificados por layer_id o nombre),
con priorización, filtros, y registro automático en ledger.

Principio P-003: "Toda acción relevante debe quedar registrada."
Cada evento emitido queda trazado.

Capa: 9 — Bus Universal de Integración
Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("vectrax.operator.universal_bus")


# ---------------------------------------------------------------------------
# Prioridad de eventos
# ---------------------------------------------------------------------------

class EventPriority(str, Enum):
    """Prioridad de un evento en el bus."""
    CRITICAL = "critical"   # Procesado primero, siempre
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"

    @property
    def weight(self) -> int:
        return {
            "critical": 0,
            "high": 1,
            "normal": 2,
            "low": 3,
        }[self.value]


# ---------------------------------------------------------------------------
# Evento del bus
# ---------------------------------------------------------------------------

@dataclass
class BusEvent:
    """Un evento que transita por el bus universal."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    channel: str = ""
    source_layer: int = 0
    event_type: str = ""
    priority: EventPriority = EventPriority.NORMAL
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "channel": self.channel,
            "source_layer": self.source_layer,
            "event_type": self.event_type,
            "priority": self.priority.value,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Suscripción
# ---------------------------------------------------------------------------

# Tipo de callback: recibe un BusEvent, retorna None
EventHandler = Callable[[BusEvent], None]


@dataclass
class Subscription:
    """Una suscripción a un canal del bus."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    channel: str = ""
    handler: EventHandler = field(default=lambda e: None)
    filter_type: Optional[str] = None        # Solo recibir este event_type
    min_priority: EventPriority = EventPriority.LOW  # Ignorar debajo de esto
    subscriber_name: str = ""

    def accepts(self, event: BusEvent) -> bool:
        """Verifica si esta suscripción acepta el evento."""
        if self.filter_type and event.event_type != self.filter_type:
            return False
        if event.priority.weight > self.min_priority.weight:
            return False
        return True


# ---------------------------------------------------------------------------
# Canales predefinidos (uno por capa + especiales)
# ---------------------------------------------------------------------------

class Channels:
    """Canales predefinidos del bus universal."""
    NUCLEUS = "layer.1.nucleus"
    PERCEPTION = "layer.2.perception"
    MEMORY = "layer.3.memory"
    COMPREHENSION = "layer.4.comprehension"
    HYPOTHESIS = "layer.5.hypothesis"
    ACTION = "layer.6.action"
    GOVERNANCE = "layer.7.governance"
    BUILDER = "layer.8.builder"
    BUS = "layer.9.bus"
    CONTRACTS = "layer.10.contracts"
    IDENTITY = "layer.11.identity"
    LEDGER = "layer.12.ledger"

    # Canales especiales
    BROADCAST = "broadcast"      # Todos reciben
    INTEGRITY = "integrity"      # Eventos de integridad
    MODE_CHANGE = "mode_change"  # Cambios de modo operativo

    # Canales live — fuentes vivas del sistema
    LIVE_CHAT        = "live.chat"         # CoreCommEngine: chat núcleo
    LIVE_VOICE       = "live.voice"        # Pipeline de voz
    LIVE_STARS       = "live.stars"        # engine.ingest: estrellas
    LIVE_CONVERGENCE = "live.convergence"  # Motor de convergencias
    LIVE_WATCHDOG    = "live.watchdog"     # Watchdog de archivos
    LIVE_UNIVERSE    = "live.universe"     # Universo / gravedad
    OPERATOR_OUT     = "operator.out"      # Salida observable del reactor

    # Canal externo — gateway de entrada/salida para canales externos
    EXTERNAL         = "external"          # ExternalGateway: web, telegram, etc.

    @classmethod
    def for_layer(cls, layer_id: int) -> str:
        """Canal correspondiente a un layer_id."""
        _map = {
            1: cls.NUCLEUS, 2: cls.PERCEPTION, 3: cls.MEMORY,
            4: cls.COMPREHENSION, 5: cls.HYPOTHESIS, 6: cls.ACTION,
            7: cls.GOVERNANCE, 8: cls.BUILDER, 9: cls.BUS,
            10: cls.CONTRACTS, 11: cls.IDENTITY, 12: cls.LEDGER,
        }
        return _map.get(layer_id, f"layer.{layer_id}.unknown")


# ---------------------------------------------------------------------------
# Universal Bus
# ---------------------------------------------------------------------------

class UniversalBus:
    """
    Bus centralizado de eventos entre capas del operador.

    Responsabilidades:
    - Publish: emitir eventos a un canal
    - Subscribe: registrar handlers para canales
    - Broadcast: emitir a todos los suscriptores
    - Historial: mantener log de eventos emitidos
    - Estadísticas: conteo por canal, tipo, prioridad
    """

    def __init__(self, *, ledger_enabled: bool = True, history_limit: int = 500) -> None:
        self._subscriptions: Dict[str, List[Subscription]] = defaultdict(list)
        self._history: List[BusEvent] = []
        self._history_limit: int = history_limit
        self._ledger_enabled: bool = ledger_enabled
        self._stats: Dict[str, int] = defaultdict(int)
        self._total_published: int = 0
        self._total_delivered: int = 0
        logger.info("UniversalBus initialized | ledger=%s", ledger_enabled)

    # -- Publicar -----------------------------------------------------------

    def publish(self, event: BusEvent) -> int:
        """
        Publica un evento en su canal.
        Retorna el número de handlers que lo procesaron.
        """
        delivered = 0
        channel = event.channel

        # Suscriptores del canal específico
        for sub in self._subscriptions.get(channel, []):
            if sub.accepts(event):
                try:
                    sub.handler(event)
                    delivered += 1
                except Exception as exc:
                    logger.error(
                        "Handler error: sub=%s channel=%s error=%s",
                        sub.subscriber_name, channel, exc,
                    )

        # Suscriptores de broadcast (si no es ya broadcast)
        if channel != Channels.BROADCAST:
            for sub in self._subscriptions.get(Channels.BROADCAST, []):
                if sub.accepts(event):
                    try:
                        sub.handler(event)
                        delivered += 1
                    except Exception as exc:
                        logger.error(
                            "Broadcast handler error: sub=%s error=%s",
                            sub.subscriber_name, exc,
                        )

        # Historial
        self._history.append(event)
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit:]

        # Estadísticas
        self._stats[channel] = self._stats.get(channel, 0) + 1
        self._stats[f"type:{event.event_type}"] = (
            self._stats.get(f"type:{event.event_type}", 0) + 1
        )
        self._total_published += 1
        self._total_delivered += delivered

        # Registro en ledger (solo eventos HIGH/CRITICAL)
        if self._ledger_enabled and event.priority.weight <= EventPriority.HIGH.weight:
            self._record_to_ledger(event, delivered)

        logger.debug(
            "Published: %s → %s | type=%s | delivered=%d",
            event.id, channel, event.event_type, delivered,
        )
        return delivered

    # -- Emitir (helper) ----------------------------------------------------

    def emit(
        self,
        channel: str,
        event_type: str,
        *,
        source_layer: int = 0,
        priority: EventPriority = EventPriority.NORMAL,
        payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> BusEvent:
        """
        Helper para crear y publicar un evento en un solo paso.
        Retorna el evento creado.
        """
        event = BusEvent(
            channel=channel,
            source_layer=source_layer,
            event_type=event_type,
            priority=priority,
            payload=payload or {},
            metadata=metadata or {},
        )
        self.publish(event)
        return event

    # -- Broadcast ----------------------------------------------------------

    def broadcast(
        self,
        event_type: str,
        *,
        source_layer: int = 0,
        priority: EventPriority = EventPriority.NORMAL,
        payload: Optional[Dict[str, Any]] = None,
    ) -> BusEvent:
        """Emite un evento a todos los suscriptores de broadcast."""
        return self.emit(
            channel=Channels.BROADCAST,
            event_type=event_type,
            source_layer=source_layer,
            priority=priority,
            payload=payload,
        )

    # -- Suscribir ----------------------------------------------------------

    def subscribe(
        self,
        channel: str,
        handler: EventHandler,
        *,
        subscriber_name: str = "",
        filter_type: Optional[str] = None,
        min_priority: EventPriority = EventPriority.LOW,
    ) -> str:
        """
        Suscribe un handler a un canal. Retorna el ID de suscripción.
        """
        sub = Subscription(
            channel=channel,
            handler=handler,
            filter_type=filter_type,
            min_priority=min_priority,
            subscriber_name=subscriber_name,
        )
        self._subscriptions[channel].append(sub)
        logger.info(
            "Subscribed: %s → %s (filter=%s)",
            subscriber_name or sub.id, channel, filter_type or "all",
        )
        return sub.id

    def unsubscribe(self, channel: str, subscription_id: str) -> bool:
        """Elimina una suscripción. Retorna False si no se encontró."""
        subs = self._subscriptions.get(channel, [])
        for i, sub in enumerate(subs):
            if sub.id == subscription_id:
                subs.pop(i)
                logger.info("Unsubscribed: %s from %s", subscription_id, channel)
                return True
        return False

    # -- Consultas ----------------------------------------------------------

    def get_history(
        self,
        channel: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[BusEvent]:
        """Consulta el historial de eventos."""
        result = self._history
        if channel:
            result = [e for e in result if e.channel == channel]
        if event_type:
            result = [e for e in result if e.event_type == event_type]
        return result[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        """Estadísticas del bus."""
        return {
            "total_published": self._total_published,
            "total_delivered": self._total_delivered,
            "channels_active": len(self._subscriptions),
            "subscriptions": sum(len(s) for s in self._subscriptions.values()),
            "history_size": len(self._history),
            "by_channel": {
                k: v for k, v in self._stats.items()
                if not k.startswith("type:")
            },
            "by_type": {
                k.removeprefix("type:"): v
                for k, v in self._stats.items()
                if k.startswith("type:")
            },
        }

    def get_subscribers(self, channel: str) -> List[str]:
        """Lista nombres de suscriptores de un canal."""
        return [
            sub.subscriber_name or sub.id
            for sub in self._subscriptions.get(channel, [])
        ]

    # -- Ledger -------------------------------------------------------------

    def _record_to_ledger(self, event: BusEvent, delivered: int) -> None:
        """Registra eventos importantes en el audit ledger."""
        try:
            from core.operator import ledger_bridge as ledger
            ledger.record_event(
                action=f"bus_event:{event.event_type}",
                category=ledger.EventCategory.NUCLEUS,
                risk_zone=(
                    ledger.RiskZone.YELLOW
                    if event.priority == EventPriority.CRITICAL
                    else ledger.RiskZone.GREEN
                ),
                reason=f"Bus event on {event.channel}",
                details={
                    "event_id": event.id,
                    "channel": event.channel,
                    "event_type": event.event_type,
                    "priority": event.priority.value,
                    "source_layer": event.source_layer,
                    "delivered_to": delivered,
                },
            )
        except Exception as exc:
            logger.warning("Could not record bus event to ledger: %s", exc)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_bus: Optional[UniversalBus] = None


def get_universal_bus() -> UniversalBus:
    """Obtener la instancia singleton del bus universal."""
    global _bus
    if _bus is None:
        _bus = UniversalBus()
    return _bus


def reset_bus() -> None:
    """Reset para testing. NO usar en producción."""
    global _bus
    _bus = None
