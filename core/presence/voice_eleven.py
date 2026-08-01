"""
core/presence/voice_eleven.py — Síntesis de voz del Canal del Creador (ElevenLabs)
==================================================================================
Síntesis de SERVIDOR para La Presencia (Fase 3), REVERTIDA desde Web Speech del
navegador (voces robóticas del sistema, sin timestamps). Usa ElevenLabs
*streaming with-timestamps*: devuelve audio + timestamps a nivel de PALABRA
(derivados de la alineación por carácter que da la API). Esos timestamps guían el
revelado texto-voz (§5/§9) y los pulsos de la presencia (§Vibración) — NO el
`onboundary` del navegador.

Voz elegida por el creador (2026-07-27): River (neutral, registro medio, tranquila).

Honestidad / robustez:
  - Nunca revienta el flujo: ante fallo devuelve None / itera vacío; el caller
    decide el fallback. No cachea audio (cada síntesis es nueva).
  - No usa el TTS de Telegram (OpenAI) ni `voice_engine.py` (no dan timestamps).

Entorno:
  ELEVENLABS_API_KEY          — obligatoria (permiso text_to_speech).
  VX_PRESENCE_VOICE_ID        — override de voz (default River).
  VX_PRESENCE_TTS_MODEL       — modelo (default eleven_multilingual_v2, el timbre
                                que el creador aprobó; turbo/flash = menor latencia).
  VX_PRESENCE_TTS_FORMAT      — output_format (default mp3_44100_128).
"""
from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger("vectrax.presence.voice_eleven")

# Voz ELEGIDA por el creador: River — neutral, middle-aged, calm.
RIVER_VOICE_ID = "SAz9YHcvj6GT2YYXdXww"
DEFAULT_VOICE_ID = os.environ.get("VX_PRESENCE_VOICE_ID", RIVER_VOICE_ID)
# Modelo: multilingüe v2 (timbre aprobado). turbo/flash bajan latencia; todos
# soportan with-timestamps.
DEFAULT_MODEL = os.environ.get("VX_PRESENCE_TTS_MODEL", "eleven_multilingual_v2")
DEFAULT_OUTPUT_FORMAT = os.environ.get("VX_PRESENCE_TTS_FORMAT", "mp3_44100_128")

_BASE = "https://api.elevenlabs.io"
_TIMEOUT = 30.0


def _api_key() -> str:
    """Clave de ElevenLabs; carga el .env de la raíz si el proceso no la tenía
    (mismo patrón defensivo que `vectrax/resolver.py`)."""
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if key:
        return key
    try:
        from dotenv import load_dotenv
        _env = Path(__file__).resolve().parents[2] / ".env"
        if _env.exists():
            load_dotenv(_env, override=False)
    except Exception:
        pass
    return os.environ.get("ELEVENLABS_API_KEY", "")


def is_available() -> bool:
    """True si hay clave para sintetizar (no valida permisos aquí)."""
    return bool(_api_key())


def _voice_settings() -> Dict:
    """Ajustes de voz: tranquila y estable (§ voz elegida). Ajustable por env."""
    return {
        "stability": float(os.environ.get("VX_PRESENCE_TTS_STABILITY", "0.55")),
        "similarity_boost": float(os.environ.get("VX_PRESENCE_TTS_SIMILARITY", "0.75")),
        "style": float(os.environ.get("VX_PRESENCE_TTS_STYLE", "0.0")),
        "use_speaker_boost": True,
    }


def _extract_alignment(obj: Dict) -> Tuple[List[str], List[float], List[float]]:
    """Saca (characters, starts, ends) de un objeto de la respuesta. Usa la
    alineación cruda (no la normalizada) para casar con el texto real."""
    al = obj.get("alignment") or obj.get("normalized_alignment") or {}
    chars = al.get("characters") or []
    starts = al.get("character_start_times_seconds") or []
    ends = al.get("character_end_times_seconds") or []
    return chars, starts, ends


def chars_to_words(chars: List[str], starts: List[float], ends: List[float]) -> List[Dict]:
    """Agrupa la alineación por CARÁCTER en timestamps por PALABRA.

    palabra.start = inicio del primer carácter no-espacio; palabra.end = fin del
    último. Separadores: espacios en blanco. Determinista y puro.
    """
    words: List[Dict] = []
    cur = ""
    cur_start: Optional[float] = None
    cur_end: Optional[float] = None
    n = min(len(chars), len(starts), len(ends))
    for i in range(n):
        ch = chars[i]
        if ch is None or (isinstance(ch, str) and ch.isspace()):
            if cur:
                words.append({
                    "word": cur,
                    "start": round(float(cur_start), 3),
                    "end": round(float(cur_end), 3),
                })
                cur = ""
                cur_start = None
                cur_end = None
        else:
            if not cur:
                cur_start = starts[i]
            cur += ch
            cur_end = ends[i]
    if cur:
        words.append({
            "word": cur,
            "start": round(float(cur_start), 3),
            "end": round(float(cur_end), 3),
        })
    return words


def stream_with_timestamps(
    text: str,
    voice_id: str = DEFAULT_VOICE_ID,
    model_id: Optional[str] = None,
    output_format: Optional[str] = None,
) -> Iterator[Tuple[bytes, List[str], List[float], List[float]]]:
    """Generador: produce (audio_chunk, chars, starts, ends) según llegan.

    Endpoint `/v1/text-to-speech/{voice}/stream/with-timestamps` (NDJSON): cada
    línea trae un trozo de audio (base64) + su alineación por carácter (tiempos
    en segundos desde el inicio de la locución). Solo I/O de red; no toca estado.
    Nunca levanta: ante error, corta la iteración.
    """
    text = (text or "").strip()
    if not text:
        return
    key = _api_key()
    if not key:
        logger.warning("ElevenLabs: sin ELEVENLABS_API_KEY")
        return

    import httpx

    url = f"{_BASE}/v1/text-to-speech/{voice_id}/stream/with-timestamps"
    payload = {
        "text": text,
        "model_id": model_id or DEFAULT_MODEL,
        "voice_settings": _voice_settings(),
    }
    params = {"output_format": output_format or DEFAULT_OUTPUT_FORMAT}
    headers = {
        "xi-api-key": key,
        "content-type": "application/json",
        "accept": "application/json",
    }
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            with client.stream("POST", url, headers=headers, json=payload, params=params) as r:
                if r.status_code != 200:
                    body = r.read()
                    logger.warning(
                        "ElevenLabs stream HTTP %d: %s", r.status_code, body[:200]
                    )
                    return
                for line in r.iter_lines():
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    audio_b64 = obj.get("audio_base64")
                    audio = base64.b64decode(audio_b64) if audio_b64 else b""
                    chars, starts, ends = _extract_alignment(obj)
                    yield audio, chars, starts, ends
    except Exception as exc:
        logger.warning("ElevenLabs stream failed: %s", exc)
        return


def synthesize_bytes(
    text: str,
    fmt: str = "mp3",
    voice_id: str = DEFAULT_VOICE_ID,
    model_id: Optional[str] = None,
) -> Optional[bytes]:
    """Síntesis SIMPLE a bytes (sin timestamps), para Telegram u otros canales.

    Usa el endpoint TTS normal con la voz River. Solo entrega MP3
    (`mp3_44100_128`); para otros formatos (p. ej. OGG/Opus de sendVoice)
    devuelve None y el caller decide el fallback. Nunca levanta.
    """
    text = (text or "").strip()
    if not text or fmt != "mp3":
        return None
    key = _api_key()
    if not key:
        return None
    import httpx
    url = f"{_BASE}/v1/text-to-speech/{voice_id}"
    payload = {
        "text": text,
        "model_id": model_id or DEFAULT_MODEL,
        "voice_settings": _voice_settings(),
    }
    params = {"output_format": "mp3_44100_128"}
    headers = {
        "xi-api-key": key,
        "content-type": "application/json",
        "accept": "audio/mpeg",
    }
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.post(url, headers=headers, json=payload, params=params)
            if r.status_code != 200:
                try:
                    _body = r.text[:200]
                except Exception:
                    _body = ""
                logger.warning("ElevenLabs TTS HTTP %d: %s", r.status_code, _body)
                return None
            return r.content
    except Exception as exc:
        logger.warning("ElevenLabs synthesize_bytes failed: %s", exc)
        return None


def synthesize_with_timestamps(
    text: str,
    voice_id: str = DEFAULT_VOICE_ID,
    model_id: Optional[str] = None,
    output_format: Optional[str] = None,
) -> Optional[Dict]:
    """Colecciona el stream y devuelve audio completo + timestamps por PALABRA.

    Return:
      {"audio": bytes(mp3), "words": [{"word","start","end"}],
       "voice_id", "model_id", "duration_s"}
    o None si no hay clave / falla / no llegó nada. Nunca levanta.
    """
    audio_parts: List[bytes] = []
    all_chars: List[str] = []
    all_starts: List[float] = []
    all_ends: List[float] = []
    got = False

    for audio, chars, starts, ends in stream_with_timestamps(
        text, voice_id=voice_id, model_id=model_id, output_format=output_format
    ):
        got = True
        if audio:
            audio_parts.append(audio)
        if chars:
            all_chars.extend(chars)
            all_starts.extend(starts)
            all_ends.extend(ends)

    if not got:
        return None

    words = chars_to_words(all_chars, all_starts, all_ends)
    duration = round(float(all_ends[-1]), 3) if all_ends else 0.0
    return {
        "audio": b"".join(audio_parts),
        "words": words,
        "voice_id": voice_id,
        "model_id": model_id or DEFAULT_MODEL,
        "duration_s": duration,
    }
