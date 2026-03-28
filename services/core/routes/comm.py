"""
Vectrax Core — Communication Engine Routes
==============================================
Two isolated communication channels exposed via API:

  /v1/comm/core/*   — Mario ↔ Nucleus (voice + text, owner-only)
  /v1/comm/star/*   — User ↔ own Star (text, any authenticated user)

All endpoints enforce RBAC via the existing auth middleware.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from services.core.auth import AuthContext, require_role, require_token
from services.core.middleware.user_context import require_permission

# Ensure project root importable
_project_root = str(Path(__file__).resolve().parents[3])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

router = APIRouter(prefix="/comm", tags=["communication"])
logger = logging.getLogger("vectrax.core.routes.comm")


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class CommTextRequest(BaseModel):
    text: str
    success: bool = False


class CommResponse(BaseModel):
    message_id: str = ""
    text: str = ""
    star_id: str = ""
    layer: str = ""
    gravity_score: float = 0.0
    resolve_mode: str = "memory"
    context_stars: int = 0
    session_id: str = ""
    owner: str = ""
    source_type: str = "text"
    engine_type: str = ""


class CommContextResponse(BaseModel):
    channel: str = ""
    owner: str = ""
    stars_total: int = 0
    constellations_total: int = 0
    layers: dict = Field(default_factory=dict)
    avg_gravity: float = 0.0
    engine_type: str = ""
    capabilities: list = Field(default_factory=list)
    pending_proposals: int = 0
    nucleus_projection: dict = Field(default_factory=dict)
    navigation: dict = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# CORE ENDPOINTS — Mario ↔ Nucleus (owner-only)
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/core/text", response_model=CommResponse)
async def core_send_text(
    body: CommTextRequest,
    ctx: AuthContext = Depends(require_role("owner")),
):
    """Send a text message from Mario to the nucleus."""
    try:
        from vectrax.comm.engine_core import CoreCommEngine

        engine = CoreCommEngine()
        result = engine.send_text(body.text, success=body.success)

        return CommResponse(
            message_id=result.message_id,
            text=result.text,
            star_id=result.star_id,
            layer=result.layer,
            gravity_score=result.gravity_score,
            resolve_mode=result.resolve_mode,
            context_stars=result.context_stars,
            session_id=result.session_id,
            owner=result.to_dict().get("owner", "mario"),
            source_type="text",
            engine_type="core",
        )
    except Exception as exc:
        logger.exception("Core text failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/core/voice", response_model=CommResponse)
async def core_send_voice(
    audio: UploadFile = File(...),
    ctx: AuthContext = Depends(require_role("owner")),
):
    """
    Send a voice message from Mario to the nucleus.

    Accepts audio files (wav, mp3, m4a, ogg, webm).
    Transcribes via Whisper, then processes as text.
    """
    allowed = {".wav", ".mp3", ".m4a", ".ogg", ".webm", ".flac"}
    ext = Path(audio.filename or "audio.wav").suffix.lower()
    if ext not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format '{ext}'. Allowed: {', '.join(sorted(allowed))}",
        )

    # Save to temp file
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            content = await audio.read()
            tmp.write(content)
            tmp_path = tmp.name

        from vectrax.comm.engine_core import CoreCommEngine

        engine = CoreCommEngine()
        result = engine.send_voice(tmp_path)

        return CommResponse(
            message_id=result.message_id,
            text=result.text,
            star_id=result.star_id,
            layer=result.layer,
            gravity_score=result.gravity_score,
            resolve_mode=result.resolve_mode,
            context_stars=result.context_stars,
            session_id=result.session_id,
            owner="mario",
            source_type="voice",
            engine_type="core",
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("Core voice failed")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        # Cleanup temp file
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@router.get("/core/history")
async def core_get_history(
    limit: int = Query(50, ge=1, le=500),
    ctx: AuthContext = Depends(require_role("owner")),
):
    """Return Mario's conversation history with the nucleus."""
    from vectrax.comm.engine_core import CoreCommEngine

    engine = CoreCommEngine()
    messages = engine.get_history(limit=limit)
    return {"messages": messages, "total": len(messages), "engine_type": "core"}


@router.get("/core/context", response_model=CommContextResponse)
async def core_get_context(
    ctx: AuthContext = Depends(require_role("owner")),
):
    """Return full nucleus context (owner-only)."""
    from vectrax.comm.engine_core import CoreCommEngine

    engine = CoreCommEngine()
    context = engine.get_context()
    return CommContextResponse(**context)


# ═══════════════════════════════════════════════════════════════════════════
# STAR ENDPOINTS — User ↔ own Star
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/star/text", response_model=CommResponse)
async def star_send_text(
    body: CommTextRequest,
    ctx: AuthContext = Depends(require_permission("event.write")),
):
    """Send a text message from the user into their own star."""
    try:
        from vectrax.comm.engine_star import StarCommEngine

        engine = StarCommEngine(owner=ctx.owner)
        result = engine.send_text(body.text, success=body.success)

        return CommResponse(
            message_id=result.message_id,
            text=result.text,
            star_id=result.star_id,
            layer=result.layer,
            gravity_score=result.gravity_score,
            resolve_mode=result.resolve_mode,
            context_stars=result.context_stars,
            session_id=result.session_id,
            owner=result.owner,
            source_type="text",
            engine_type="star",
        )
    except Exception as exc:
        logger.exception("Star text failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/star/history")
async def star_get_history(
    limit: int = Query(50, ge=1, le=500),
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """Return the user's conversation history within their star."""
    from vectrax.comm.engine_star import StarCommEngine

    engine = StarCommEngine(owner=ctx.owner)
    messages = engine.get_history(limit=limit)
    return {
        "messages": messages,
        "total": len(messages),
        "owner": ctx.owner,
        "engine_type": "star",
    }


@router.get("/star/context", response_model=CommContextResponse)
async def star_get_context(
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """
    Return the user's star context plus a structural projection
    of the nucleus (metadata only, never creator content).
    """
    from vectrax.comm.engine_star import StarCommEngine

    engine = StarCommEngine(owner=ctx.owner)
    context = engine.get_context()
    return CommContextResponse(**context)
