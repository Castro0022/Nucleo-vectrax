"""
core/opportunities/sqlite_repository.py
=========================================
Implementación concreta de las interfaces de `repository.py` usando
SQLAlchemy 2.0 async + aiosqlite.

Todas las queries van filtradas por `owner_id`. Multi-tenant desde el
inicio; cuando migremos a Postgres bastará cambiar el dialect del
engine.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    and_,
    delete,
    select,
    update as sql_update,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base

from .domain import (
    EventType,
    IntentType,
    JobStatus,
    OpportunityEvent,
    OpportunityState,
    OpportunityStatus,
    ReactivationJob,
    SourceType,
    Urgency,
)


logger = logging.getLogger("vectrax.opportunities.sqlite_repo")

Base = declarative_base()


# ---------------------------------------------------------------------------
# ORM models — internos al backend SQLite
# ---------------------------------------------------------------------------

class _Opportunity(Base):
    __tablename__ = "opportunities"

    id = Column(String(32), primary_key=True)
    owner_id = Column(String(64), nullable=False, index=True)
    user_id = Column(String(128), nullable=False, index=True)
    contact_name = Column(String(256), nullable=True)
    status = Column(String(16), nullable=False, index=True)
    intent_signal = Column(String(256), nullable=False, default="")
    intent_type = Column(String(32), nullable=False, default="INTEREST")
    source_message = Column(Text, nullable=False, default="")
    source_type = Column(String(32), nullable=False, default="AUTO_DETECTED")
    confidence_score = Column(Float, nullable=False, default=0.0)
    priority_score = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime(timezone=True), nullable=False)
    last_activity = Column(
        DateTime(timezone=True), nullable=False, index=True,
    )
    closed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_opp_owner_status", "owner_id", "status"),
    )


class _OpportunityEvent(Base):
    __tablename__ = "opportunity_events"

    id = Column(String(32), primary_key=True)
    opportunity_id = Column(String(32), nullable=False, index=True)
    owner_id = Column(String(64), nullable=False, index=True)
    event_type = Column(String(64), nullable=False)
    payload_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime(timezone=True), nullable=False)


class _ReactivationJob(Base):
    __tablename__ = "reactivation_jobs"

    id = Column(String(32), primary_key=True)
    opportunity_id = Column(String(32), nullable=False, index=True)
    owner_id = Column(String(64), nullable=False, index=True)
    due_at = Column(DateTime(timezone=True), nullable=False, index=True)
    urgency = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, default="PENDING")
    created_at = Column(DateTime(timezone=True), nullable=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_job_owner_due", "owner_id", "due_at"),
    )


# ---------------------------------------------------------------------------
# Helpers de mapeo ORM ↔ dominio
# ---------------------------------------------------------------------------

def _ensure_aware(dt: datetime) -> datetime:
    """SQLite descarta tzinfo. Reinyecta UTC en lecturas."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _to_state(row: _Opportunity) -> OpportunityState:
    return OpportunityState(
        id=row.id,
        owner_id=row.owner_id,
        user_id=row.user_id,
        contact_name=row.contact_name,
        status=OpportunityStatus(row.status),
        intent_signal=row.intent_signal,
        intent_type=IntentType(row.intent_type),
        source_message=row.source_message,
        source_type=SourceType(row.source_type),
        confidence_score=float(row.confidence_score),
        priority_score=float(row.priority_score),
        created_at=_ensure_aware(row.created_at),
        last_activity=_ensure_aware(row.last_activity),
        closed_at=_ensure_aware(row.closed_at) if row.closed_at else None,
        updated_at=_ensure_aware(row.updated_at),
    )


def _from_state(op: OpportunityState) -> dict:
    return {
        "id": op.id,
        "owner_id": op.owner_id,
        "user_id": op.user_id,
        "contact_name": op.contact_name,
        "status": op.status.value,
        "intent_signal": op.intent_signal,
        "intent_type": op.intent_type.value,
        "source_message": op.source_message,
        "source_type": op.source_type.value,
        "confidence_score": op.confidence_score,
        "priority_score": op.priority_score,
        "created_at": op.created_at,
        "last_activity": op.last_activity,
        "closed_at": op.closed_at,
        "updated_at": op.updated_at,
    }


def _to_event(row: _OpportunityEvent) -> OpportunityEvent:
    try:
        payload = json.loads(row.payload_json)
    except Exception:
        payload = {}
    return OpportunityEvent(
        id=row.id,
        opportunity_id=row.opportunity_id,
        owner_id=row.owner_id,
        event_type=EventType(row.event_type),
        payload=payload,
        created_at=_ensure_aware(row.created_at),
    )


def _to_job(row: _ReactivationJob) -> ReactivationJob:
    return ReactivationJob(
        id=row.id,
        opportunity_id=row.opportunity_id,
        owner_id=row.owner_id,
        due_at=_ensure_aware(row.due_at),
        urgency=Urgency(row.urgency),
        status=JobStatus(row.status),
        created_at=_ensure_aware(row.created_at),
        processed_at=(
            _ensure_aware(row.processed_at)
            if row.processed_at else None
        ),
    )


# ---------------------------------------------------------------------------
# Implementaciones concretas
# ---------------------------------------------------------------------------

class SqliteOpportunityRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def init_schema(self) -> None:
        # Schema se crea en SqliteUnitOfWork.init_schema (single-shot).
        return None

    async def save(self, op: OpportunityState) -> None:
        async with self._sessionmaker() as s:
            s.add(_Opportunity(**_from_state(op)))
            await s.commit()

    async def update(self, op: OpportunityState) -> None:
        async with self._sessionmaker() as s:
            payload = _from_state(op)
            payload.pop("id")
            payload.pop("owner_id")
            stmt = (
                sql_update(_Opportunity)
                .where(
                    and_(
                        _Opportunity.id == op.id,
                        _Opportunity.owner_id == op.owner_id,
                    )
                )
                .values(**payload)
            )
            await s.execute(stmt)
            await s.commit()

    async def get(
        self, opportunity_id: str, owner_id: str,
    ) -> Optional[OpportunityState]:
        async with self._sessionmaker() as s:
            stmt = select(_Opportunity).where(
                and_(
                    _Opportunity.id == opportunity_id,
                    _Opportunity.owner_id == owner_id,
                )
            )
            row = (await s.execute(stmt)).scalar_one_or_none()
            return _to_state(row) if row else None

    async def find_open_for_contact(
        self, owner_id: str, user_id: str,
    ) -> Optional[OpportunityState]:
        async with self._sessionmaker() as s:
            stmt = (
                select(_Opportunity)
                .where(
                    and_(
                        _Opportunity.owner_id == owner_id,
                        _Opportunity.user_id == user_id,
                        _Opportunity.status == OpportunityStatus.OPEN.value,
                    )
                )
                .order_by(_Opportunity.last_activity.desc())
                .limit(1)
            )
            row = (await s.execute(stmt)).scalar_one_or_none()
            return _to_state(row) if row else None

    async def list_for_owner(
        self,
        owner_id: str,
        status: Optional[OpportunityStatus] = None,
        limit: int = 100,
    ) -> List[OpportunityState]:
        async with self._sessionmaker() as s:
            stmt = select(_Opportunity).where(_Opportunity.owner_id == owner_id)
            if status is not None:
                stmt = stmt.where(_Opportunity.status == status.value)
            stmt = stmt.order_by(
                _Opportunity.last_activity.desc(),
            ).limit(limit)
            rows = (await s.execute(stmt)).scalars().all()
            return [_to_state(r) for r in rows]


class SqliteEventRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def init_schema(self) -> None:
        return None

    async def append(self, event: OpportunityEvent) -> None:
        async with self._sessionmaker() as s:
            s.add(_OpportunityEvent(
                id=event.id,
                opportunity_id=event.opportunity_id,
                owner_id=event.owner_id,
                event_type=event.event_type.value,
                payload_json=json.dumps(event.payload, ensure_ascii=False),
                created_at=event.created_at,
            ))
            await s.commit()

    async def list_for_opportunity(
        self, opportunity_id: str, owner_id: str,
    ) -> List[OpportunityEvent]:
        async with self._sessionmaker() as s:
            stmt = (
                select(_OpportunityEvent)
                .where(
                    and_(
                        _OpportunityEvent.opportunity_id == opportunity_id,
                        _OpportunityEvent.owner_id == owner_id,
                    )
                )
                .order_by(_OpportunityEvent.created_at.asc())
            )
            rows = (await s.execute(stmt)).scalars().all()
            return [_to_event(r) for r in rows]


class SqliteReactivationJobRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def init_schema(self) -> None:
        return None

    async def schedule(self, job: ReactivationJob) -> None:
        async with self._sessionmaker() as s:
            s.add(_ReactivationJob(
                id=job.id,
                opportunity_id=job.opportunity_id,
                owner_id=job.owner_id,
                due_at=job.due_at,
                urgency=job.urgency.value,
                status=job.status.value,
                created_at=job.created_at,
                processed_at=job.processed_at,
            ))
            await s.commit()

    async def cancel_pending_for_opportunity(
        self, opportunity_id: str, owner_id: str,
    ) -> int:
        async with self._sessionmaker() as s:
            stmt = (
                sql_update(_ReactivationJob)
                .where(
                    and_(
                        _ReactivationJob.opportunity_id == opportunity_id,
                        _ReactivationJob.owner_id == owner_id,
                        _ReactivationJob.status == JobStatus.PENDING.value,
                    )
                )
                .values(status=JobStatus.CANCELLED.value)
            )
            result = await s.execute(stmt)
            await s.commit()
            return result.rowcount or 0

    async def list_pending_for_owner(
        self,
        owner_id: str,
        due_before: Optional[datetime] = None,
        urgency: Optional[Urgency] = None,
    ) -> List[ReactivationJob]:
        async with self._sessionmaker() as s:
            stmt = select(_ReactivationJob).where(
                and_(
                    _ReactivationJob.owner_id == owner_id,
                    _ReactivationJob.status == JobStatus.PENDING.value,
                )
            )
            if due_before is not None:
                stmt = stmt.where(_ReactivationJob.due_at <= due_before)
            if urgency is not None:
                stmt = stmt.where(_ReactivationJob.urgency == urgency.value)
            stmt = stmt.order_by(_ReactivationJob.due_at.asc())
            rows = (await s.execute(stmt)).scalars().all()
            return [_to_job(r) for r in rows]

    async def mark_processed(
        self, job_id: str, owner_id: str,
    ) -> None:
        async with self._sessionmaker() as s:
            stmt = (
                sql_update(_ReactivationJob)
                .where(
                    and_(
                        _ReactivationJob.id == job_id,
                        _ReactivationJob.owner_id == owner_id,
                    )
                )
                .values(
                    status=JobStatus.PROCESSED.value,
                    processed_at=datetime.now(timezone.utc),
                )
            )
            await s.execute(stmt)
            await s.commit()


class SqliteUnitOfWork:
    """Une las tres tablas detrás de un único engine."""

    def __init__(self, db_url: str) -> None:
        # Para SQLite con aiosqlite. Cambiar a postgresql+asyncpg://...
        # sólo requiere otro URL aquí.
        self._engine: AsyncEngine = create_async_engine(
            db_url, echo=False, future=True,
        )
        self._sessionmaker = async_sessionmaker(
            self._engine, expire_on_commit=False, class_=AsyncSession,
        )
        self.opportunities = SqliteOpportunityRepository(self._sessionmaker)
        self.events = SqliteEventRepository(self._sessionmaker)
        self.jobs = SqliteReactivationJobRepository(self._sessionmaker)

    async def init_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self._engine.dispose()
