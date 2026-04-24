"""
GET /v1/recovery/status — read-only snapshot of the recovery engine.

Exposes:
  - engine_running: bool
  - detectors: dict {detector_id: last_event_dict or None}
  - Each last_event has state OK/DEGRADED/BROKEN and evidence.

No auth required — exposes no secrets (evidence dicts do include
env path and other paths, but no token/secret values).

If the recovery engine is disabled (RESILIENCE_ENABLED != "1"),
returns a minimal response with engine_running=False.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter(prefix="/recovery", tags=["recovery"])
logger = logging.getLogger("vectrax.core.routes.recovery")

# Singleton references; set by startup hook in app.py
_orchestrator = None
_executor = None


def set_orchestrator(orch) -> None:
    """Called by app.py startup hook to register the HealthOrchestrator."""
    global _orchestrator
    _orchestrator = orch


def set_executor(executor) -> None:
    """Called by app.py startup hook to register the RecoveryExecutor
    (needed to report dry_run state in /status)."""
    global _executor
    _executor = executor


@router.get("/status")
async def recovery_status() -> Dict[str, Any]:
    """Snapshot of the recovery engine.  Read-only."""
    if _orchestrator is None:
        return {"engine_running": False, "mode": "disabled"}
    dry_run = bool(getattr(_executor, "_dry_run_global", False)) if _executor else False
    return {
        "engine_running": bool(_orchestrator.is_running()),
        "mode": "dry_run" if dry_run else "enabled",
        "detectors": _orchestrator.snapshot(),
    }
