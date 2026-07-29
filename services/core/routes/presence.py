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
from pydantic import BaseModel

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
    inventa. Es la fuente SELF_DETERMINISTIC (datos propios + procedencia) del
    canal. El habla y la sincronía por palabra (Web Speech `onboundary`) son del
    cliente; sin TTS de servidor. Nunca 500: ante error, payload vacío.
    """
    try:
        from core.presence.phase0 import utterance_payload

        payload = utterance_payload()
        payload["source"] = "SELF_DETERMINISTIC"
        return payload
    except Exception:
        return {"question": "", "text": "", "provenance": [],
                "provenance_count": 0, "source": "SELF_DETERMINISTIC"}


class PresenceAskBody(BaseModel):
    """Transcripción hablada por el creador en el canal."""

    text: str = ""


class PresenceRegisterBody(BaseModel):
    """Registro visual (fuente) que el campo debe reflejar en la FORMA."""

    source: str = ""


# Los 4 registros de la presencia. El cliente marca el registro y avisa al
# servidor (POST /presence/register); el campo se tiñe por fuente en la FORMA
# (core/presence_runtime.set_register + core/presence/style.REGISTER_TINTS).
_REGISTERS = ("SELF_DETERMINISTIC", "MEMORY", "ONLINE", "MODEL")


def _register_from_source(gw_source: str) -> str:
    """Mapea la señal de fuente del cerebro a uno de los 4 registros.

    Conservador y honesto (decisión del creador): NUNCA hacia arriba. Solo sube
    de registro cuando la señal lo PRUEBA (memory → MEMORY; búsqueda externa →
    ONLINE). Cualquier otra cosa (llm/fast/self_aware/criterion/vacío) → MODEL.
    SELF_DETERMINISTIC no sale de aquí: viene de phase0 (`/utterance`).
    """
    s = (gw_source or "").strip().lower()
    if s == "memory":
        return "MEMORY"
    if any(k in s for k in ("online", "places", "web", "search", "market")):
        return "ONLINE"
    return "MODEL"


@router.post("/presence/ask")
async def presence_ask(body: PresenceAskBody):
    """Conversación del Canal del Creador: enruta la transcripción por el MISMO
    cerebro que los demás canales (`ExternalGateway.receive_message` → memoria +
    identidad), sin crear routing nuevo. Devuelve la respuesta + el `source`
    (registro): SELF_DETERMINISTIC | MEMORY | ONLINE | MODEL. Ante duda → MODEL
    (nunca hacia arriba).

    Auth: canal LOCAL del creador — sin token; corre como el UID del creador
    (`VX_CREATOR_ID`) para que traiga su memoria e identidad. Nunca 500.
    """
    text = (body.text or "").strip()
    if not text:
        return {"answer": "", "source": "MODEL", "provenance": []}
    try:
        import os
        from core.operator.external_gateway import get_external_gateway

        creator_uid = os.environ.get("VX_CREATOR_ID", "2030762343")
        result = get_external_gateway().receive_message(
            user_id=creator_uid, content=text, channel="web",
        )
        answer = (getattr(result, "response", "") or "").strip()
        source = _register_from_source(getattr(result, "source", "")) if answer else "MODEL"
        return {"answer": answer, "source": source, "provenance": []}
    except Exception:
        return {"answer": "", "source": "MODEL", "provenance": []}


def _empty_speak() -> dict:
    return {"audio_base64": "", "words": [], "voice_id": "", "model_id": "", "duration_s": 0.0}


@router.post("/presence/speak")
async def presence_speak(body: PresenceAskBody):
    """Sintetiza `text` con la voz del canal (River) vía ElevenLabs streaming
    with-timestamps. Devuelve audio (base64 mp3) + timestamps por PALABRA para que
    el cliente lo reproduzca y revele el texto sincronizado (y dispare los pulsos
    de la presencia). Sustituye a la síntesis robótica del navegador (§Audio,
    REVERTIDO). Nunca 500: ante fallo, audio vacío y words=[] (el cliente decide
    fallback).
    """
    text = (body.text or "").strip()
    if not text:
        return _empty_speak()
    try:
        from core.presence.voice_eleven import synthesize_with_timestamps

        res = synthesize_with_timestamps(text)
        if not res or not res.get("audio"):
            return _empty_speak()
        return {
            "audio_base64": base64.b64encode(res["audio"]).decode("ascii"),
            "words": res["words"],
            "voice_id": res.get("voice_id", ""),
            "model_id": res.get("model_id", ""),
            "duration_s": res.get("duration_s", 0.0),
        }
    except Exception:
        return _empty_speak()


@router.post("/presence/register")
async def presence_register(body: PresenceRegisterBody):
    """Fija el REGISTRO visual actual (SELF_DETERMINISTIC | MEMORY | ONLINE |
    MODEL) para que la presencia lo distinga en la FORMA (tinte por fuente, §2:
    sin texto ni etiqueta). Canal local. Nunca 500.
    """
    try:
        from core.presence_runtime import set_register

        return {"ok": True, "source": set_register(body.source)}
    except Exception:
        return {"ok": False, "source": None}
