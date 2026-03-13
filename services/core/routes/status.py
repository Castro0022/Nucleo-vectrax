"""
Vectrax Core — Status Routes
================================
System introspection endpoints.

  GET /v1/status           — governor state, graph summary, memory counts
  GET /v1/status/operator  — 12-layer architecture status from nucleus
  GET /v1/status/audit     — recent audit ledger entries
"""

import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from services.core.auth import AuthContext
from services.core.middleware.user_context import require_permission
from services.core.models import (
    AuditEntryResponse,
    AuditListResponse,
    LayerInfo,
    OperatorStatusResponse,
    StatusResponse,
)

router = APIRouter(prefix="/status", tags=["status"])
logger = logging.getLogger("vectrax.core.routes.status")

_project_root = str(Path(__file__).resolve().parents[3])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


# ---------------------------------------------------------------------------
# GET /v1/status
# ---------------------------------------------------------------------------

@router.get("", response_model=StatusResponse)
async def system_status(
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """Return overall system status: governor, graph, memory counts."""
    # Governor state
    gov = {"mode": "unknown", "reason": "", "autopatch_allowed": False, "clean_streak": 0}
    try:
        from core.governor import get_current_policy
        gov = get_current_policy()
    except Exception:
        pass

    # Graph summary
    graph = {"nodes": 0, "edges": 0, "components": 0}
    try:
        from vectrax.graph import graph_summary
        graph = graph_summary()
    except Exception:
        pass

    # Memory counts
    counts = {"stars": 0, "constellations": 0, "pending_proposals": 0}
    try:
        from vectrax import db
        db.init_db()
        counts = db.get_counts()
    except Exception:
        pass

    return StatusResponse(
        governor_mode=gov.get("mode", "unknown"),
        governor_reason=gov.get("reason", ""),
        autopatch_allowed=gov.get("autopatch_allowed", False),
        clean_streak=gov.get("clean_streak", 0),
        graph_nodes=graph.get("nodes", 0),
        graph_edges=graph.get("edges", 0),
        graph_components=graph.get("components", 0),
        stars_total=counts.get("stars", 0),
        constellations_total=counts.get("constellations", 0),
        pending_proposals=counts.get("pending_proposals", 0),
    )


# ---------------------------------------------------------------------------
# GET /v1/status/operator
# ---------------------------------------------------------------------------

@router.get("/operator", response_model=OperatorStatusResponse)
async def operator_status(
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """Return the 12-layer operator architecture status from nucleus."""
    layers_info = []
    try:
        from core.operator.nucleus import LAYERS
        from core.operator.identity import IDENTITY

        for layer_def in LAYERS:
            layers_info.append(LayerInfo(
                id=layer_def.id,
                name=layer_def.name,
                status=layer_def.status.value,
                modules=layer_def.modules,
            ))

        mode = IDENTITY.get("default_mode", "guided")
        name = IDENTITY.get("name", "Vectrax")
    except Exception:
        mode = "guided"
        name = "Vectrax"

    active = sum(1 for l in layers_info if l.status == "active")

    return OperatorStatusResponse(
        operator_name=name,
        mode=mode,
        layers=layers_info,
        total_layers=len(layers_info),
        active_layers=active,
    )


# ---------------------------------------------------------------------------
# GET /v1/status/audit
# ---------------------------------------------------------------------------

@router.get("/audit", response_model=AuditListResponse)
async def audit_log(
    limit: int = Query(50, ge=1, le=500),
    action_filter: str = Query(None, description="Filter by action name"),
    ctx: AuthContext = Depends(require_permission("audit.read")),
):
    """Return recent entries from the audit ledger."""
    entries = []
    try:
        from core.audit_ledger import query as ledger_query
        raw = ledger_query(limit=limit, action_filter=action_filter)
        entries = [
            AuditEntryResponse(
                id=e.get("id", 0),
                timestamp=e.get("timestamp", ""),
                actor=e.get("actor", ""),
                role=e.get("role", ""),
                action=e.get("action", ""),
                decision=e.get("decision", ""),
                reason=e.get("reason", ""),
            )
            for e in raw
        ]
    except Exception:
        pass

    return AuditListResponse(entries=entries, total=len(entries))
