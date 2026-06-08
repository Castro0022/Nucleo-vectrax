"""
core/voice/synthesizer.py — Síntesis de voz para Vectrax.

Convierte texto en bytes de audio listos para enviar como `sendVoice` de
Telegram. Usa OpenAI TTS por defecto (voz `nova` — cálida, neutra).

Diseño:
  - synthesize(text, voice, lang) → bytes (MP3) — cada call genera
    BYTES NUEVOS, sin cache compartida. Garantiza unicidad: el audio
    enviado al usuario corresponde EXACTAMENTE a este request, sin
    heredar nada de mensajes anteriores.
  - Defensive: NUNCA levanta excepción que rompa el envío de texto. Si
    el TTS falla, devuelve None y el caller manda solo texto.
  - NO hay cache compartida ni en memoria ni en disco. La cache
    anterior (LRU por hash de texto) se eliminó porque generaba la
    sensación de "audio anterior arrastrado" cuando el mismo texto
    se repetía. Ahora cada call → audio nuevo. El Anti-Repetition
    Filter (core/voice/anti_repetition.py) ya garantiza que dos
    respuestas nunca son textualmente idénticas, así que el costo
    de re-sintetizar es despreciable.
  - Filtros: si el texto excede MAX_TTS_CHARS, recorta a un punto
    natural antes de llamar al API (ahorro adicional de tokens).

Costo estimado (OpenAI tts-1 a Mayo 2026):
  ~$0.015 por 1000 chars. Una respuesta promedio de 200 chars cuesta
  ~$0.003. Volumen típico de tens/día: <$0.10/mes.

Latencia: 1-3s desde el call hasta los bytes.

Variables de entorno:
  OPENAI_API_KEY        — credencial obligatoria (ya existe en server).
  VECTRAX_TTS_VOICE     — voz default (nova si no se setea).
  VECTRAX_TTS_MODEL     — tts-1 (default) o tts-1-hd (mejor calidad,
                          más caro, más lento).
  VECTRAX_TTS_DISABLED  — si "1", desactiva TTS (modo solo-texto).
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Optional

logger = logging.getLogger("vectrax.voice.synthesizer")

# Directorio para audios persistidos a disco. La ruta se construye
# relativa al CWD del proceso por convención del Gravity spec
# (cache/audio/...). Si una llamada quiere persistir audio, usa los
# helpers `get_unique_output_path` / `write_unique_audio` /
# `cleanup_audio_file` definidos al final del módulo.
_AUDIO_CACHE_DIR = os.environ.get("VECTRAX_AUDIO_CACHE_DIR", "cache/audio")

# OpenAI TTS voices: alloy, echo, fable, onyx, nova, shimmer
DEFAULT_VOICE = os.environ.get("VECTRAX_TTS_VOICE", "nova")
DEFAULT_MODEL = os.environ.get("VECTRAX_TTS_MODEL", "tts-1")

# Formato de salida: 'opus' (default, OGG/Opus para Telegram sendVoice)
# o 'mp3' (legacy, para sendAudio). Voice memos no disparan autoplay
# del siguiente en el cliente Telegram — fix estructural del bug
# 'el audio anterior se reproduce al final'.
DEFAULT_FORMAT = os.environ.get("VECTRAX_TTS_FORMAT", "opus").lower().strip()
_VALID_FORMATS = {"opus", "mp3"}
if DEFAULT_FORMAT not in _VALID_FORMATS:
    DEFAULT_FORMAT = "opus"

# Caracter limit antes de cortar (ahorro de costo en respuestas largas)
MAX_TTS_CHARS = 1000

# Rate limit cooldown: after a 429, skip TTS for this many seconds
# to let the OpenAI rate limit window reset. Prevents wasting requests.
_tts_cooldown_until: float = 0.0
_TTS_COOLDOWN_SECONDS = 300  # 5 minutes after a 429


def is_tts_enabled() -> bool:
    """True si el TTS está habilitado por env y la API key existe."""
    if os.environ.get("VECTRAX_TTS_DISABLED") == "1":
        return False
    if not os.environ.get("OPENAI_API_KEY"):
        return False
    return True


def synthesize(
    text: str,
    voice: Optional[str] = None,
    lang: str = "es",
    fmt: Optional[str] = None,
) -> Optional[bytes]:
    """Sintetiza `text` y devuelve audio en bytes.

    Args:
      text: el texto a convertir. Se trunca a MAX_TTS_CHARS si excede.
      voice: nombre de voz OpenAI (default: nova).
      lang: idioma del texto (`es`, `en`, etc.). Hint para el modelo.
      fmt: 'opus' (OGG/Opus para sendVoice) | 'mp3' (para sendAudio).
           Default DEFAULT_FORMAT (opus).

    Returns:
      Bytes del audio, o None si TTS está desactivado, falla, o el
      texto está vacío.
    """
    if not text or not text.strip():
        return None
    if not is_tts_enabled():
        logger.debug("TTS disabled (no API key or disabled flag)")
        return None
    # Rate limit: check centralized API gate first
    try:
        from core.api_gate import check_gate
        if not check_gate("openai_tts"):
            logger.debug("TTS: API gate closed, skipping")
            return None
    except Exception:
        pass
    # Legacy cooldown (backup)
    if time.time() < _tts_cooldown_until:
        logger.debug("TTS cooldown active (%.0fs remaining)",
                     _tts_cooldown_until - time.time())
        return None

    # Truncado defensivo
    raw = text.strip()
    if len(raw) > MAX_TTS_CHARS:
        raw = _smart_truncate(raw, MAX_TTS_CHARS)

    voice = voice or DEFAULT_VOICE
    fmt = (fmt or DEFAULT_FORMAT).lower().strip()
    if fmt not in _VALID_FORMATS:
        fmt = DEFAULT_FORMAT

    # Sin cache: cada synth produce audio nuevo. Ver docstring del modulo.
    try:
        audio = _call_openai_tts(raw, voice, DEFAULT_MODEL, fmt)
    except Exception as exc:
        logger.warning("TTS synthesis failed: %s", exc)
        return None

    if audio is None:
        return None

    logger.debug(
        "TTS synth ok (%d bytes, %d chars, fmt=%s)",
        len(audio), len(raw), fmt,
    )
    return audio


def _call_openai_tts(
    text: str, voice: str, model: str, fmt: str = "opus",
) -> Optional[bytes]:
    """Llamada concreta al endpoint de OpenAI TTS.

    `fmt` controla el contenedor de audio:
      - 'opus' → OGG/Opus, ideal para Telegram sendVoice (sin autoplay
        del siguiente en el cliente).
      - 'mp3'  → MP3, ideal para Telegram sendAudio (legacy).
    """
    import httpx

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return None

    url = "https://api.openai.com/v1/audio/speech"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    response_format = "opus" if fmt == "opus" else "mp3"
    payload = {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": response_format,
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(url, headers=headers, json=payload)
            if r.status_code == 429:
                global _tts_cooldown_until
                _tts_cooldown_until = time.time() + _TTS_COOLDOWN_SECONDS
                # Record in centralized gate (exponential backoff)
                try:
                    from core.api_gate import record_429
                    record_429("openai_tts")
                except Exception:
                    pass
                logger.warning(
                    "TTS rate limited (429) — cooldown %ds",
                    _TTS_COOLDOWN_SECONDS,
                )
                return None
            if r.status_code != 200:
                logger.warning(
                    "TTS HTTP %d: %s", r.status_code, r.text[:200],
                )
                return None
            return r.content
    except Exception as exc:
        logger.warning("TTS HTTP call crashed: %s", exc)
        return None


def _smart_truncate(text: str, max_len: int) -> str:
    """Trunca al último punto/oración antes de max_len."""
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    last = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    if last > max_len // 3:
        return cut[:last + 1].strip()
    return cut.strip()


def clear_cache() -> int:
    """Compat shim: el cache fue eliminado. Mantener la función para
    no romper callers/tests existentes. Devuelve siempre 0.

    Adicionalmente, limpia (best-effort) cualquier directorio residual
    `cache/voice_segments/` que pudiera quedar de versiones previas.
    """
    cleaned = 0
    for d in ("cache/voice_segments", "voice_segments"):
        try:
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    try:
                        os.remove(os.path.join(d, fn))
                        cleaned += 1
                    except Exception:
                        pass
        except Exception:
            pass
    return cleaned


# ===========================================================================
# Persistencia opcional a disco (Gravity spec: tts_{uid}_{ts_ns}_{uuid}.mp3)
# ===========================================================================

def _sanitize_user_id(user_id: str) -> str:
    """Normaliza el user_id para uso en filename (sin / : espacios).

    Ejemplo: 'tg:2030762343' -> 'tg_2030762343'.
    """
    if not user_id:
        return "anon"
    safe = []
    for ch in str(user_id):
        if ch.isalnum() or ch in ("-", "_"):
            safe.append(ch)
        else:
            safe.append("_")
    return "".join(safe)[:64] or "anon"


def get_unique_output_path(user_id: str, ext: str = "mp3") -> str:
    """Devuelve una ruta única de salida para un audio de TTS.

    Forma: cache/audio/tts_{user_id}_{ts_ns}_{uuid4_hex8}.{ext}

    Entropía combinada (timestamp en nanosegundos + 4 bytes de uuid4)
    garantiza unicidad incluso bajo concurrencia y relojes con baja
    resolución. NO crea el archivo — solo devuelve la ruta. El caller
    es responsable de escribir y luego limpiarlo.
    """
    uid = _sanitize_user_id(user_id)
    ts = time.time_ns()
    rand = uuid.uuid4().hex[:8]
    fname = f"tts_{uid}_{ts}_{rand}.{ext}"
    return os.path.join(_AUDIO_CACHE_DIR, fname)


def write_unique_audio(audio: bytes, user_id: str, ext: str = "mp3") -> Optional[str]:
    """Escribe `audio` a una ruta única y la devuelve.

    Crea el directorio destino si no existe. Devuelve None si la
    escritura falla (NUNCA raise: el caller decide fallback).

    Cada llamada produce un archivo nuevo — sin sobrescritura, sin
    cache compartido entre requests. Esto es la garantía estructural
    contra el bug "el audio anterior se arrastra".
    """
    if not audio:
        return None
    path = get_unique_output_path(user_id, ext=ext)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(audio)
        return path
    except Exception as exc:
        logger.warning("write_unique_audio failed for %s: %s", path, exc)
        return None


def cleanup_audio_file(path: Optional[str]) -> bool:
    """Borra un archivo de audio tras envío exitoso. Best-effort.

    Devuelve True si el archivo se borró, False si no existía o falla.
    """
    if not path:
        return False
    try:
        if os.path.isfile(path):
            os.remove(path)
            return True
    except Exception as exc:
        logger.debug("cleanup_audio_file %s: %s", path, exc)
    return False
