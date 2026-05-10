"""
tests/opportunities/test_opportunities.py
==========================================
Cubre los 11 escenarios obligatorios de la capa de oportunidades:

    1.  detecta señal abierta
    2.  detecta señal cerrada
    3.  crea oportunidad OPEN
    4.  actualiza oportunidad existente
    5.  cierra oportunidad
    6.  cancela reactivaciones al cerrar
    7.  marca WARM después de 3 días
    8.  marca URGENT después de 7 días
    9.  no mezcla oportunidades entre distintos owner_id
    10. registra eventos inmutables
    11. formatter genera mensaje humano
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from core.opportunities import (
    EventType,
    JobStatus,
    OpportunityDetector,
    OpportunityService,
    OpportunityStatus,
    ReactivationScheduler,
    SqliteUnitOfWork,
    Urgency,
    humanize,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def uow(tmp_path):
    db_path = tmp_path / "opps.db"
    uow = SqliteUnitOfWork(f"sqlite+aiosqlite:///{db_path}")
    await uow.init_schema()
    yield uow
    await uow.dispose()


@pytest_asyncio.fixture
async def service(uow):
    scheduler = ReactivationScheduler(uow.opportunities, uow.jobs)
    return OpportunityService(
        opportunities=uow.opportunities,
        events=uow.events,
        jobs=uow.jobs,
        scheduler=scheduler,
    )


# ---------------------------------------------------------------------------
# 1. Detector — open
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detector_detects_open_signal():
    det = OpportunityDetector()
    r = await det.detect("Carlos dice que lo piensa para la próxima semana.")
    assert r.matched is True
    assert r.is_open is True
    assert r.is_closed is False
    assert r.matched_signal in {
        "lo pienso", "lo piensa",
        "la próxima semana", "la proxima semana",
    }
    assert r.contact_name == "Carlos"


# 2. Detector — closed

@pytest.mark.asyncio
async def test_detector_detects_closed_signal():
    det = OpportunityDetector()
    r = await det.detect("Carlos ya pagué la factura, todo confirmado.")
    assert r.matched is True
    assert r.is_closed is True
    assert r.matched_signal in {"ya pagué", "ya pague", "confirmado"}


# ---------------------------------------------------------------------------
# 3. Service — crea OPEN
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_service_creates_open_opportunity(service, uow):
    res = await service.process_message(
        owner_id="mario",
        content="Carlos dice que lo piensa para la próxima semana.",
    )
    assert res.matched is True
    assert res.action == "created"
    assert res.opportunity_id is not None

    opps = await uow.opportunities.list_for_owner("mario")
    assert len(opps) == 1
    op = opps[0]
    assert op.status == OpportunityStatus.OPEN
    assert op.contact_name == "Carlos"
    assert op.user_id == "carlos"


# 4. Service — actualiza existente

@pytest.mark.asyncio
async def test_service_updates_existing_opportunity(service, uow):
    await service.process_message(
        owner_id="mario",
        content="Carlos me dijo que lo piensa.",
    )
    res2 = await service.process_message(
        owner_id="mario",
        content="Carlos hablamos la próxima semana.",
    )
    assert res2.action == "updated"

    opps = await uow.opportunities.list_for_owner("mario")
    assert len(opps) == 1
    assert opps[0].status == OpportunityStatus.OPEN


# 5. Service — cierra oportunidad

@pytest.mark.asyncio
async def test_service_closes_opportunity(service, uow):
    res_open = await service.process_message(
        owner_id="mario",
        content="Carlos dijo que lo piensa.",
    )
    res_close = await service.process_message(
        owner_id="mario",
        content="Carlos ya pagué, listo.",
    )
    assert res_close.action == "closed"
    assert res_close.opportunity_id == res_open.opportunity_id

    op = await uow.opportunities.get(res_close.opportunity_id, "mario")
    assert op is not None
    assert op.status == OpportunityStatus.CLOSED
    assert op.closed_at is not None


# 6. Service — cancela reactivaciones al cerrar

@pytest.mark.asyncio
async def test_close_cancels_pending_jobs(service, uow):
    res = await service.process_message(
        owner_id="mario",
        content="Carlos dijo que lo piensa la próxima semana.",
    )
    pending = await uow.jobs.list_pending_for_owner("mario")
    assert len(pending) == 2

    await service.process_message(
        owner_id="mario",
        content="Carlos ya pagué.",
    )

    pending_after = await uow.jobs.list_pending_for_owner("mario")
    assert len(pending_after) == 0


# ---------------------------------------------------------------------------
# 7-8. Scheduler — WARM 3d / URGENT 7d
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scheduler_marks_warm_after_3_days(uow):
    """Una oportunidad con last_activity de hace 3 días debe ser WARM."""
    from core.opportunities.domain import OpportunityState

    base = datetime.now(timezone.utc) - timedelta(days=3, hours=1)
    op = OpportunityState(
        owner_id="mario",
        user_id="carlos",
        contact_name="Carlos",
        last_activity=base,
        created_at=base,
        updated_at=base,
    )
    await uow.opportunities.save(op)
    sched = ReactivationScheduler(uow.opportunities, uow.jobs)
    assert sched.classify_for_state(op) == Urgency.WARM


@pytest.mark.asyncio
async def test_scheduler_marks_urgent_after_7_days(uow):
    from core.opportunities.domain import OpportunityState

    base = datetime.now(timezone.utc) - timedelta(days=7, hours=1)
    op = OpportunityState(
        owner_id="mario",
        user_id="carlos",
        last_activity=base,
        created_at=base,
        updated_at=base,
    )
    await uow.opportunities.save(op)
    sched = ReactivationScheduler(uow.opportunities, uow.jobs)
    assert sched.classify_for_state(op) == Urgency.URGENT


@pytest.mark.asyncio
async def test_scheduler_recent_returns_none(uow):
    from core.opportunities.domain import OpportunityState

    op = OpportunityState(
        owner_id="mario",
        user_id="carlos",
    )
    await uow.opportunities.save(op)
    sched = ReactivationScheduler(uow.opportunities, uow.jobs)
    assert sched.classify_for_state(op) is None


@pytest.mark.asyncio
async def test_scheduler_creates_two_jobs_warm_and_urgent(service, uow):
    res = await service.process_message(
        owner_id="mario",
        content="Carlos dijo que lo piensa.",
    )
    pending = await uow.jobs.list_pending_for_owner("mario")
    urgencies = sorted(j.urgency.value for j in pending)
    assert urgencies == ["URGENT", "WARM"]

    # Sanity: due_at deltas
    op = await uow.opportunities.get(res.opportunity_id, "mario")
    for j in pending:
        delta = (j.due_at - op.last_activity).days
        if j.urgency == Urgency.WARM:
            assert delta == 3
        if j.urgency == Urgency.URGENT:
            assert delta == 7


# ---------------------------------------------------------------------------
# 9. Multi-tenant — no mezcla owner_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_owners_are_isolated(service, uow):
    await service.process_message(
        owner_id="mario",
        content="Carlos dijo que lo piensa.",
    )
    await service.process_message(
        owner_id="other_tenant",
        content="Carlos dijo que lo piensa.",
    )

    mario_opps = await uow.opportunities.list_for_owner("mario")
    other_opps = await uow.opportunities.list_for_owner("other_tenant")

    assert len(mario_opps) == 1
    assert len(other_opps) == 1
    assert mario_opps[0].id != other_opps[0].id

    # Get del de mario filtrando por otro owner debe devolver None
    foreign = await uow.opportunities.get(
        mario_opps[0].id, owner_id="other_tenant",
    )
    assert foreign is None


# ---------------------------------------------------------------------------
# 10. Eventos inmutables
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_events_are_appended_and_not_replaced(service, uow):
    res = await service.process_message(
        owner_id="mario",
        content="Carlos dijo que lo piensa.",
    )
    await service.process_message(
        owner_id="mario",
        content="Carlos hablamos la próxima.",
    )
    await service.process_message(
        owner_id="mario",
        content="Carlos ya pagué.",
    )

    events = await uow.events.list_for_opportunity(
        res.opportunity_id, "mario",
    )
    types = [e.event_type for e in events]
    # Created + Scheduled x2 + Updated + Closed
    assert types[0] == EventType.OPPORTUNITY_CREATED
    assert EventType.REACTIVATION_SCHEDULED in types
    assert EventType.OPPORTUNITY_UPDATED in types
    assert types[-1] == EventType.OPPORTUNITY_CLOSED

    # Garantía de inmutabilidad: orden ascendente por created_at
    timestamps = [e.created_at for e in events]
    assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# 11. Formatter — humano
# ---------------------------------------------------------------------------

def test_formatter_produces_human_message():
    from core.opportunities.domain import OpportunityState, IntentType

    base = datetime.now(timezone.utc) - timedelta(days=9)
    op = OpportunityState(
        owner_id="mario",
        user_id="carlos",
        contact_name="Carlos",
        intent_signal="lo pienso la próxima semana",
        intent_type=IntentType.DELAY,
        last_activity=base,
        created_at=base,
        updated_at=base,
    )
    text = humanize(op)
    assert "Carlos" in text
    assert "hace 9 días" in text
    assert "lo pienso" in text
    assert "ABIERTO" in text


def test_formatter_handles_closed():
    from core.opportunities.domain import OpportunityState

    op = OpportunityState(
        owner_id="mario",
        user_id="carlos",
        contact_name="Carlos",
        status=OpportunityStatus.CLOSED,
        intent_signal="ya pagué",
    )
    text = humanize(op)
    assert "CERRADO" in text
