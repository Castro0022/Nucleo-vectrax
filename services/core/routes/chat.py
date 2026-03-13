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
    Sovereign Chat: classify → investigate silently → respond cleanly.

    - MEMORY: ingest as star, confirm briefly.
    - LOCAL/ONLINE: resolve and return clean sovereign answer.
    - Internal details (sources, mode, engines) stored for audit/debug only.
    """
    try:
        from vectrax import db
        from vectrax.engine import ingest
        from vectrax.resolver import resolve, _detect_lang, _get_labels

        db.init_db()

        # ---- Resolve --------------------------------------------------------
        resolution = resolve(body.text, ctx.channel, ctx.owner)
        mode = resolution.mode  # "memory" | "local" | "online"

        # ---- MEMORY mode: ingest as before ----------------------------------
        if mode == "memory":
            star = ingest(
                text=body.text,
                success=body.success,
                channel=ctx.channel,
                owner=ctx.owner,
            )
            lang = _detect_lang(body.text)
            labels = _get_labels(lang)
            sovereign = labels["registered"] if star.repetition_count == 1 else labels["updated"]
            # Log learning
            _log_learning(body.text, "memory", [], "", ctx.channel, ctx.owner)

            return ChatResponse(
                star_id=star.id,
                content=star.content,
                layer=star.layer,
                gravity_score=round(star.gravity_score, 4),
                repetition_count=star.repetition_count,
                channel=star.channel,
                owner=star.owner,
                is_duplicate=star.repetition_count > 1,
                message="Star created" if star.repetition_count == 1 else "Star updated (near-duplicate)",
                resolve_mode="memory",
                sovereign_answer=sovereign,
            )

        # ---- LOCAL / ONLINE mode: return sovereign answer -------------------
        sources = [
            SourceItem(title=s.title, url=s.url, snippet=s.snippet)
            for s in resolution.sources
        ]

        # Audit online research (including fallback escalations)
        if mode == "online":
            action = "chat.online_research"
            if resolution.fallback_from:
                action = "chat.fallback_local_to_online"
            _audit_online(ctx, body.text, resolution.search_query, len(sources), action=action)

        # Determine resolution pattern for learning
        pattern = mode
        if resolution.fallback_from:
            pattern = f"{resolution.fallback_from}→{mode}"

        # Log learning
        _log_learning(
            body.text, pattern,
            resolution.engines_used,
            resolution.sovereign_answer[:100],
            ctx.channel, ctx.owner,
        )

        return ChatResponse(
            content=body.text,
            channel=ctx.channel,
            owner=ctx.owner,
            message=f"Resolved via {mode}",
            resolve_mode=mode,
            sovereign_answer=resolution.sovereign_answer,
            answer=resolution.answer,
            sources=sources,
            context_stars=resolution.context_stars,
            search_query=resolution.search_query,
            fallback_from=resolution.fallback_from,
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
