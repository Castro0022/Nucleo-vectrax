"""
core/opportunities/events.py
==============================
Event bus en memoria para reaccionar ante cambios en oportunidades sin
acoplar al storage. La persistencia inmutable la hace `EventRepository`;
este bus permite que componentes externos (formatter, métricas,
notificaciones futuras) escuchen sin conocer detalles del repositorio.

Async desde el inicio. Suscriptores reciben `OpportunityEvent`.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Awaitable, Callable, List, Union

from .domain import OpportunityEvent


logger = logging.getLogger("vectrax.opportunities.events")

EventHandler = Callable[[OpportunityEvent], Union[None, Awaitable[None]]]


class OpportunityEventBus:
    """Bus async simple. No retiene historial — eso es del repositorio."""

    def __init__(self) -> None:
        self._subscribers: List[EventHandler] = []
        self._lock = asyncio.Lock()

    async def subscribe(self, handler: EventHandler) -> None:
        async with self._lock:
            self._subscribers.append(handler)

    async def unsubscribe(self, handler: EventHandler) -> None:
        async with self._lock:
            try:
                self._subscribers.remove(handler)
            except ValueError:
                pass

    async def publish(self, event: OpportunityEvent) -> None:
        # Snapshot de subscribers para evitar locks largos.
        async with self._lock:
            subs = list(self._subscribers)
        for h in subs:
            try:
                ret = h(event)
                if inspect.isawaitable(ret):
                    await ret
            except Exception as exc:
                logger.debug(
                    "subscriber failed (event=%s, owner=%s): %s",
                    event.event_type.value, event.owner_id, exc,
                )


# Singleton process-wide. Reset disponible para tests.
_bus: OpportunityEventBus | None = None


def get_event_bus() -> OpportunityEventBus:
    global _bus
    if _bus is None:
        _bus = OpportunityEventBus()
    return _bus


def reset_event_bus_for_tests() -> None:
    global _bus
    _bus = None
