"""
/v1/engines — Estado de los motores de Vectrax (READ-ONLY).

GET /v1/engines → snapshot read-only por motor (tier, disponible, gating).

Seguridad / decisión de diseño:
  Endpoint ESTRICTAMENTE de solo lectura (no muta estado, no activa nada),
  igual que /v1/universe y /v1/health. NO se expone ningún disparador de
  activación por HTTP: activar motores es una operación privilegiada que
  requiere autorización del creador, así que sólo ocurre (a) en el arranque
  del proceso (perfil 'safe' por defecto) y (b) por el comando LOCAL
  `make activate` / `python -m core.orchestration`. Así no se abre ninguna
  puerta de activación remota sin auth.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter(tags=["engines"])
logger = logging.getLogger("vectrax.routes.engines")


@router.get("/engines")
async def engines_status() -> Dict[str, Any]:
    """Estado read-only de todos los motores (no activa nada)."""
    try:
        from core.orchestration import get_engine_status
        return get_engine_status()
    except Exception as exc:  # defensivo
        logger.warning("engines_status failed: %s", exc)
        return {"error": str(exc), "total": 0, "engines": []}
