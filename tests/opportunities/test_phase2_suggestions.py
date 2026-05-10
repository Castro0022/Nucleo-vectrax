"""
tests/opportunities/test_phase2_suggestions.py
================================================
Cubre la Fase 2: surfacing de reactivaciones due al LLM.

Verifica:
    * pending_suggestions devuelve solo jobs realmente vencidos
    * marca esos jobs como PROCESSED (idempotente — no re-fire)
    * emite REACTIVATION_TRIGGERED inmutable
    * aislamiento por owner_id
    * si la oportunidad fue cerrada, el job se descarta (no se sugiere)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from core.opportunities import (
    EventType,
    JobStatus,
    OpportunityService,
    OpportunityStatus,
    ReactivationScheduler,
    SqliteUnitOfWork,
    Urgency,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def uow(tmp_path):
    db_path = tmp_path / "p2.db"
    uow = SqliteUnitOfWork(f"sqlite+aiosqlite:///{db_path}")
    await uow.init_schema()
    yield uow
    await uow.dispose()


@pytest_asyncio.fixture
async def service(uow):
    sched = ReactivationScheduler(uow.opportunities, uow.jobs)
    return OpportunityService(
        opportunities=uow.opportunities,
        events=uow.events,
        jobs=uow.jobs,
        scheduler=sched,
    )


# ---------------------------------------------------------------------------
# 1. No suggestions when nothing is due
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_suggestions_when_no_due_jobs(service):
    await service.process_message(
        owner_id="mario",
        content="Carlos dijo que lo piensa.",
    )
    # Jobs reci\u00e9n creados \u2014 due_at est\u00e1 en el futuro.
    sugs = await service.pending_suggestions_for_owner("mario")
    assert sugs == []


# ---------------------------------------------------------------------------
# 2. Returns formatted suggestion when WARM is due
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_returns_suggestion_when_warm_due(service):
    await service.process_message(
        owner_id="mario",
        content="Carlos dijo que lo piensa.",
    )
    # Avanzar el reloj 4 d\u00edas: WARM ya venci\u00f3, URGENT no.
    future = datetime.now(timezone.utc) + timedelta(days=4)
    sugs = await service.pending_suggestions_for_owner(
        "mario", now=future,
    )
    assert len(sugs) == 1
    text = sugs[0]
    assert "Carlos" in text
    assert "tibio" in text or "ABIERTO" in text


# ---------------------------------------------------------------------------
# 3. Marks job as PROCESSED (idempotent)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_marks_job_processed_and_does_not_refire(service, uow):
    await service.process_message(
        owner_id="mario",
        content="Carlos dijo que lo piensa.",
    )
    future = datetime.now(timezone.utc) + timedelta(days=4)
    sugs1 = await service.pending_suggestions_for_owner("mario", now=future)
    assert len(sugs1) == 1

    sugs2 = await service.pending_suggestions_for_owner("mario", now=future)
    assert sugs2 == []  # Mismo punto temporal: WARM ya consumido.


# ---------------------------------------------------------------------------
# 4. Emits REACTIVATION_TRIGGERED event (immutable)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emits_triggered_event(service, uow):
    res = await service.process_message(
        owner_id="mario",
        content="Carlos dijo que lo piensa.",
    )
    future = datetime.now(timezone.utc) + timedelta(days=4)
    await service.pending_suggestions_for_owner("mario", now=future)

    events = await uow.events.list_for_opportunity(
        res.opportunity_id, "mario",
    )
    triggered = [e for e in events if e.event_type == EventType.REACTIVATION_TRIGGERED]
    assert len(triggered) == 1
    assert triggered[0].payload.get("urgency") == "WARM"


# ---------------------------------------------------------------------------
# 5. Multi-tenant isolation in suggestions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_suggestions_isolated_per_owner(service):
    await service.process_message(
        owner_id="mario",
        content="Carlos dijo que lo piensa.",
    )
    await service.process_message(
        owner_id="other",
        content="Ana dijo que lo piensa.",
    )

    future = datetime.now(timezone.utc) + timedelta(days=4)
    mario_sugs = await service.pending_suggestions_for_owner(
        "mario", now=future,
    )
    other_sugs = await service.pending_suggestions_for_owner(
        "other", now=future,
    )

    assert len(mario_sugs) == 1
    assert len(other_sugs) == 1
    assert "Carlos" in mario_sugs[0]
    assert "Ana" in other_sugs[0]
    assert "Carlos" not in other_sugs[0]
    assert "Ana" not in mario_sugs[0]


# ---------------------------------------------------------------------------
# 6. Closed opportunity \u2014 jobs skipped, no suggestions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_closed_opportunities_do_not_surface(service, uow):
    await service.process_message(
        owner_id="mario",
        content="Carlos dijo que lo piensa.",
    )
    await service.process_message(
        owner_id="mario",
        content="Carlos ya pagu\u00e9.",
    )
    # Aunque hubiera job programado, al cerrarse fue cancelado.
    future = datetime.now(timezone.utc) + timedelta(days=4)
    sugs = await service.pending_suggestions_for_owner("mario", now=future)
    assert sugs == []


# ---------------------------------------------------------------------------
# 7. URGENT only when 7+ days passed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_urgent_surfaces_after_seven_days(service):
    await service.process_message(
        owner_id="mario",
        content="Carlos dijo que lo piensa.",
    )
    # Saltar a +8 d\u00edas: ambos jobs (WARM y URGENT) vencidos.
    future = datetime.now(timezone.utc) + timedelta(days=8)
    sugs = await service.pending_suggestions_for_owner(
        "mario", max_items=5, now=future,
    )
    assert len(sugs) == 2
    # El m\u00e1s urgente apunta a 'urgente' o 'sin seguimiento'
    joined = " ".join(sugs).lower()
    assert "urgente" in joined or "sin seguimiento" in joined
