"""
core/voice/synthesizer.py — Síntesis de voz para Vectrax.

Convierte texto en bytes de audio listos para enviar como `sendVoice` de
Telegram. Usa OpenAI TTS por defecto (voz `nova` — cálida, neutra).

Diseño:
  - synthesize(text, voice, lang) → bytes (OGG/Opus, mono).
  - Defensive: NUNCA levanta excepción que rompa el envío de texto. Si
    el TTS falla, devuelve None y el caller manda solo texto.
  - Cache opcional por hash del (text, voice) para evitar re-pedir
    audios idénticos (ahorro de costo y latencia). LRU acotado.
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

import hashlib
import logging
import os
import threading
from collections import OrderedDict
from typing import Optional

logger = logging.getLogger("vectrax.voice.synthesizer")

# OpenAI TTS voices: alloy, echo, fable, onyx, nova, shimmer
DEFAULT_VOICE = os.environ.get("VECTRAX_TTS_VOICE", "nova")
DEFAULT_MODEL = os.environ.get("VECTRAX_TTS_MODEL", "tts-1")

# Caracter limit antes de cortar (ahorro de costo en respuestas largas)
MAX_TTS_CHARS = 1000

# LRU cache acotada
_CACHE_MAX = 64
_cache: "OrderedDict[str, bytes]" = OrderedDict()
_cache_lock = threading.Lock()


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
) -> Optional[bytes]:
    """Sintetiza `text` y devuelve audio en bytes (OGG/Opus mono).

    Args:
      text: el texto a convertir. Se trunca a MAX_TTS_CHARS si excede.
      voice: nombre de voz OpenAI (default: nova). Ignorado si lang
             implica fallback a otro modelo.
      lang: idioma del texto (`es`, `en`, etc.). Usado solo como hint;
            OpenAI TTS detecta idioma del input.

    Returns:
      Bytes del audio, o None si TTS está desactivado, falla, o el
      texto está vacío.
    """
    if not text or not text.strip():
        return None
    if not is_tts_enabled():
        logger.debug("TTS disabled (no API key or disabled flag)")
        return None

    # Truncado defensivo
    raw = text.strip()
    if len(raw) > MAX_TTS_CHARS:
        raw = _smart_truncate(raw, MAX_TTS_CHARS)

    voice = voice or DEFAULT_VOICE
    cache_key = _cache_key(raw, voice, DEFAULT_MODEL)

    # Lookup en cache
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached is not None:
            _cache.move_to_end(cache_key)
            logger.debug("TTS cache hit (%d bytes)", len(cached))
            return cached

    try:
        audio = _call_openai_tts(raw, voice, DEFAULT_MODEL)
    except Exception as exc:
        logger.warning("TTS synthesis failed: %s", exc)
        return None

    if audio is None:
        return None

    with _cache_lock:
        _cache[cache_key] = audio
        if len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return audio


def _call_openai_tts(text: str, voice: str, model: str) -> Optional[bytes]:
    """Llamada concreta al endpoint de OpenAI TTS.

    Usa httpx directamente (no la lib oficial) para evitar dependencias.
    Pide formato `opus`, que OpenAI devuelve en contenedor OGG/Opus —
    listo para Telegram sendVoice.
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
    payload = {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": "opus",
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(url, headers=headers, json=payload)
            if r.status_code != 200:
                logger.warning(
                    "TTS HTTP %d: %s", r.status_code, r.text[:200],
                )
                return None
            return r.content
    except Exception as exc:
        logger.warning("TTS HTTP call crashed: %s", exc)
        return None


def _cache_key(text: str, voice: str, model: str) -> str:
    raw = f"{model}|{voice}|{text}"
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24]


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
    """Test helper / utility: limpia el cache. Devuelve cuántos había."""
    with _cache_lock:
        n = len(_cache)
        _cache.clear()
    return n
