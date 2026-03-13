"""POST /v1/events — agent event ingestion (auth required, multi-user)."""

import uuid
import logging

from fastapi import APIRouter, Depends

from services.core.auth import AuthContext, require_token
from services.core.middleware.user_context import require_permission
from services.core.models import EventPayload, EventResponse

router = APIRouter(prefix="/events", tags=["events"])
logger = logging.getLogger("vectrax.core.events")

# In-memory event store (replace with persistent store later)
_events: list = []


@router.post("", response_model=EventResponse)
async def ingest_event(
    body: EventPayload,
    ctx: AuthContext = Depends(require_permission("event.write")),
):
    """Accept an event from an agent and store it."""
    event_id = str(uuid.uuid4())[:12]

    record = {
        "event_id": event_id,
        "event_type": body.event_type.value,
        "agent_id": body.agent_id,
        "timestamp": body.timestamp,
        "data": body.data,
        # Multi-user: associate event with authenticated identity
        "user_id": ctx.user_id,
        "username": ctx.username,
        "channel": ctx.channel,
    }

    _events.append(record)

    # Keep bounded
    if len(_events) > 10_000:
        _events[:] = _events[-5_000:]

    logger.info(
        "Event ingested: id=%s type=%s agent=%s user=%s",
        event_id,
        body.event_type.value,
        body.agent_id,
        ctx.username,
    )

    return EventResponse(
        accepted=True,
        event_id=event_id,
        message=f"Event {body.event_type.value} accepted",
    )


def get_events() -> list:
    """Accessor for tests and internal use."""
    return list(_events)
