"""
Vectrax Core — Memory Routes
================================
Read-only endpoints for memory graph data (stars, constellations, history).
All queries are scoped to the authenticated user's channel/owner.

  GET /v1/memory/stars
  GET /v1/memory/stars/{star_id}
  GET /v1/memory/constellations
  GET /v1/memory/history
  GET /v1/memory/sessions
  GET /v1/memory/stats
"""

import logging
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from services.core.auth import AuthContext
from services.core.middleware.user_context import require_permission
from services.core.models import (
    ConstellationListResponse,
    ConstellationResponse,
    MemoryStatsResponse,
    StarListResponse,
    StarResponse,
)

router = APIRouter(prefix="/memory", tags=["memory"])
logger = logging.getLogger("vectrax.core.routes.memory")

_project_root = str(Path(__file__).resolve().parents[3])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


# ---------------------------------------------------------------------------
# GET /v1/memory/stars
# ---------------------------------------------------------------------------

@router.get("/stars", response_model=StarListResponse)
async def list_stars(
    layer: Optional[str] = Query(None, description="Filter by layer: core, mid, outer"),
    limit: int = Query(100, ge=1, le=1000),
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """List stars scoped to the authenticated user's channel/owner."""
    from vectrax import db
    db.init_db()

    stars = db.get_all_stars(layer=layer, channel=ctx.channel, owner=ctx.owner)
    items = [
        StarResponse(
            id=s.id, content=s.content, timestamp=s.timestamp,
            layer=s.layer, gravity_score=round(s.gravity_score, 4),
            repetition_count=s.repetition_count,
            success_count=s.success_count, total_count=s.total_count,
            channel=s.channel, owner=s.owner,
        )
        for s in stars[:limit]
    ]
    return StarListResponse(stars=items, total=len(items))


# ---------------------------------------------------------------------------
# GET /v1/memory/stars/{star_id}
# ---------------------------------------------------------------------------

@router.get("/stars/{star_id}", response_model=StarResponse)
async def get_star(
    star_id: str,
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """Get a single star by ID. Enforces channel/owner scope."""
    from vectrax import db
    db.init_db()

    star = db.get_star(star_id)
    if star is None:
        raise HTTPException(status_code=404, detail="Star not found")

    # Enforce channel scope (owner can see their own; owner role sees all)
    if ctx.role != "owner" and (star.channel != ctx.channel or star.owner != ctx.owner):
        raise HTTPException(status_code=403, detail="Access denied to this star")

    return StarResponse(
        id=star.id, content=star.content, timestamp=star.timestamp,
        layer=star.layer, gravity_score=round(star.gravity_score, 4),
        repetition_count=star.repetition_count,
        success_count=star.success_count, total_count=star.total_count,
        channel=star.channel, owner=star.owner,
    )


# ---------------------------------------------------------------------------
# GET /v1/memory/constellations
# ---------------------------------------------------------------------------

@router.get("/constellations", response_model=ConstellationListResponse)
async def list_constellations(
    limit: int = Query(100, ge=1, le=1000),
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """List constellations scoped to the authenticated user's channel/owner."""
    from vectrax import db
    db.init_db()

    consts = db.get_all_constellations(channel=ctx.channel, owner=ctx.owner)
    items = [
        ConstellationResponse(
            id=c.id, member_count=c.member_count,
            coherence_score=round(c.coherence_score, 4),
            repetition_count=c.repetition_count,
            success_rate=round(c.success_rate, 4),
            gravity_score=round(c.gravity_score, 4),
            channel=c.channel, owner=c.owner,
        )
        for c in consts[:limit]
    ]
    return ConstellationListResponse(constellations=items, total=len(items))


# ---------------------------------------------------------------------------
# GET /v1/memory/history
# ---------------------------------------------------------------------------

@router.get("/history")
async def get_history(
    limit: int = Query(50, ge=1, le=500),
    session_id: Optional[str] = Query(None),
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """Return conversation history. Optionally filtered by session_id."""
    from vectrax import db
    db.init_db()

    if session_id:
        messages = db.get_session_messages(session_id)
    else:
        messages = db.get_recent_messages(limit=limit)

    return {"messages": messages, "total": len(messages)}


# ---------------------------------------------------------------------------
# GET /v1/memory/sessions
# ---------------------------------------------------------------------------

@router.get("/sessions")
async def list_sessions(
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """Return all conversation sessions with message counts."""
    from vectrax import db
    db.init_db()

    sessions = db.get_sessions()
    return {"sessions": sessions, "total": len(sessions)}


# ---------------------------------------------------------------------------
# GET /v1/memory/stats
# ---------------------------------------------------------------------------

@router.get("/stats", response_model=MemoryStatsResponse)
async def get_stats(
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """Return aggregate memory statistics."""
    from vectrax import db
    db.init_db()

    counts = db.get_counts()
    return MemoryStatsResponse(
        stars=counts.get("stars", 0),
        constellations=counts.get("constellations", 0),
        pending_proposals=counts.get("pending_proposals", 0),
        layers=counts.get("layers", {}),
    )
