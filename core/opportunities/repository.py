"""
core/opportunities/repository.py
==================================
Interfaz abstracta de persistencia. Todo el dominio depende SOLO de
estos protocolos. Implementaciones concretas (SQLite, Postgres, in-memory
para tests) viven en archivos separados y pueden swap-earse sin tocar
service/scheduler/detector.

Todas las operaciones son async desde el inicio.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Protocol, runtime_checkable

from .domain import (
    JobStatus,
    OpportunityEvent,
    OpportunityState,
    OpportunityStatus,
    ReactivationJob,
    Urgency,
)


@runtime_checkable
class OpportunityRepository(Protocol):
    """Tabla `opportunities` — proyección de estado actual."""

    async def init_schema(self) -> None: ...

    async def save(self, op: OpportunityState) -> None: ...

    async def update(self, op: OpportunityState) -> None: ...

    async def get(
        self, opportunity_id: str, owner_id: str,
    ) -> Optional[OpportunityState]: ...

    async def find_open_for_contact(
        self, owner_id: str, user_id: str,
    ) -> Optional[OpportunityState]: ...

    async def list_for_owner(
        self,
        owner_id: str,
        status: Optional[OpportunityStatus] = None,
        limit: int = 100,
    ) -> List[OpportunityState]: ...


@runtime_checkable
class EventRepository(Protocol):
    """Tabla `opportunity_events` — historial inmutable."""

    async def init_schema(self) -> None: ...

    async def append(self, event: OpportunityEvent) -> None: ...

    async def list_for_opportunity(
        self, opportunity_id: str, owner_id: str,
    ) -> List[OpportunityEvent]: ...


@runtime_checkable
class ReactivationJobRepository(Protocol):
    """Tabla `reactivation_jobs` — motor temporal."""

    async def init_schema(self) -> None: ...

    async def schedule(self, job: ReactivationJob) -> None: ...

    async def cancel_pending_for_opportunity(
        self, opportunity_id: str, owner_id: str,
    ) -> int: ...

    async def list_pending_for_owner(
        self,
        owner_id: str,
        due_before: Optional[datetime] = None,
        urgency: Optional[Urgency] = None,
    ) -> List[ReactivationJob]: ...

    async def mark_processed(
        self, job_id: str, owner_id: str,
    ) -> None: ...


@runtime_checkable
class UnitOfWork(Protocol):
    """Agrupa las tres tablas y permite swap atómico de backend."""

    opportunities: OpportunityRepository
    events: EventRepository
    jobs: ReactivationJobRepository

    async def init_schema(self) -> None: ...
