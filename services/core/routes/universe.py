"""
/v1/universe — Live heartbeat of the Vectrax cognitive universe.

Returns the nucleus state, all active stars, convergences, and
constellations. This is the "pulse" of the system.

/v1/universe/ws — WebSocket stream of the universe state in real time.

No auth required for basic status (read-only, no PII).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["universe"])
logger = logging.getLogger("vectrax.routes.universe")

_UNIVERSE_HTML = Path(__file__).resolve().parent.parent.parent / "ui" / "static" / "universe.html"

_WS_INTERVAL_S = 2.0  # Push interval for WebSocket stream


@router.get("/universe/view", response_class=HTMLResponse)
async def universe_view():
    """Visual rendering of the Vectrax universe (HTML/Canvas)."""
    if _UNIVERSE_HTML.exists():
        return HTMLResponse(_UNIVERSE_HTML.read_text())
    return HTMLResponse("<h1>universe.html not found</h1>", status_code=404)


def _build_snapshot() -> Dict[str, Any]:
    """Build the unified universe snapshot via universe_observer."""
    from core.self_observation.universe_observer import observe_universe
    return observe_universe().to_api_dict()


@router.get("/universe")
async def get_universe() -> Dict[str, Any]:
    """
    Live state of the Vectrax universe.

    Returns the unified snapshot: gravitational state (nucleus, stars,
    convergences) + operational state (worker, queue, memory, modes,
    health signals).
    """
    return _build_snapshot()


@router.websocket("/universe/ws")
async def universe_ws(ws: WebSocket):
    """WebSocket stream: pushes the universe snapshot every 2s."""
    await ws.accept()
    logger.info("Universe WS connected")
    try:
        while True:
            snapshot = _build_snapshot()
            await ws.send_json(snapshot)
            await asyncio.sleep(_WS_INTERVAL_S)
    except WebSocketDisconnect:
        logger.info("Universe WS disconnected")
    except Exception as exc:
        logger.debug("Universe WS error: %s", exc)


@router.get("/universe/star/{user_id}")
async def get_star_detail(user_id: str) -> Dict[str, Any]:
    """Detail of a single user-star including topic fingerprint."""
    from vectrax.db import get_user_star, get_pattern_count, get_topic_distribution

    star = get_user_star(user_id)
    if star is None:
        return {"error": "Star not found", "user_id": user_id}

    return {
        "star": star.to_dict(),
        "patterns": get_pattern_count(user_id),
        "topics": get_topic_distribution(user_id),
    }
