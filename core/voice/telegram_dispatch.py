"""
core/voice/telegram_dispatch.py — Despacho unificado de audio a Telegram.

Punto único usado por TODOS los caminos de envío (telegram_gateway._send,
pipeline_worker._tg_send, y cualquier futuro). Garantiza que cualquier
respuesta enviada al usuario, no importa qué módulo la genere, dispare
voz si los gates lo permiten.

Esto resuelve estructuralmente la situación donde el gateway tenía
hook al TTS pero el pipeline_worker (que envía la mayoría de respuestas
QUEUED) no — Clase G aplicada al envío de Telegram.

Uso:
    from core.voice.telegram_dispatch import dispatch_audio_async
    dispatch_audio_async(chat_id=cid, text=resp, http_client=client, executor=pool)

Si no se pasa executor, se usa un ThreadPoolExecutor compartido del módulo.
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Any

logger = logging.getLogger("vectrax.voice.telegram_dispatch")

# Pool compartido para callers que no pasen su propio executor.
# Workers separados de los de gateway/pipeline; evita compartir saturación.
_DEFAULT_POOL_WORKERS = 3
_default_pool: Optional[ThreadPoolExecutor] = None
_pool_lock = threading.Lock()


def _get_default_pool() -> ThreadPoolExecutor:
    global _default_pool
    with _pool_lock:
        if _default_pool is None:
            _default_pool = ThreadPoolExecutor(
                max_workers=_DEFAULT_POOL_WORKERS,
                thread_name_prefix="vectrax-tts",
            )
    return _default_pool


def should_speak(text: str) -> bool:
    """Gate para decidir si vale la pena sintetizar este texto.

    Reglas:
      - TTS habilitado por env (OPENAI_API_KEY presente,
        VECTRAX_TTS_DISABLED != "1").
      - Texto no vacío.
      - Texto <= MAX_TTS_CHARS (no malgastar TTS en muros largos).
    """
    try:
        from core.voice.synthesizer import is_tts_enabled, MAX_TTS_CHARS
    except Exception:
        return False
    if not is_tts_enabled():
        return False
    if not text or not text.strip():
        return False
    if len(text) > MAX_TTS_CHARS:
        return False
    return True


def dispatch_audio_async(
    chat_id: int,
    text: str,
    http_client: Any,
    executor: Optional[ThreadPoolExecutor] = None,
    reply_to_message_id: Optional[int] = None,
) -> None:
    """Dispara síntesis + sendAudio en background. Nunca bloquea ni levanta.

    Args:
      chat_id: destinatario en Telegram.
      text: texto a sintetizar.
      http_client: httpx.Client ya creado por el caller. Compartirlo
        (en vez de crear uno nuevo) ahorra TCP handshakes.
      executor: opcional, ThreadPoolExecutor del caller. Si None se
        usa el pool compartido del módulo.
      reply_to_message_id: opcional. Si está dado, el audio se envía
        como REPLY al mensaje de texto correspondiente — Telegram
        ancla visualmente el audio a ese texto aunque llegue fuera
        de orden temporal. Resuelve la sensación de "repite el
        diálogo anterior" cuando hay varios mensajes seguidos.
    """
    if not should_speak(text):
        return
    pool = executor or _get_default_pool()
    try:
        pool.submit(_synth_and_send, chat_id, text, http_client, reply_to_message_id)
    except Exception as exc:
        logger.debug("dispatch submit swallowed: %s", exc)


def _synth_and_send(
    chat_id: int,
    text: str,
    http_client: Any,
    reply_to_message_id: Optional[int] = None,
) -> None:
    """Worker: sintetiza el texto y envía como sendAudio. Defensive."""
    try:
        from core.voice.synthesizer import synthesize
        audio = synthesize(text)
    except Exception as exc:
        logger.debug("TTS synth failed in dispatch: %s", exc)
        return
    if not audio:
        return
    try:
        send_audio_bytes(chat_id, audio, http_client, reply_to_message_id)
    except Exception as exc:
        logger.debug("sendAudio failed in dispatch: %s", exc)


def send_audio_bytes(
    chat_id: int,
    audio: bytes,
    http_client: Any,
    reply_to_message_id: Optional[int] = None,
) -> bool:
    """POST sendAudio a Telegram con multipart/form-data.

    Devuelve True si HTTP 200, False en cualquier otro caso. Logs en
    warning si falla.

    Si `reply_to_message_id` se provee, el audio queda anclado a ese
    mensaje en la UI de Telegram (audio aparece como reply al texto
    correspondiente, evitando confusión de orden).

    Requiere TELEGRAM_BOT_TOKEN en env.
    """
    if not audio:
        return False
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.warning("sendAudio: TELEGRAM_BOT_TOKEN missing")
        return False

    url = f"https://api.telegram.org/bot{token}/sendAudio"
    files = {
        "audio": ("vectrax.mp3", audio, "audio/mpeg"),
    }
    data = {
        "chat_id": str(chat_id),
        "title": "Vectrax",
        "performer": "Vectrax",
    }
    if reply_to_message_id:
        data["reply_to_message_id"] = str(reply_to_message_id)
        # Si el mensaje al que respondemos no existe, no fallar el send
        data["allow_sending_without_reply"] = "true"
    try:
        r = http_client.post(url, data=data, files=files)
        if r.status_code != 200:
            logger.warning("sendAudio HTTP %d: %s",
                           r.status_code, r.text[:200])
            return False
        return True
    except Exception as exc:
        logger.warning("sendAudio crashed: %s", exc)
        return False
