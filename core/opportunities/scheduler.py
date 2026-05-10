"""
core/opportunities/scheduler.py
=================================
Política temporal de seguimiento. NO envía nada — solo programa jobs y
los marca due. La capa de envío (futura) consumirá `due_jobs(...)`.

Reglas:
    * Al abrir/refrescar oportunidad: se programan dos jobs:
        - WARM   a +3 días
        - URGENT a +7 días
    * Al cerrar: se cancelan jobs pendientes de esa oportunidad.
    * `due_now(...)` calcula qué jobs ya vencieron y propone urgencia
      coherente con `last_activity` de la oportunidad asociada.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List

from .domain import (
    JobStatus,
    OpportunityState,
    OpportunityStatus,
    ReactivationJob,
    Urgency,
)
from .repository import (
    OpportunityRepository,
    ReactivationJobRepository,
)


logger = logging.getLogger("vectrax.opportunities.scheduler")


# Ventanas en días, configurables por env si hace falta a futuro.
WARM_DAYS = 3
URGENT_DAYS = 7


@dataclass(frozen=True)
class SchedulingPolicy:
    warm_days: int = WARM_DAYS
    urgent_days: int = URGENT_DAYS


class ReactivationScheduler:
    """
    Encapsula la política temporal. No conoce SQLite; recibe los repos
    abstractos y opera con `OpportunityState` puro.
    """

    def __init__(
        self,
        opportunities: OpportunityRepository,
        jobs: ReactivationJobRepository,
        policy: SchedulingPolicy | None = None,
    ) -> None:
        self._opps = opportunities
        self._jobs = jobs
        self._policy = policy or SchedulingPolicy()

    @property
    def policy(self) -> SchedulingPolicy:
        return self._policy

    # -- Programación al abrir/refrescar ------------------------------------

    async def schedule_for_opportunity(
        self, op: OpportunityState,
    ) -> List[ReactivationJob]:
        """Programa WARM y URGENT relativos a `last_activity`."""
        if op.status != OpportunityStatus.OPEN:
            return []
        base = op.last_activity or datetime.now(timezone.utc)
        warm = ReactivationJob(
            opportunity_id=op.id,
            owner_id=op.owner_id,
            due_at=base + timedelta(days=self._policy.warm_days),
            urgency=Urgency.WARM,
            status=JobStatus.PENDING,
        )
        urgent = ReactivationJob(
            opportunity_id=op.id,
            owner_id=op.owner_id,
            due_at=base + timedelta(days=self._policy.urgent_days),
            urgency=Urgency.URGENT,
            status=JobStatus.PENDING,
        )
        await self._jobs.schedule(warm)
        await self._jobs.schedule(urgent)
        return [warm, urgent]

    # -- Cancelación al cerrar ---------------------------------------------

    async def cancel_for_opportunity(
        self, opportunity_id: str, owner_id: str,
    ) -> int:
        return await self._jobs.cancel_pending_for_opportunity(
            opportunity_id, owner_id,
        )

    # -- Consulta de jobs vencidos -----------------------------------------

    async def due_now(
        self,
        owner_id: str,
        now: datetime | None = None,
    ) -> List[ReactivationJob]:
        """Lista de jobs cuyo `due_at` ya pasó para un owner dado."""
        ts = now or datetime.now(timezone.utc)
        return await self._jobs.list_pending_for_owner(
            owner_id=owner_id, due_before=ts,
        )

    # -- Helper de clasificación temporal ----------------------------------

    def classify_for_state(
        self, op: OpportunityState, now: datetime | None = None,
    ) -> Urgency | None:
        """
        Para una oportunidad dada, devuelve qué urgencia correspondería
        AHORA según `last_activity`. None si CLOSED o aún reciente.
        """
        if op.status != OpportunityStatus.OPEN:
            return None
        ts = now or datetime.now(timezone.utc)
        delta = ts - op.last_activity
        if delta >= timedelta(days=self._policy.urgent_days):
            return Urgency.URGENT
        if delta >= timedelta(days=self._policy.warm_days):
            return Urgency.WARM
        return None
