"""
POST /v1/gateway/message — External Gateway endpoint.

Primer canal externo de Vectrax. Recibe mensajes de usuarios externos,
los procesa a través del Universal Bus, y devuelve una respuesta limpia.

Nada accede al núcleo directamente — todo transita por el bus.
Todos los eventos quedan registrados en el ledger.

Creado: 2026-03-19
Creador: Mario Bravo Castro
"""

import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from services.core.auth import AuthContext
from services.core.middleware.user_context import require_permission
from services.core.models import GatewayMessageRequest, GatewayMessageResponse

router = APIRouter(prefix="/gateway", tags=["gateway"])
logger = logging.getLogger("vectrax.core.routes.gateway")

# Ensure project root importable
_project_root = str(Path(__file__).resolve().parents[3])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


@router.post("/message", response_model=GatewayMessageResponse)
async def gateway_message(
    body: GatewayMessageRequest,
    ctx: AuthContext = Depends(require_permission("event.write")),
):
    """
    Recibe un mensaje externo y lo procesa a través del External Gateway.

    Input:  { user_id, content, channel? }
    Output: { event_id, user_id, channel, response, timestamp, processed }

    El gateway:
    1. Emite external.message_received al Universal Bus
    2. Procesa el mensaje vía pipeline de resolución
    3. Emite external.message_response al Universal Bus
    4. Registra ambos eventos en el ledger
    5. Retorna la respuesta al usuario
    """
    try:
        from core.operator.external_gateway import get_external_gateway

        gateway = get_external_gateway()

        result = gateway.receive_message(
            user_id=body.user_id or ctx.owner,
            content=body.content,
            channel=body.channel,
        )

        if not result.processed:
            raise HTTPException(
                status_code=422,
                detail=result.error or "Message could not be processed",
            )

        return GatewayMessageResponse(
            event_id=result.event_id,
            user_id=result.user_id,
            channel=result.channel,
            response=result.response,
            timestamp=result.timestamp,
            processed=result.processed,
            error=result.error,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Gateway message processing failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/stats")
async def gateway_stats(
    ctx: AuthContext = Depends(require_permission("event.read")),
):
    """Estadísticas del gateway externo."""
    try:
        from core.operator.external_gateway import get_external_gateway

        gateway = get_external_gateway()
        return gateway.stats()
    except Exception as exc:
        logger.exception("Gateway stats failed")
        raise HTTPException(status_code=500, detail=str(exc))
