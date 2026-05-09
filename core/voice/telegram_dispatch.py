"""
core/voice/telegram_dispatch.py — Despacho unificado de audio a Telegram.

Punto único usado por TODOS los caminos de envío (telegram_gateway._send,
pipeline_worker._tg_send, y cualquier futuro). Garantiza que cualquier
respuesta enviada al usuario, no importa qué módulo la genere, dispare
voz si los gates lo permiten.

Esto resuelve estructuralmente la situación donde el gateway tenía
hook al TTS pero el pipeline_worker (que envía la mayoría de respuestas
QUEUED) no — Clase G aplicada al envío de Telegram.

Garantías estructurales contra el bug "el audio anterior se repite":
  - Dedup por (chat_id, sha256(text)) en ventana VECTRAX_AUDIO_DEDUP_WINDOW_S
    (default 60s). Si el mismo chat recibió el mismo texto en la ventana,
    el dispatch se SKIP. Cierra la clase de bug "dos audios idénticos
    seguidos por race entre paths o retry".
  - Lock per-chat: dos dispatches al MISMO chat_id se serializan. Así,
    aunque dos paths intenten enviar audio al mismo tiempo, uno espera
    al otro y el dedup bloquea el segundo si el texto es el mismo.
  - Cada call a synthesize() es fresh (sin cache; ver synthesizer.py).
  - Si VECTRAX_AUDIO_DISABLED=1, todos los dispatches son no-op (kill switch).

Uso:
    from core.voice.telegram_dispatch import dispatch_audio_async
    dispatch_audio_async(chat_id=cid, text=resp, http_client=client, executor=pool)

Si no se pasa executor, se usa un ThreadPoolExecutor compartido del módulo.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Any, Dict

logger = logging.getLogger("vectrax.voice.telegram_dispatch")

# Pool compartido para callers que no pasen su propio executor.
# Workers separados de los de gateway/pipeline; evita compartir saturación.
_DEFAULT_POOL_WORKERS = 3
_default_pool: Optional[ThreadPoolExecutor] = None
_pool_lock = threading.Lock()

# === Anti-duplicate dispatch state =========================================
# (chat_id, sha256(text)[:16]) -> last_dispatch_ts. Bounded LRU.
_DEDUP_MAX = 256
_dedup_seen: "OrderedDict[str, float]" = OrderedDict()
_dedup_lock = threading.Lock()

# Per-chat send lock map. Garantiza que dos audios al mismo chat se
# serialicen — sin afectar el throughput entre chats distintos.
_chat_locks: Dict[int, threading.Lock] = {}
_chat_locks_lock = threading.Lock()

# Chats con VOICE_MESSAGES_FORBIDDEN en privacy: para ellos saltamos
# directo a sendAudio (legacy MP3) sin malgastar un round-trip a
# sendVoice. Set se llena en runtime cuando Telegram devuelve 400.
_voice_forbidden_chats: set = set()
_voice_forbidden_lock = threading.Lock()


def _mark_voice_forbidden(chat_id: int) -> None:
    """Marca el chat como voice_forbidden en RAM + persiste en SQLite.

    Persistencia via core.continuity.voice_forbidden — sobrevive
    restarts. Best-effort: si la persistencia falla, el RAM cache
    sigue protegiendo durante esta sesión.
    """
    cid = int(chat_id)
    with _voice_forbidden_lock:
        _voice_forbidden_chats.add(cid)
    try:
        from core.continuity.voice_forbidden import mark_forbidden as _mf
        _mf(cid)
    except Exception as exc:
        logger.debug("voice_forbidden persist skipped: %s", exc)


def _is_voice_forbidden(chat_id: int) -> bool:
    """True si chat está forbidden (RAM o DB persistente).

    Primero RAM (latencia 0). Si no, consulta el módulo persistente,
    que tiene su propio in-memory cache hidratado al boot desde DB.
    """
    cid = int(chat_id)
    with _voice_forbidden_lock:
        if cid in _voice_forbidden_chats:
            return True
    try:
        from core.continuity.voice_forbidden import is_forbidden as _is
        if _is(cid):
            # Hidratar el RAM cache local con lo que vino de DB.
            with _voice_forbidden_lock:
                _voice_forbidden_chats.add(cid)
            return True
    except Exception:
        pass
    return False


def _get_chat_lock(chat_id: int) -> threading.Lock:
    with _chat_locks_lock:
        lk = _chat_locks.get(chat_id)
        if lk is None:
            lk = threading.Lock()
            _chat_locks[chat_id] = lk
            # Limitar el dict (no fugar locks en sistemas con millones
            # de users); 4096 chats activos en simultáneo es mucho.
            if len(_chat_locks) > 4096:
                # drop the oldest (insertion order)
                first_key = next(iter(_chat_locks))
                if first_key != chat_id:
                    _chat_locks.pop(first_key, None)
        return lk


def _dedup_key(chat_id: int, text: str) -> str:
    h = hashlib.sha256((text or "").encode("utf-8", "ignore")).hexdigest()[:16]
    return f"{chat_id}|{h}"


def _is_recent_duplicate(chat_id: int, text: str) -> bool:
    """True si (chat_id, hash(text)) ya fue dispatched dentro de la ventana.
    Mark-on-check: si NO es duplicado, lo registra ahora.
    """
    try:
        window = float(os.environ.get("VECTRAX_AUDIO_DEDUP_WINDOW_S", "120"))
    except Exception:
        window = 120.0
    if window <= 0:
        return False
    key = _dedup_key(chat_id, text)
    now = time.time()
    with _dedup_lock:
        last = _dedup_seen.get(key)
        if last is not None and (now - last) < window:
            # Refresh timestamp para mantener la ventana activa
            _dedup_seen[key] = now
            _dedup_seen.move_to_end(key)
            return True
        _dedup_seen[key] = now
        _dedup_seen.move_to_end(key)
        if len(_dedup_seen) > _DEDUP_MAX:
            _dedup_seen.popitem(last=False)
        return False


def reset_dedup_for_tests() -> None:
    """Test helper: limpia el cache de dedup."""
    with _dedup_lock:
        _dedup_seen.clear()
    with _chat_locks_lock:
        _chat_locks.clear()


def _get_default_pool() -> ThreadPoolExecutor:
    global _default_pool
    with _pool_lock:
        if _default_pool is None:
            _default_pool = ThreadPoolExecutor(
                max_workers=_DEFAULT_POOL_WORKERS,
                thread_name_prefix="vectrax-tts",
            )
    return _default_pool


def audio_mode() -> str:
    """Devuelve el modo activo: 'voice' | 'audio' | 'off'.

    - 'voice' (default): sendVoice con OGG/Opus — NO dispara autoplay
      del siguiente en cliente Telegram. Fix estructural definitivo.
    - 'audio'          : sendAudio con MP3 — legacy, autoplay-prone.
    - 'off'            : no dispatch.

    Compat: VECTRAX_AUDIO_DISABLED=1 → 'off' (kill switch legacy).
    """
    if os.environ.get("VECTRAX_AUDIO_DISABLED") == "1":
        return "off"
    m = (os.environ.get("VECTRAX_AUDIO_MODE") or "voice").lower().strip()
    if m not in ("voice", "audio", "off"):
        return "voice"
    return m


def should_speak(text: str) -> bool:
    """Gate para decidir si vale la pena sintetizar este texto.

    Reglas:
      - audio_mode() == 'off' → False (kill switch).
      - TTS habilitado por env (OPENAI_API_KEY presente,
        VECTRAX_TTS_DISABLED != "1").
      - Texto no vacío.
      - Texto <= MAX_TTS_CHARS (no malgastar TTS en muros largos).
    """
    if audio_mode() == "off":
        return False
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

    Garantías estructurales:
      - DEDUP: si (chat_id, sha256(text)) fue dispatched en la ventana
        VECTRAX_AUDIO_DEDUP_WINDOW_S (default 60s), se SKIP. Esto cierra
        la clase de bug "dos audios idénticos seguidos".
      - SERIALIZACIÓN PER-CHAT: dos dispatches al mismo chat_id corren
        en serie (lock). Distintos chats siguen paralelos.
      - LOG INSTRUMENTADO: cada dispatch loggea (chat, reply_to, text)
        Y cada SKIP loggea la razón. Permite diagnóstico desde logs.

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
        logger.debug(
            "DISPATCH SKIP (gate) chat=%s text=%r",
            chat_id, (text or "")[:60],
        )
        return

    # === BLOCK ORPHAN AUDIO: sin reply_to, el audio se desancla del
    # texto y parece "repetir el anterior". Mejor no enviar. ==========
    if reply_to_message_id is None:
        logger.info(
            "DISPATCH SKIP (no reply_to) chat=%s text=%r",
            chat_id, (text or "")[:60],
        )
        return

    # === DEDUP por (chat_id, hash(text)) en ventana corta ============
    if _is_recent_duplicate(chat_id, text):
        logger.warning(
            "DISPATCH SKIP (duplicate) chat=%s text=%r reply_to=%s",
            chat_id, (text or "")[:80], reply_to_message_id,
        )
        return

    logger.info(
        "DISPATCH chat=%s reply_to=%s text=%r",
        chat_id, reply_to_message_id, (text or "")[:80],
    )
    pool = executor or _get_default_pool()
    try:
        pool.submit(
            _synth_and_send,
            chat_id, text, http_client, reply_to_message_id,
        )
    except Exception as exc:
        logger.debug("dispatch submit swallowed: %s", exc)


def _synth_and_send(
    chat_id: int,
    text: str,
    http_client: Any,
    reply_to_message_id: Optional[int] = None,
) -> None:
    """Worker: sintetiza el texto y envía como sendAudio. Defensive.

    Toma un lock per-chat para garantizar que dos audios al mismo
    chat_id se manden en serie (sin overlap), aun en presencia de un
    pool con varios threads. Esto es la última barrera contra el bug
    de "audio anterior arrastrado" si el dedup no atrapara el caso.
    """
    chat_lock = _get_chat_lock(chat_id)
    with chat_lock:
        mode = audio_mode()
        if mode == "off":
            return

        # Si este chat YA está marcado como voice_forbidden (su privacy
        # de Telegram bloquea voice memos), degradamos a TEXTO-SOLO.
        # NO caemos a sendAudio porque sendAudio dispara autoplay del
        # cliente y reintroduce el bug 'el audio anterior se reproduce'.
        # Para volver a recibir audio, el user debe permitir voice
        # messages en su Telegram privacy.
        if mode == "voice" and _is_voice_forbidden(chat_id):
            logger.debug(
                "dispatch: chat %s voice_forbidden → texto-solo (sin audio)",
                chat_id,
            )
            return

        # Acoplar formato de síntesis al endpoint:
        #   voice → opus (OGG/Opus, sendVoice, sin autoplay)
        #   audio → mp3  (MP3, sendAudio, legacy con autoplay)
        fmt = "opus" if mode == "voice" else "mp3"
        try:
            from core.voice.synthesizer import synthesize
            audio = synthesize(text, fmt=fmt)
        except Exception as exc:
            logger.debug("TTS synth failed in dispatch: %s", exc)
            return
        if not audio:
            return
        try:
            if mode == "voice":
                ok, forbidden = _send_voice_with_forbidden_flag(
                    chat_id, audio, http_client, reply_to_message_id,
                )
                endpoint = "sendVoice"
                # Si Telegram dice VOICE_MESSAGES_FORBIDDEN, marcamos
                # el chat como voice_forbidden para futuros dispatches.
                # NO reintentamos con sendAudio: degradación natural a
                # texto-solo (el sendMessage ya se hizo en el caller).
                if forbidden:
                    _mark_voice_forbidden(chat_id)
                    logger.info(
                        "dispatch: chat %s VOICE_MESSAGES_FORBIDDEN "
                        "→ degradado a texto-solo. Para audio, el user "
                        "debe permitir voice messages en su Telegram privacy.",
                        chat_id,
                    )
            else:
                ok = send_audio_bytes(
                    chat_id, audio, http_client, reply_to_message_id,
                )
                endpoint = "sendAudio"
            logger.info(
                "DISPATCH SENT chat=%s ok=%s bytes=%d endpoint=%s text=%r",
                chat_id, ok, len(audio), endpoint, (text or "")[:60],
            )
        except Exception as exc:
            logger.debug("audio send failed in dispatch: %s", exc)
        finally:
            # Liberar referencias al buffer de audio inmediatamente
            # para evitar cualquier ilusión de "audio arrastrado".
            try:
                del audio
            except Exception:
                pass


def send_audio_bytes(
    chat_id: int,
    audio: bytes,
    http_client: Any,
    reply_to_message_id: Optional[int] = None,
) -> bool:
    """POST sendAudio a Telegram (MP3, formato legacy de música).

    NOTA: sendAudio dispara autoplay del siguiente en el cliente
    Telegram. Para chat conversacional usar `send_voice_bytes`
    (sendVoice/Opus). Este endpoint queda como compat para 'audio' mode.
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


def _send_voice_with_forbidden_flag(
    chat_id: int,
    audio: bytes,
    http_client: Any,
    reply_to_message_id: Optional[int] = None,
) -> tuple:
    """Wrapper sobre send_voice_bytes que además detecta el error
    VOICE_MESSAGES_FORBIDDEN y lo expone al caller via flag.

    Devuelve: (ok: bool, voice_forbidden: bool).
    """
    if not audio:
        return False, False
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.warning("sendVoice: TELEGRAM_BOT_TOKEN missing")
        return False, False

    url = f"https://api.telegram.org/bot{token}/sendVoice"
    files = {"voice": ("vectrax.ogg", audio, "audio/ogg")}
    data = {"chat_id": str(chat_id)}
    if reply_to_message_id:
        data["reply_to_message_id"] = str(reply_to_message_id)
        data["allow_sending_without_reply"] = "true"
    try:
        r = http_client.post(url, data=data, files=files)
        if r.status_code == 200:
            return True, False
        body = (r.text or "")[:300]
        forbidden = (
            r.status_code == 400
            and "VOICE_MESSAGES_FORBIDDEN" in body
        )
        if not forbidden:
            logger.warning("sendVoice HTTP %d: %s", r.status_code, body)
        return False, forbidden
    except Exception as exc:
        logger.warning("sendVoice crashed: %s", exc)
        return False, False


def send_voice_bytes(
    chat_id: int,
    audio: bytes,
    http_client: Any,
    reply_to_message_id: Optional[int] = None,
) -> bool:
    """POST sendVoice a Telegram (OGG/Opus, voice memo, SIN autoplay).

    Fix estructural del bug 'el audio anterior se reproduce'. Telegram
    no encadena voice memos en autoplay; el usuario los reproduce de a
    uno con play explícito.

    Si el destinatario tiene VOICE_MESSAGES_FORBIDDEN en privacy, el
    endpoint devuelve 403; el caller queda con texto-solo (degradación
    natural, no error fatal).

    Requiere bytes en formato OGG/Opus (Telegram rechaza MP3 aquí o
    lo trata mal). El synthesizer debe llamarse con fmt='opus'.
    """
    if not audio:
        return False
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.warning("sendVoice: TELEGRAM_BOT_TOKEN missing")
        return False

    url = f"https://api.telegram.org/bot{token}/sendVoice"
    files = {
        "voice": ("vectrax.ogg", audio, "audio/ogg"),
    }
    data = {
        "chat_id": str(chat_id),
    }
    if reply_to_message_id:
        data["reply_to_message_id"] = str(reply_to_message_id)
        data["allow_sending_without_reply"] = "true"
    try:
        r = http_client.post(url, data=data, files=files)
        if r.status_code != 200:
            logger.warning("sendVoice HTTP %d: %s",
                           r.status_code, r.text[:200])
            return False
        return True
    except Exception as exc:
        logger.warning("sendVoice crashed: %s", exc)
        return False
