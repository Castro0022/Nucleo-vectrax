"""GET /v1/health — no auth required."""

import time

from fastapi import APIRouter

from services.core.config import get_settings
from services.core.models import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health():
    settings = get_settings()

    # Uptime from app module
    try:
        from services.core.app import get_uptime
        uptime = get_uptime()
    except Exception:
        uptime = 0.0

    # Component checks
    components = {"api": "ok"}

    # DB connectivity
    try:
        from vectrax.db import _get_conn
        conn = _get_conn()
        conn.execute("SELECT 1")
        conn.close()
        components["database"] = "ok"
    except Exception:
        components["database"] = "degraded"

    # Governor state
    governor_mode = None
    governor_reason = None
    try:
        from core.governor import get_current_policy
        policy = get_current_policy()
        governor_mode = policy.get("mode", "unknown")
        governor_reason = policy.get("reason", "")
        components["governor"] = governor_mode
    except Exception:
        components["governor"] = "unavailable"

    return HealthResponse(
        status="ok",
        version="1.0.0",
        env=settings.env,
        uptime_seconds=uptime,
        components=components,
        governor_mode=governor_mode,
        governor_reason=governor_reason,
    )
