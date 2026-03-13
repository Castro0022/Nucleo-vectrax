"""GET /v1/connectors — list registered connectors (auth required)."""

from fastapi import APIRouter, Depends

from services.core.auth import require_token
from services.core.models import ConnectorInfo, ConnectorListResponse

router = APIRouter(prefix="/connectors", tags=["connectors"])


@router.get("", response_model=ConnectorListResponse)
async def list_connectors(_token: str = Depends(require_token)):
    """Return all connectors registered in the ConnectorRegistry."""
    try:
        from connectors.registry import get_connector_registry

        registry = get_connector_registry()
        infos = []
        for name, adapter in registry.list_all().items():
            spec = adapter.spec
            infos.append(
                ConnectorInfo(
                    name=spec.name,
                    scopes=list(spec.scopes),
                    permissions=list(spec.permissions),
                    healthy=adapter.healthcheck(),
                    version=spec.version,
                )
            )
        return ConnectorListResponse(connectors=infos, total=len(infos))
    except Exception:
        # Connectors module not available yet — return empty
        return ConnectorListResponse(connectors=[], total=0)
