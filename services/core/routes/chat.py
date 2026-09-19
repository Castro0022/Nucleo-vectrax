"""
POST /v1/chat — Sovereign Cognitive Chat endpoint (auth required, multi-user).

Vectrax investigates silently (memory, web, future AI engines) and
returns a single clean answer to the user.  Internal pipeline details
(sources, engines, fallback) are stored for audit/debug only.
"""

import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from services.core.auth import AuthContext
from services.core.middleware.user_context import require_permission
from services.core.models import ChatRequest, ChatResponse, SourceItem

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger("vectrax.core.routes.chat")

# Ensure project root importable
_project_root = str(Path(__file__).resolve().parents[3])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


@router.post("", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    ctx: AuthContext = Depends(require_permission("event.write")),
):
    """
    Sovereign Chat — ADAPTADOR DE I/O PURO (reunificación 2026-09-19).

    Este endpoint YA NO clasifica ni decide nada: delega íntegramente en
    `core.nucleus.nucleus_authority.NucleusAuthority`, la misma autoridad de
    decisión que usa el canal de Telegram (`core/transport/pipeline_worker.py`).
    `vectrax.resolver.classify()` dejó de ser un decisor de producto — sus
    funciones `resolve_local()`/`resolve_online()` ahora son ejecutores puros,
    invocados únicamente por orden del Núcleo (ver `nucleus_authority.py`).
    """
    try:
        from vectrax import db
        from core.nucleus.nucleus_authority import get_nucleus_authority

        db.init_db()

        # ── AUTORIDAD ÚNICA: el Núcleo decide, ejecuta y autoriza evidencia ──
        # (incluye internamente el ciclo de convergencia total, fases 1-7,
        # y la consulta auxiliar a SmartRouter cuando el Núcleo no tiene
        # evidencia/capacidad propia suficiente — ver nucleus_authority.py)
        nr = get_nucleus_authority().resolve(
            body.text, channel=ctx.channel, owner=ctx.owner, source="api",
        )
        # ─────────────────────────────────────────────────────────────────────

        # ---- MEMORY: mismo contrato de respuesta que antes ------------------
        if nr.final_action == "MEMORY":
            star_id = (nr.evidence or {}).get("star_id", "")
            repetition_count = (nr.evidence or {}).get("repetition_count", 1)
            _log_learning(body.text, "memory", [], "", ctx.channel, ctx.owner)
            _shadow_observe(body.text, "memory", nr.answer, ctx.owner)
            _maybe_speak(nr.answer)
            return ChatResponse(
                star_id=star_id,
                content=body.text,
                channel=ctx.channel,
                owner=ctx.owner,
                is_duplicate=repetition_count > 1,
                message="Star created" if repetition_count == 1 else "Star updated (near-duplicate)",
                resolve_mode="memory",
                sovereign_answer=nr.answer,
            )

        # ---- LOCAL/IDENTITY/ONLINE/PLACES/MARKET/CLARIFICATION --------------
        mode = nr.final_action.lower()
        sources = [
            SourceItem(title=t, url="", snippet="")
            for t in (nr.evidence or {}).get("sources", [])
        ]

        if nr.final_action == "ONLINE":
            _audit_online(
                ctx, body.text, body.text, len(sources),
                action="chat.online_research",
            )

        _log_learning(
            body.text, mode, [], nr.answer[:100], ctx.channel, ctx.owner,
        )
        _shadow_observe(body.text, mode, nr.answer, ctx.owner)
        _maybe_speak(nr.answer)

        return ChatResponse(
            content=body.text,
            channel=ctx.channel,
            owner=ctx.owner,
            message=f"Resolved via nucleus:{mode}",
            resolve_mode=mode,
            sovereign_answer=nr.answer,
            answer=nr.answer,
            sources=sources,
            context_stars=(nr.evidence or {}).get("context_stars", 0),
            search_query=body.text if nr.final_action == "ONLINE" else "",
            fallback_from="",
        )

    except Exception as exc:
        logger.exception("Chat processing failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _audit_online(
    ctx: AuthContext, user_text: str, query: str, n_sources: int,
    action: str = "chat.online_research",
) -> None:
    """Record an audit entry when online research is performed."""
    try:
        from core.audit_ledger import record
        record(
            actor=ctx.owner,
            role=ctx.role,
            action=action,
            decision="executed",
            reason=f"query='{query}' sources={n_sources}",
        )
    except Exception as exc:
        logger.warning("Audit record failed (non-fatal): %s", exc)


def _maybe_speak(text: str) -> None:
    """Speak the answer via macOS TTS when VX_CHAT_VOICE=1 (gated, non-fatal).

    core_api corre localmente, así que ``say`` suena en el Mac. Desactivado por
    defecto para respetar la preferencia de "sin audio"; se activa con el flag.
    """
    try:
        import os
        if os.environ.get("VX_CHAT_VOICE", "0") != "1" or not (text or "").strip():
            return
        from vectrax.comm.voice import speak_core_async
        speak_core_async(text)
    except Exception as exc:
        logger.debug("chat voice skipped: %s", exc)


def _shadow_observe(text: str, mode: str, resp_text: str, owner: str) -> None:
    """Gravity Kernel shadow observation (read-only, gated, never raises).

    No altera la respuesta del API. Gateada por VX_GRAVITY_KERNEL_SHADOW.
    """
    try:
        from core import gravity_kernel
        if gravity_kernel.is_enabled():
            gravity_kernel.observe(
                content=text,
                user_id=owner or "",
                result_source=mode,
                response_sent=bool(resp_text),
                response_len=len(resp_text or ""),
                source="web_chat",
            )
    except Exception as exc:
        logger.debug("gravity_kernel shadow skipped (web_chat): %s", exc)


def _log_learning(
    question: str, pattern: str, engines: list,
    consensus: str, channel: str, owner: str,
) -> None:
    """Store resolution pattern for silent learning."""
    try:
        from vectrax.db import log_resolution
        log_resolution(
            question=question,
            resolve_pattern=pattern,
            engines_used=engines,
            consensus=consensus,
            channel=channel,
            owner=owner,
        )
    except Exception as exc:
        logger.warning("Learning log failed (non-fatal): %s", exc)
