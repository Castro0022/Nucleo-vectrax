"""
services/core/routes/presence.py — El Canal del Creador (Fase 1, render)
=======================================================================
Sirve el CAMPO de densidad del universo como raster (spec §9 Fase 1 "render",
§3 "vive dentro de la misma app que el universo"). Es la misma fuente de datos
que el universo (gravity index), sin caché propia del canal.

Fase 1: estático. Sin vida, sin animación, sin voz, sin estilo. La vista no
tiene cifras, etiquetas, tooltips ni leyenda (§2): es solo el campo.
"""
from __future__ import annotations

import base64

from fastapi import APIRouter, Response

router = APIRouter()

# Fallback 1×1 negro: el endpoint NUNCA debe devolver 500 (§presencia siempre).
_BLACK_1x1_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


@router.get("/presence/field.png")
async def presence_field_png() -> Response:
    """Render del campo de densidad actual (PNG continuo, sin nodos)."""
    try:
        from core.presence.projection import render_current_field_png

        png = render_current_field_png()
        if not png:
            png = _BLACK_1x1_PNG
    except Exception:
        png = _BLACK_1x1_PNG
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )
