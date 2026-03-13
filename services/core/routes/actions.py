"""POST /v1/actions/propose — generate a proposal remotely (auth required)."""

import uuid
import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from services.core.auth import AuthContext
from services.core.middleware.user_context import require_permission
from services.core.models import ProposeRequest, ProposeResponse

router = APIRouter(prefix="/actions", tags=["actions"])
logger = logging.getLogger("vectrax.core.actions")

# Ensure project root is importable
_project_root = str(Path(__file__).resolve().parents[3])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


@router.post("/propose", response_model=ProposeResponse)
async def propose_action(
    body: ProposeRequest,
    ctx: AuthContext = Depends(require_permission("propose")),
):
    """
    Generate a proposal via the existing ProposalEngine.
    Returns the proposal for human review — never auto-applies.
    """
    try:
        from core.proposal_engine import ProposalEngine

        engine = ProposalEngine(project_root=_project_root)

        try:
            proposal = await engine.propose(body.description, model=body.model)
        finally:
            await engine.close()

        proposal_id = str(uuid.uuid4())[:12]

        return ProposeResponse(
            proposal_id=proposal_id,
            description=body.description,
            files_affected=len(proposal.file_changes),
            risk_score=proposal.risk_score,
            risk_level=proposal.risk_level,
            allowed=proposal.allowed,
            rationale=proposal.rationale,
            diff=proposal.full_diff(),
            requires_confirmation=True,  # always
        )

    except RuntimeError as exc:
        # e.g. Ollama not running
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("Proposal generation failed")
        raise HTTPException(status_code=500, detail=str(exc))
