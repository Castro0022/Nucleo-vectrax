"""
services/core/routes/presence.py — El Canal del Creador (Fase 2, presencia viva)
================================================================================
Sirve el CAMPO de densidad del universo como raster VIVO (spec §9 Fase 2:
"reacción-difusión sembrada por el campo. Respiración, tensión, deriva").
Misma fuente que el universo (gravity index), sin caché propia del canal.

El tiempo `t` deriva del RELOJ DEL SERVIDOR: la presencia tiene un ÚNICO estado
compartido, así dos pestañas ven a VECTRAX en el mismo instante de su vida.
`evolve()` sigue siendo pura; el server solo mapea reloj→t. El parámetro `?t=`
es opcional y solo para depuración/tests (fija el frame).

La vista no tiene cifras, etiquetas, tooltips ni leyenda (§2): es solo el campo.
"""
from __future__ import annotations

import base64
from typing import Optional

from fastapi import APIRouter, Response

router = APIRouter()

# Fallback 1×1 negro: el endpoint NUNCA debe devolver 500 (§presencia siempre).
_BLACK_1x1_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)

# Cota defensiva del t de depuración (evita valores absurdos vía querystring).
_T_MAX = 10_000_000


@router.get("/presence/field.png")
async def presence_field_png(t: Optional[int] = None) -> Response:
    """Render del frame vivo actual (PNG continuo, sin nodos).

    Sin `t`: el frame se deriva del reloj del servidor (estado único). Con `?t=N`
    (debug/test) se fija ese frame. Nunca devuelve 500: ante cualquier error,
    responde el PNG negro de reserva.
    """
    try:
        from core.presence.life import render_current_frame_png

        frame_t = None if t is None else max(0, min(int(t), _T_MAX))
        png = render_current_frame_png(frame_t)
        if not png:
            png = _BLACK_1x1_PNG
    except Exception:
        png = _BLACK_1x1_PNG
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )
