"""
/v1/domain — Domain Knowledge Library API
============================================
Read-only endpoints for inspecting the domain knowledge library.

GET /v1/domain/list                — list all domains with libraries
GET /v1/domain/{domain}/knowledge  — patterns for a specific domain
"""
from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(prefix="/domain", tags=["domain-knowledge"])


def _require_creator(api_key: str) -> None:
    """Validate that the caller is the creator."""
    creator_key = os.environ.get("VX_CREATOR_KEY", "")
    if creator_key and api_key == creator_key:
        return
    try:
        from core.tenant import validate_key
        tenant = validate_key(api_key)
        if tenant and tenant.plan == "creator":
            return
    except Exception:
        pass
    raise HTTPException(status_code=403, detail="Creator access required")


@router.get("/list")
async def list_domains_endpoint(
    x_api_key: str = Header("", alias="x-api-key"),
) -> Dict[str, Any]:
    """List all domains that have a knowledge library."""
    _require_creator(x_api_key)
    from core.domain_knowledge import list_domains, get_domain_summary
    domains = list_domains()
    return {
        "domains": [get_domain_summary(d) for d in domains],
        "total": len(domains),
    }


@router.get("/{domain}/knowledge")
async def domain_knowledge_endpoint(
    domain: str,
    x_api_key: str = Header("", alias="x-api-key"),
) -> Dict[str, Any]:
    """Return the full domain knowledge library for a domain."""
    _require_creator(x_api_key)
    from core.domain_knowledge import get_domain_priors, get_domain_summary
    patterns = get_domain_priors(domain)
    summary = get_domain_summary(domain)
    return {
        "summary": summary,
        "patterns": [p.to_dict() for p in patterns],
    }
