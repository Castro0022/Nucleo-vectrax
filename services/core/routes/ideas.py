"""
Vectrax Core — Ideas Routes
==============================
  GET  /v1/ideas              — lista ideas (filtrable por status)
  POST /v1/ideas/{id}/approve — aprueba una idea (solo creator/operator)
  POST /v1/ideas/{id}/reject  — rechaza una idea (solo creator/operator)
  POST /v1/ideas/refresh      — ingesta nuevas ideas de todos los módulos
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from services.core.auth import AuthContext
from services.core.middleware.user_context import require_permission

router = APIRouter(prefix="/ideas", tags=["ideas"])
logger = logging.getLogger("vectrax.core.routes.ideas")

_project_root = str(Path(__file__).resolve().parents[3])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


class ReviewBody(BaseModel):
    note: str = ""


# ---------------------------------------------------------------------------
# GET /v1/ideas
# ---------------------------------------------------------------------------

@router.get("")
async def list_ideas(
    status: Optional[str] = Query(None, description="pending|approved|rejected|applied|all"),
    limit:  int           = Query(20, ge=1, le=100),
    ctx:    AuthContext   = Depends(require_permission("core.read")),
):
    """Lista ideas del IdeaStore ordenadas por priority_score."""
    try:
        from core.idea_store import get_idea_store, IdeaStatus

        store = get_idea_store()

        if status and status != "all":
            try:
                st = IdeaStatus(status)
                ideas = [i for i in store.all() if i.status == st][:limit]
            except ValueError:
                raise HTTPException(400, f"status inválido: {status}")
        else:
            ideas = store.all()[:limit]

        return {
            "ideas": [i.to_dict() for i in ideas],
            "total": len(ideas),
            "stats": store.stats(),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("list_ideas error: %s", exc)
        raise HTTPException(500, str(exc))


# ---------------------------------------------------------------------------
# POST /v1/ideas/refresh
# ---------------------------------------------------------------------------

@router.post("/refresh")
async def refresh_ideas(
    ctx: AuthContext = Depends(require_permission("core.write")),
):
    """Fuerza ingesta de ideas desde todos los módulos de aprendizaje."""
    try:
        from core.idea_store import get_idea_store
        store = get_idea_store()
        added = store.refresh()
        total_new = sum(added.values())
        return {"added": added, "total_new": total_new}
    except Exception as exc:
        logger.error("refresh_ideas error: %s", exc)
        raise HTTPException(500, str(exc))


# ---------------------------------------------------------------------------
# POST /v1/ideas/{idea_id}/approve
# ---------------------------------------------------------------------------

@router.post("/{idea_id}/approve")
async def approve_idea(
    idea_id: str,
    body:    ReviewBody  = ReviewBody(),
    ctx:     AuthContext = Depends(require_permission("core.write")),
):
    """Aprueba una idea pendiente. Solo creator/operator."""
    try:
        from core.idea_store import get_idea_store
        store = get_idea_store()
        ok = store.approve(idea_id.upper(), by=ctx.user_id, note=body.note)
        if not ok:
            raise HTTPException(404, f"Idea {idea_id} no encontrada o ya revisada")
        idea = store.get_by_id(idea_id.upper())
        return {"approved": True, "idea": idea.to_dict() if idea else {}}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("approve_idea error: %s", exc)
        raise HTTPException(500, str(exc))


# ---------------------------------------------------------------------------
# POST /v1/ideas/{idea_id}/reject
# ---------------------------------------------------------------------------

@router.post("/{idea_id}/reject")
async def reject_idea(
    idea_id: str,
    body:    ReviewBody  = ReviewBody(),
    ctx:     AuthContext = Depends(require_permission("core.write")),
):
    """Rechaza una idea pendiente. Solo creator/operator."""
    try:
        from core.idea_store import get_idea_store
        store = get_idea_store()
        ok = store.reject(idea_id.upper(), by=ctx.user_id, note=body.note)
        if not ok:
            raise HTTPException(404, f"Idea {idea_id} no encontrada o ya revisada")
        idea = store.get_by_id(idea_id.upper())
        return {"rejected": True, "idea": idea.to_dict() if idea else {}}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("reject_idea error: %s", exc)
        raise HTTPException(500, str(exc))
