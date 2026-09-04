"""
/v1/ingest/{domain} — Generic Data Ingest API
================================================
Any business can feed data to Vectrax through this endpoint.

POST /v1/ingest/{domain}
Headers: x-api-key: vx_...
Body: {"event_type": "sale", "data": {"product": "iPhone", "amount": 1199}}

The data is converted to text, embedded, passed through the Learning Gate,
and incorporated into the gravitational universe under the tenant's domain.

Creador: Mario Bravo Castro
Fecha: 2026-06-15
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("vectrax.routes.ingest_api")

router = APIRouter(prefix="/ingest", tags=["ingest"])


class IngestEvent(BaseModel):
    event_type: str = Field(..., min_length=1, max_length=100)
    data: Dict[str, Any] = Field(default_factory=dict)
    timestamp: Optional[str] = None


class IngestResponse(BaseModel):
    success: bool
    star_id: Optional[str] = None
    domain: str = ""
    event_type: str = ""
    learning: str = ""
    text: str = ""
    latency_ms: float = 0


class TenantCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    plan: str = "free"
    domain: str = ""


# ---------------------------------------------------------------------------
# POST /v1/ingest/{domain} — ingest a business event
# ---------------------------------------------------------------------------

@router.post("/{domain}", response_model=IngestResponse)
async def ingest_event(
    domain: str,
    event: IngestEvent,
    x_api_key: str = Header(..., alias="x-api-key"),
):
    """Ingest a business event into the Vectrax gravitational universe."""
    # Validate API key
    from core.tenant import validate_key, record_event
    tenant = validate_key(x_api_key)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if not tenant.active:
        raise HTTPException(status_code=403, detail="Tenant is deactivated")

    # Rate limit
    if not tenant.can_ingest:
        raise HTTPException(
            status_code=429,
            detail=f"Daily limit reached ({tenant.daily_limit} events/day for {tenant.plan} plan)",
        )

    # Domain must match tenant's domain (or be a sub-domain)
    if tenant.domain and domain != tenant.domain:
        raise HTTPException(
            status_code=403,
            detail=f"Domain '{domain}' not authorized for this tenant (expected '{tenant.domain}')",
        )

    # Ingest
    from core.domain_ingester import ingest_event as do_ingest
    result = do_ingest(
        tenant_id=tenant.tenant_id,
        domain=domain,
        event_type=event.event_type,
        data=event.data,
        event_timestamp=event.timestamp,
    )

    if result.get("success"):
        record_event(tenant.tenant_id)

    return IngestResponse(
        success=result.get("success", False),
        star_id=result.get("star_id"),
        domain=result.get("domain", domain),
        event_type=result.get("event_type", event.event_type),
        learning=result.get("learning", ""),
        text=result.get("text", ""),
        latency_ms=result.get("latency_ms", 0),
    )


# ---------------------------------------------------------------------------
# POST /v1/ingest/tenant/create — create a new tenant (creator only)
# ---------------------------------------------------------------------------

@router.post("/tenant/create")
async def create_tenant_endpoint(
    req: TenantCreateRequest,
    x_api_key: str = Header("", alias="x-api-key"),
):
    """Create a new tenant. Requires creator API key or VX_CREATOR_KEY."""
    import os
    creator_key = os.environ.get("VX_CREATOR_KEY", "")

    # Allow creator key or existing creator tenant
    authorized = False
    if creator_key and x_api_key == creator_key:
        authorized = True
    else:
        from core.tenant import validate_key
        tenant = validate_key(x_api_key)
        if tenant and tenant.plan == "creator":
            authorized = True

    if not authorized:
        raise HTTPException(status_code=403, detail="Only creator can create tenants")

    from core.tenant import create_tenant
    result = create_tenant(name=req.name, plan=req.plan, domain=req.domain)
    return result


# ---------------------------------------------------------------------------
# GET /v1/ingest/tenant/list — list tenants (creator only)
# ---------------------------------------------------------------------------

@router.get("/tenant/list")
async def list_tenants_endpoint(
    x_api_key: str = Header("", alias="x-api-key"),
):
    """List all tenants. Creator only."""
    import os
    creator_key = os.environ.get("VX_CREATOR_KEY", "")

    authorized = False
    if creator_key and x_api_key == creator_key:
        authorized = True
    else:
        from core.tenant import validate_key
        tenant = validate_key(x_api_key)
        if tenant and tenant.plan == "creator":
            authorized = True

    if not authorized:
        raise HTTPException(status_code=403, detail="Only creator can list tenants")

    from core.tenant import list_tenants
    tenants = list_tenants()
    return {
        "tenants": [
            {
                "tenant_id": t.tenant_id,
                "name": t.name,
                "plan": t.plan,
                "domain": t.domain,
                "events_today": t.events_today,
                "daily_limit": t.daily_limit,
                "active": t.active,
            }
            for t in tenants
        ],
        "total": len(tenants),
    }
