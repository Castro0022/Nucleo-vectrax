"""
services/core/routes/presence.py — El Canal del Creador (Fase 2, presencia viva)
================================================================================
Sirve el CAMPO de densidad del universo como raster VIVO (spec §9 Fase 2:
"reacción-difusión sembrada por el campo. Respiración, tensión, deriva").
Misma fuente que el universo (gravity index), sin caché propia del canal.

El estado del campo EVOLUCIONA en el servidor (`core.presence_runtime`, motor
temporal); todas las pestañas leen el MISMO estado → ven el mismo instante de la
vida de VECTRAX (un único estado que no puede mentir; no reproducibilidad externa).

La vista no tiene cifras, etiquetas, tooltips ni leyenda (§2): es solo el campo.
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
    """Frame vivo actual del Canal del Creador (motor TEMPORAL, reloj del server).

    El estado RD evoluciona en el servidor (`core.presence_runtime`); todas las
    pestañas leen el mismo estado (mismo instante). Nunca devuelve 500: ante
    cualquier error, responde el PNG negro de reserva.
    """
    try:
        from core.presence_runtime import current_frame_png

        png = current_frame_png() or _BLACK_1x1_PNG
    except Exception:
        png = _BLACK_1x1_PNG
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/presence/utterance")
async def presence_utterance():
    """Texto (+ procedencia) que la presencia dirá — voz+texto en el CLIENTE (§9).

    Determinista y honesto (§5): sale de datos persistidos (op_cycles), no
    inventa. El habla y la sincronía por palabra (Web Speech `onboundary`) son
    del cliente; sin TTS de servidor. Nunca 500: ante error, payload vacío.
    """
    try:
        from core.presence.phase0 import utterance_payload

        return utterance_payload()
    except Exception:
        return {"question": "", "text": "", "provenance": [], "provenance_count": 0}
