"""
Vectrax Core — Proposal Routes
==================================
CRUD for structural improvement proposals stored in vectrax.db.

  GET  /v1/proposals           — list proposals (optionally filter by status)
  GET  /v1/proposals/{id}      — single proposal detail
  POST /v1/proposals/{id}/approve — approve a pending proposal (owner/operator)
  POST /v1/proposals/{id}/reject  — reject a pending proposal (owner/operator)
"""

import logging
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from services.core.auth import AuthContext
from services.core.middleware.user_context import require_permission
from services.core.models import (
    ProposalActionRequest,
    ProposalActionResponse,
    ProposalDetail,
    ProposalListResponse,
)

router = APIRouter(prefix="/proposals", tags=["proposals"])
logger = logging.getLogger("vectrax.core.routes.proposals")

_project_root = str(Path(__file__).resolve().parents[3])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


# ---------------------------------------------------------------------------
# GET /v1/proposals
# ---------------------------------------------------------------------------

@router.get("", response_model=ProposalListResponse)
async def list_proposals(
    status: Optional[str] = Query(None, description="Filter: pending, approved, rejected"),
    limit: int = Query(100, ge=1, le=1000),
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """List proposals. Owner sees all; other roles see only pending."""
    from vectrax import db
    db.init_db()

    proposals = db.get_proposals(status=status)
    items = [
        ProposalDetail(
            id=p.id,
            constellation_id=p.constellation_id,
            description=p.description,
            evidence=p.evidence,
            status=p.status,
            created_at=p.created_at,
        )
        for p in proposals[:limit]
    ]
    return ProposalListResponse(proposals=items, total=len(items))


# ---------------------------------------------------------------------------
# GET /v1/proposals/{proposal_id}
# ---------------------------------------------------------------------------

@router.get("/{proposal_id}", response_model=ProposalDetail)
async def get_proposal(
    proposal_id: str,
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """Get a single proposal by ID."""
    from vectrax import db
    db.init_db()

    p = db.get_proposal(proposal_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Proposal not found")

    return ProposalDetail(
        id=p.id,
        constellation_id=p.constellation_id,
        description=p.description,
        evidence=p.evidence,
        status=p.status,
        created_at=p.created_at,
    )


# ---------------------------------------------------------------------------
# POST /v1/proposals/{proposal_id}/approve
# ---------------------------------------------------------------------------

@router.post("/{proposal_id}/approve", response_model=ProposalActionResponse)
async def approve_proposal(
    proposal_id: str,
    body: ProposalActionRequest = ProposalActionRequest(),
    ctx: AuthContext = Depends(require_permission("apply_proposal")),
):
    """Approve a pending proposal. Requires apply_proposal permission."""
    from vectrax import db
    db.init_db()

    p = db.get_proposal(proposal_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if p.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Proposal is already '{p.status}', cannot approve",
        )

    db.update_proposal_status(proposal_id, "approved")

    # Record in audit ledger (best-effort)
    try:
        from core.audit_ledger import record
        record(
            action=f"proposal.approved:{proposal_id}",
            actor=ctx.username,
            role=ctx.role,
            decision="approved",
            reason=body.reason or "Approved via API",
        )
    except Exception:
        pass

    logger.info(
        "Proposal %s approved by %s (role=%s)", proposal_id, ctx.username, ctx.role
    )
    return ProposalActionResponse(
        id=proposal_id,
        status="approved",
        message="Proposal approved",
    )


# ---------------------------------------------------------------------------
# POST /v1/proposals/{proposal_id}/reject
# ---------------------------------------------------------------------------

@router.post("/{proposal_id}/reject", response_model=ProposalActionResponse)
async def reject_proposal(
    proposal_id: str,
    body: ProposalActionRequest = ProposalActionRequest(),
    ctx: AuthContext = Depends(require_permission("apply_proposal")),
):
    """Reject a pending proposal. Requires apply_proposal permission."""
    from vectrax import db
    db.init_db()

    p = db.get_proposal(proposal_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if p.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Proposal is already '{p.status}', cannot reject",
        )

    db.update_proposal_status(proposal_id, "rejected")

    try:
        from core.audit_ledger import record
        record(
            action=f"proposal.rejected:{proposal_id}",
            actor=ctx.username,
            role=ctx.role,
            decision="rejected",
            reason=body.reason or "Rejected via API",
        )
    except Exception:
        pass

    logger.info(
        "Proposal %s rejected by %s (role=%s)", proposal_id, ctx.username, ctx.role
    )
    return ProposalActionResponse(
        id=proposal_id,
        status="rejected",
        message="Proposal rejected",
    )
