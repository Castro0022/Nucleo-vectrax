"""
Vectrax — Telegram Gateway (Scalable)
========================================
Gateway ultra-ligero: SOLO hace I/O con Telegram.
Todo el procesamiento pesado va al Pipeline Worker via cola SQLite.

Flujo:
  1. Polling → recibe mensaje
  2. Fast-path? → responde instantáneo (0ms)
  3. No fast-path → encola en SQLite (<1ms)
  4. Thread espera respuesta del worker (poll cada 300ms)
  5. Respuesta llega → envía al usuario

El gateway NUNCA ejecuta el pipeline cognitivo.
Nunca se traba. Nunca consume >50MB.

Creado: 2026-03-19 | Escalable: 2026-03-28
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
import signal
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# --- Logging: stdout + persistent file ---
_LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
_LOG_DATE = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(level=logging.INFO, format=_LOG_FMT, datefmt=_LOG_DATE)
logger = logging.getLogger("vectrax.telegram_gateway")

# Persistent log file (survives stdout=DEVNULL from supervisor)
_LOG_DIR = os.path.join(os.path.expanduser("~"), ".vectrax")
os.makedirs(_LOG_DIR, exist_ok=True)
_file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(_LOG_DIR, "gateway.log"),
    maxBytes=5 * 1024 * 1024,  # 5 MB
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter(_LOG_FMT, datefmt=_LOG_DATE))
logger.addHandler(_file_handler)

TELEGRAM_API = "https://api.telegram.org/bot{token}"
POLL_TIMEOUT = 30
RETRY_DELAY = 15          # espera 15s entre reintentos (antes 5s)
MAX_CONSECUTIVE_ERRORS = 10
CONFLICT_RETRY_DELAY = 60  # 409 Conflict: espera 60s antes de reintentar
RESPONSE_WAIT_TIMEOUT = 25.0
WORKERS = 6
HEARTBEAT_INTERVAL = 10  # seconds between heartbeat writes
STATUS_LOG_INTERVAL = 300  # log status summary every 5 min
POLL_CLIENT_REFRESH = 1800  # recreate HTTP client every 30 min to avoid stale TCP
# POLL_STUCK_THRESHOLD: watchdog kills process if poll hasn't completed in N seconds.
# Must be > POLL_ALARM_TIMEOUT (32s) to give SIGALRM time to fire first,
# but low enough to catch cases where SIGALRM can't interrupt C-level code.
POLL_STUCK_THRESHOLD = 35
POLL_ALARM_TIMEOUT = POLL_TIMEOUT + 2   # 32s: SIGALRM fires before 35s watchdog
_OFFSET_FILE = os.path.join(os.path.expanduser("~"), ".vectrax", "gateway_offset")


# --- SIGALRM handler: interrupts stuck C-level calls (SSL read, etc.) ---
class _PollTimeout(Exception):
    """Raised by SIGALRM when a poll exceeds the hard timeout."""

def _poll_alarm_handler(signum, frame):
    raise _PollTimeout(f"Poll exceeded {POLL_ALARM_TIMEOUT}s hard timeout")


def _load_offset() -> int:
    """Load persisted Telegram offset from disk."""
    try:
        if os.path.exists(_OFFSET_FILE):
            with open(_OFFSET_FILE, "r") as f:
                return int(f.read().strip())
    except Exception:
        pass
    return 0


def _save_offset(offset: int) -> None:
    """Persist Telegram offset to disk so restarts don't re-process messages."""
    try:
        os.makedirs(os.path.dirname(_OFFSET_FILE), exist_ok=True)
        with open(_OFFSET_FILE, "w") as f:
            f.write(str(offset))
    except Exception:
        pass


class TelegramGateway:
    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN vacío")
        self._token = token
        self._base = TELEGRAM_API.format(token=token)
        self._offset: int = _load_offset()
        self._running: bool = False
        self._processed: int = 0
        self._errors: int = 0
        self._polls: int = 0
        self._empty_polls: int = 0
        self._handler_errors: int = 0
        self._last_status_log: float = 0
        self._last_update_time: float = 0
        self._start_time: float = time.time()
        self._poll_http = self._make_poll_client()
        self._poll_http_created: float = time.time()
        self._last_poll_ok: float = time.time()
        self._send_http = httpx.Client(
            timeout=httpx.Timeout(15, connect=5),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        self._dl_http = httpx.Client(timeout=30)
        self._pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="tg")
        self._heartbeat_thread: threading.Thread | None = None
        self._needs_poll_refresh: bool = False
        logger.info("Gateway ready (%d workers, queue-based)", WORKERS)

    @staticmethod
    def _make_poll_client() -> httpx.Client:
        """Create a fresh HTTP client for long-polling.
        Uses max_keepalive_connections=1 to minimize stale TCP connections.
        """
        return httpx.Client(
            timeout=httpx.Timeout(POLL_TIMEOUT + 10, connect=10),
            limits=httpx.Limits(
                max_connections=5,
                max_keepalive_connections=1,
                keepalive_expiry=300,  # 5 min max for idle connections
            ),
        )

    def _refresh_poll_client(self, close_old: bool = False) -> None:
        """Create a fresh poll HTTP client.

        If close_old=True, closes the previous client. Only safe when the
        caller guarantees no request is active on it (e.g., after SIGALRM
        interrupt where the poll was already aborted, or in main thread
        between polls).

        If close_old=False (default), abandons the old client. Used when:
          - The old client is already in is_closed state (close() is no-op).
          - Caller can't guarantee no active request on the old client
            (e.g., heartbeat thread — would deadlock; but heartbeat now
            uses the flag pattern instead of calling this method).

        Original deadlock bug (3600+75 crash loop): the heartbeat thread
        used to call this with an effective close_old=True while the main
        thread held an active post() on the same client. Fixed by the
        flag pattern in _heartbeat_loop + consumer in run().
        """
        old_client = self._poll_http
        self._poll_http = self._make_poll_client()
        self._poll_http_created = time.time()
        if close_old:
            try:
                old_client.close()
            except Exception as _e:
                logger.debug("old_client.close() raised (ignored): %s", _e)
        logger.info("Poll HTTP client refreshed (close_old=%s)", close_old)

    # == Telegram API ======================================================

    def _tg(self, method: str, **p) -> Optional[Dict]:
        try:
            r = self._send_http.post(f"{self._base}/{method}", json=p)
            r.raise_for_status()
            d = r.json()
            return d.get("result") if d.get("ok") else None
        except Exception as e:
            logger.warning("TG %s: %s", method, e)
            return None

    # == Filtro de salida — bloquea fuga de datos internos ====================

    # Patrones que indican datos técnicos internos filtrando hacia el usuario.
    # Ninguno de estos debe aparecer en una respuesta conversacional normal.
    _INTERNAL_LEAK_PATTERNS = [
        (re.compile(r"\bid=[a-f0-9]{6,}\b", re.I),           "id_hash"),
        (re.compile(r"\bnucleo=[a-z]+\s*·", re.I),           "nucleo_metric"),
        (re.compile(r"\bnúcleo=[a-z]+\s*·", re.I),           "nucleo_metric_acent"),
        (re.compile(r"\bpeso=[\d.]+", re.I),                  "peso_metric"),
        (re.compile(r"\bweight=[\d.]+", re.I),                "weight_metric"),
        (re.compile(r"\blayer=[a-z]+", re.I),                 "layer_field"),
        (re.compile(r"\(reversible\)", re.I),                 "reversible_field"),
        (re.compile(r"cuarentena,\s*peso", re.I),             "cuarentena_weight"),
        (re.compile(r"✓\s+Anotado"),                          "anotado_receipt"),
        (re.compile(r"✓\s+Grabado\s+—"),                     "grabado_receipt"),
        (re.compile(r"\bcore_memory\b", re.I),                "core_memory_field"),
        (re.compile(r"\bgravity_score\b", re.I),              "gravity_field"),
        (re.compile(r"\bembedding\b", re.I),                  "embedding_field"),
        (re.compile(r"Traceback\s+\(most recent"),            "traceback"),
        (re.compile(r"absorbido\s+en\s+core", re.I),         "absorbed_in_core"),
    ]

    def _sanitize_for_user(self, text: str) -> str:
        """
        Última línea de defensa antes de enviar a Telegram.
        Detecta datos técnicos internos y los bloquea antes de que lleguen al usuario.
        """
        for pattern, label in self._INTERNAL_LEAK_PATTERNS:
            if pattern.search(text):
                logger.warning(
                    "INTERNAL_LEAK_BLOCKED | pattern=%s | text=%r",
                    label, text[:100],
                )
                # Respuesta neutral que no revela nada
                return "Entendido."
        return text

    def _send(self, cid: int, text: str, **extra) -> bool:
        if not text:
            return False

        # === Sanitize final: bloquear fuga de datos internos ================
        text = self._sanitize_for_user(text)
        if not text:
            return False

        if len(text) > 4096:
            text = text[:4093] + "..."

        # === User Sovereignty Engine
        # Cada envío declara un SendReason. Default USER_REPLY (este sender
        # se invoca como respuesta directa al usuario). Callers internos
        # pueden overridear pasando _sovereignty_reason en extra.
        # Modo observe (default) loggea pero NO bloquea — production-safe.
        try:
            from core.sovereignty import (
                require_authorization, SendReason,
            )
            reason = extra.pop("_sovereignty_reason", SendReason.USER_REPLY)
            sov_user_id = extra.pop("_sovereignty_user_id", None)
            decision = require_authorization(
                chat_id=int(cid),
                reason=reason,
                user_id=sov_user_id,
                payload_size=len(text),
            )
            if not decision.is_allowed:
                logger.warning(
                    "sovereignty: blocked send chat=%s reason=%s rule=%s",
                    cid, reason.value, decision.rule,
                )
                return False
        except Exception as _se:
            # El sovereignty engine NUNCA debe romper un envío legítimo.
            logger.debug("sovereignty bypass on _send: %s", _se)

        try:
            from core.recovery.detectors.hardcoded_handler_runtime import (
                register_response,
            )
            register_response(user_id=str(cid), text=text, lang="")
        except Exception:
            pass
        # sendMessage retorna el message_id del mensaje del bot. Lo
        # capturamos para que el audio sea reply_to_message_id de ESE
        # texto — ancla visualmente texto + audio aunque el TTS llegue
        # fuera de orden temporal.
        result = self._tg("sendMessage", chat_id=cid, text=text, **extra)
        ok = result is not None
        sent_message_id = result.get("message_id") if isinstance(result, dict) else None
        if ok:
            try:
                from core.voice.telegram_dispatch import dispatch_audio_async
                dispatch_audio_async(
                    chat_id=cid,
                    text=text,
                    http_client=self._send_http,
                    executor=self._pool,
                    reply_to_message_id=sent_message_id,
                )
            except Exception as exc:
                logger.debug("audio dispatch swallowed: %s", exc)
        return ok

    def _send_photo(self, cid: int, photo_url: str, caption: str = "") -> bool:
        """Send a photo to Telegram by URL."""
        params = {"chat_id": cid, "photo": photo_url}
        if caption:
            params["caption"] = caption[:1024]
        return self._tg("sendPhoto", **params) is not None

    def _send_photo_bytes(self, cid: int, img_bytes: bytes, caption: str = "") -> bool:
        """Upload a photo to Telegram as binary file (for b64-based generation)."""
        try:
            import io
            data = {"chat_id": str(cid)}
            if caption:
                data["caption"] = caption[:1024]
            files = {"photo": ("image.png", io.BytesIO(img_bytes), "image/png")}
            r = self._send_http.post(
                f"{self._base}/sendPhoto",
                data=data,
                files=files,
            )
            r.raise_for_status()
            return r.json().get("ok", False)
        except Exception as e:
            logger.warning("sendPhoto bytes failed: %s", e)
            return False

    def _venue(self, cid: int, p: Dict) -> bool:
        lat, lng = p.get("lat", 0), p.get("lng", 0)
        if not lat or not lng:
            return False
        t = p.get("nombre", "Lugar")
        ex = []
        if p.get("rating"):
            ex.append(f"{p['rating']}★")
        if p.get("distancia_label"):
            ex.append(p["distancia_label"])
        if ex:
            t = f"{t} — {' — '.join(ex)}"
        return self._tg(
            "sendVenue", chat_id=cid, latitude=lat, longitude=lng,
            title=t, address=p.get("direccion", "") or "Sin dirección",
        ) is not None

    # == Fast-path (instantáneo, sin worker) ===============================

    @staticmethod
    def _fast(text: str, user_id: str = "") -> str:
        t = text.strip().lower().rstrip("!?.")

        # Identity del usuario
        name = ""
        try:
            from vectrax.user_memory import get_user_profile
            profile = get_user_profile(user_id)
            name = profile.get("name", "")
        except Exception:
            pass

        # Saludos (multilingüe)
        _GREETINGS = {
            # Español
            "hola": "es", "buenas": "es", "buenos dias": "es", "buenos días": "es",
            "buenas tardes": "es", "buenas noches": "es", "que tal": "es", "qué tal": "es",
            "saludos": "es",
            # Inglés
            "hi": "en", "hey": "en", "hello": "en", "good morning": "en",
            "good afternoon": "en", "good evening": "en",
            "how are you": "en", "how's it going": "en", "what's up": "en",
            "sup": "en", "howdy": "en",
            # Francés
            "bonjour": "fr", "salut": "fr", "bonsoir": "fr", "coucou": "fr",
            # Italiano
            "ciao": "it", "buongiorno": "it", "buonasera": "it",
            # Alemán
            "hallo": "de", "guten tag": "de", "guten morgen": "de",
            "guten abend": "de", "moin": "de",
            # Portugués
            "olá": "pt", "ola": "pt", "bom dia": "pt", "boa tarde": "pt",
            "boa noite": "pt", "oi": "pt",
            # Holandés
            "hoi": "nl", "goedemorgen": "nl", "goedemiddag": "nl",
        }
        _GREETING_REPLIES = {
            "es": "Núcleo estable.",
            "en": "Core stable.",
            "fr": "Noyau stable.",
            "it": "Nucleo stabile.",
            "de": "Kern stabil.",
            "pt": "Núcleo estável.",
            "nl": "Kern stabiel.",
        }
        lang = _GREETINGS.get(t)
        if lang:
            if lang == "en":
                return (
                    f"Hey{f', {name}' if name else ''}. ¡Aquí estoy, listo para ti.\n"
                    f"{'Add a client: /lead add name' if not name else 'What do you need?'}"
                )
            return (
                f"Hola{f', {name}' if name else ''}. Aquí estoy.\n"
                f"{'Agrega tu primer cliente: /lead add nombre' if not name else '¿Qué necesitas?'}"
            )

        # Agradecimientos → confirmar registro en núcleo (multilingüe)
        _THANKS_MAP = {
            "gracias": "es", "muchas gracias": "es", "mil gracias": "es",
            "thanks": "en", "thank you": "en",
            "merci": "fr", "merci beaucoup": "fr",
            "grazie": "it", "grazie mille": "it",
            "danke": "de", "danke schön": "de", "danke schon": "de",
            "obrigado": "pt", "obrigada": "pt",
            "dank je": "nl", "bedankt": "nl",
        }
        _THANKS_REPLIES = {
            "es": "Registrado en el núcleo.",
            "en": "Registered in core.",
            "fr": "Enregistré dans le noyau.",
            "it": "Registrato nel nucleo.",
            "de": "Im Kern registriert.",
            "pt": "Registrado no núcleo.",
            "nl": "Geregistreerd in de kern.",
        }
        thanks_lang = _THANKS_MAP.get(t)
        if thanks_lang:
            return _THANKS_REPLIES.get(thanks_lang, "Registrado en el núcleo.")

        # Despedidas → confirmar estado (multilingüe)
        _BYES_MAP = {
            "chao": "es", "adiós": "es", "adios": "es", "hasta luego": "es",
            "nos vemos": "es", "chau": "es",
            "bye": "en", "goodbye": "en", "see you": "en",
            "au revoir": "fr", "a bientôt": "fr",
            "arrivederci": "it", "ciao ciao": "it",
            "tschüss": "de", "tschuss": "de", "auf wiedersehen": "de",
            "tchau": "pt", "até logo": "pt",
            "doei": "nl", "tot ziens": "nl",
        }
        _BYES_REPLIES = {
            "es": "Núcleo en reposo. Vectrax permanece activo.",
            "en": "Core in standby. Vectrax remains active.",
            "fr": "Noyau en repos. Vectrax reste actif.",
            "it": "Nucleo in riposo. Vectrax resta attivo.",
            "de": "Kern im Ruhezustand. Vectrax bleibt aktiv.",
            "pt": "Núcleo em repouso. Vectrax permanece ativo.",
            "nl": "Kern in rust. Vectrax blijft actief.",
        }
        bye_lang = _BYES_MAP.get(t)
        if bye_lang:
            return _BYES_REPLIES.get(bye_lang, "Núcleo en reposo. Vectrax permanece activo.")

        # Confirmaciones (multilingüe)
        _CONFIRM_MAP = {
            "ok": "es", "okay": "es", "sí": "es", "si": "es",
            "listo": "es", "entendido": "es", "perfecto": "es",
            "vale": "es", "claro": "es",
            "yes": "en", "sure": "en", "got it": "en",
            "oui": "fr", "d'accord": "fr",
            "va bene": "it",
            "ja": "de", "genau": "de",
            "sim": "pt", "certo": "pt",
        }
        _CONFIRM_REPLIES = {
            "es": "Registrado.",
            "en": "Registered.",
            "fr": "Enregistré.",
            "it": "Registrato.",
            "de": "Registriert.",
            "pt": "Registrado.",
            "nl": "Geregistreerd.",
        }
        confirm_lang = _CONFIRM_MAP.get(t)
        if confirm_lang:
            return _CONFIRM_REPLIES.get(confirm_lang, "Registrado.")

        # /upgrade command
        if t in ("upgrade", "/upgrade", "pro", "/pro"):
            try:
                from services.billing.stripe_billing import create_checkout_session
                url = create_checkout_session(user_id)
                if url:
                    return (
                        "Vectrax PRO: memoria completa, voz, mapas, mercado, sin límites.\n\n"
                        f"Activar aquí: {url}"
                    )
            except Exception:
                pass
            return "Sistema de pago en configuración."

        # Identidad de Vectrax — respuesta fija desde core_identity, sin LLM

        from vectrax.identity_handler import respond_if_identity
        identity_response = respond_if_identity(t, lang=_lang if "_lang" in locals() else "es")
        if identity_response:
            return identity_response

        # Identidad del usuario
        if re.search(
            r"(?:qui[eé]n soy|c[oó]mo me llamo|cu[aá]l es mi nombre"
            r"|who am i|what'?s my name|me conoces|sabes qui[eé]n soy)", t,
        ):
            try:
                from vectrax.user_memory import resolve_with_memory
                r = resolve_with_memory(user_id, text)
                if r and isinstance(r, dict) and r.get("text"):
                    return r["text"]
            except Exception:
                pass
            if name:
                return f"Eres {name}."

        # Market (btc, eth, etc.)
        if re.match(
            r"^(?:btc|bitcoin|eth|ethereum|bnb|sol|ada|dot|avax|matic|link|xrp)\s*[?]?$", t,
        ):
            try:
                from intents.market_intents import detect_market_intent, handle_market_intent
                detected = detect_market_intent(text)
                if detected:
                    result = handle_market_intent(detected[0], detected[1])
                    if result.get("success") and result.get("response"):
                        return result["response"]
            except Exception:
                pass

        return ""

    # == Heartbeat + periodic status (background thread) ====================

    def _heartbeat_loop(self) -> None:
        """Background daemon thread: writes heartbeat every HEARTBEAT_INTERVAL seconds,
        logs a status summary every STATUS_LOG_INTERVAL seconds,
        and acts as a watchdog for the polling loop.
        """
        while self._running:
            self._write_heartbeat()

            now = time.time()

            # === WATCHDOG: detect stuck poll client ===
            poll_age = now - self._last_poll_ok
            if poll_age > POLL_STUCK_THRESHOLD:
                logger.warning(
                    "WATCHDOG | Poll stuck for %.0fs (>%ds) — forcing process exit for supervisor restart",
                    poll_age, POLL_STUCK_THRESHOLD,
                )
                # CRITICAL: Do NOT call self._poll_http.close() here!
                # close() waits for the active request to finish, which
                # deadlocks because the main thread is stuck in that request.
                # Instead, force-terminate the process. The supervisor will
                # restart us cleanly in ~5 seconds.
                self._write_heartbeat()  # one last heartbeat to log the event
                os._exit(1)

            # === Signal main thread to refresh poll client ===
            # We CAN'T call _refresh_poll_client() here: abandoning the old
            # client without close() leaks sockets/handles (observed: gateway
            # killed by supervisor at uptime=10306s due to stale heartbeat
            # after ~5 refreshes accumulated half-closed resources).
            # Set a flag; main thread does the swap between polls where
            # close() is safe (no active request on the old client).
            if now - self._poll_http_created > POLL_CLIENT_REFRESH and not self._needs_poll_refresh:
                logger.info("REFRESH SIGNAL | Poll client age %.0fs — requesting refresh",
                            now - self._poll_http_created)
                self._needs_poll_refresh = True

            # Periodic status summary
            if now - self._last_status_log >= STATUS_LOG_INTERVAL:
                uptime = int(now - self._start_time)
                h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60
                pool_active = len([t for t in threading.enumerate()
                                   if t.name.startswith("tg")])
                since_last = (
                    f"{now - self._last_update_time:.0f}s ago"
                    if self._last_update_time else "never"
                )
                logger.info(
                    "STATUS | up=%dh%dm%ds | processed=%d | polls=%d "
                    "(empty=%d) | errors=%d | handler_err=%d | "
                    "pool_threads=%d/%d | last_msg=%s",
                    h, m, s, self._processed, self._polls,
                    self._empty_polls, self._errors, self._handler_errors,
                    pool_active, WORKERS, since_last,
                )
                self._last_status_log = now

            # Sleep in small increments so we notice _running=False quickly
            for _ in range(HEARTBEAT_INTERVAL * 2):
                if not self._running:
                    break
                time.sleep(0.5)

    # == Polling loop (NEVER blocks) =======================================

    def run(self) -> None:
        self._running = True

        # Install SIGALRM handler — this is the HARD timeout that can interrupt
        # stuck C-level calls (SSL read, etc.) which freeze the entire process.
        # signal.alarm() operates at the OS level, independent of GIL.
        signal.signal(signal.SIGALRM, _poll_alarm_handler)

        # Start dedicated heartbeat thread (daemon — dies with main process)
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, name="gw-heartbeat", daemon=True,
        )
        self._heartbeat_thread.start()

        if self._offset:
            logger.info("Bot started — resumed at offset %d (heartbeat thread active)",
                        self._offset)
        else:
            logger.info("Bot started — queue-based polling (heartbeat thread active)")

        while self._running:
            try:
                # Refresh poll client if it was closed by watchdog
                if self._poll_http.is_closed:
                    self._refresh_poll_client()

                # Consume refresh signal from heartbeat thread.
                # Safe HERE: we're between polls, no active post() on old client.
                if self._needs_poll_refresh:
                    logger.info("REFRESH | consuming signal from heartbeat thread")
                    old_client = self._poll_http
                    self._poll_http = self._make_poll_client()
                    self._poll_http_created = time.time()
                    self._last_poll_ok = time.time()
                    self._needs_poll_refresh = False
                    try:
                        old_client.close()  # safe: self._poll_http already points to new client
                    except Exception as _e:
                        logger.debug("old_client.close() raised (ignored): %s", _e)

                # Set OS-level hard timeout — interrupts even stuck C calls
                signal.alarm(POLL_ALARM_TIMEOUT)

                t0 = time.time()
                r = self._poll_http.post(
                    f"{self._base}/getUpdates",
                    json={"offset": self._offset, "timeout": POLL_TIMEOUT,
                          "allowed_updates": ["message"]},
                )
                r.raise_for_status()

                # Cancel alarm — poll completed successfully
                signal.alarm(0)

                self._last_poll_ok = time.time()
                poll_ms = (self._last_poll_ok - t0) * 1000
                d = r.json()
                updates = d.get("result", []) if d.get("ok") else []
                self._polls += 1
                self._errors = 0

                if updates:
                    self._last_update_time = time.time()
                    logger.info("POLL #%d | %d updates | %.0fms",
                                self._polls, len(updates), poll_ms)
                else:
                    self._empty_polls += 1
                    if poll_ms > (POLL_TIMEOUT + 5) * 1000:
                        logger.warning("POLL #%d slow empty | %.0fms (>%ds)",
                                       self._polls, poll_ms, POLL_TIMEOUT + 5)

                for u in updates:
                    uid = u.get("update_id", 0)
                    # === Continuity Engine: idempotencia de update_id =====
                    # Si el supervisor reinició el gateway en medio del
                    # procesamiento de un update, Telegram redelivera ese
                    # update_id al levantarse el bot. Sin idempotencia,
                    # se procesa otra vez y la respuesta (texto+audio)
                    # se manda DOS veces — percibido como "el audio
                    # anterior se reproduce". Cierra el bug a nivel
                    # gateway, antes de despachar el handler.
                    if uid:
                        try:
                            from core.continuity import (
                                is_processed, mark_processed,
                            )
                            if is_processed(uid):
                                logger.warning(
                                    "IDEMPOTENT skip update_id=%d "
                                    "(redelivery después de restart)", uid,
                                )
                                self._offset = uid + 1
                                continue
                            _cid = str(
                                u.get("message", {})
                                 .get("chat", {})
                                 .get("id", "") or ""
                            )
                            mark_processed(uid, chat_id=_cid)
                        except Exception as _ie:
                            logger.debug(
                                "continuity ledger skip (passthrough): %s",
                                _ie,
                            )
                    self._offset = uid + 1
                    self._pool.submit(self._handle, u)

                # Persist offset after processing updates
                if updates:
                    _save_offset(self._offset)

            except _PollTimeout:
                # SIGALRM fired — the poll was stuck at the C/OS level.
                signal.alarm(0)  # cancel any pending alarm
                logger.warning(
                    "SIGALRM | Poll exceeded %ds hard timeout — abandoning dead socket",
                    POLL_ALARM_TIMEOUT,
                )
                # ROOT CAUSE FIX (REPEAT FAILURE #324, every ~10800s):
                # close_old=False — do NOT call old_client.close() here.
                #
                # When Telegram's load balancer silently drops the TCP connection
                # at ~3h (10800s), the next poll blocks on a dead SSL socket.
                # SIGALRM interrupts the Python frame, but old_client.close() then
                # attempts an SSL shutdown handshake on the broken socket, which
                # blocks at the C level, holds the GIL, and prevents the heartbeat
                # daemon thread from running → os._exit() never fires → supervisor
                # detects stale heartbeat (40s threshold) and kills the process.
                #
                # Abandoning the socket (close_old=False) leaks one fd every ~3h —
                # acceptable. The garbage collector will eventually reclaim it.
                self._refresh_poll_client(close_old=False)
                self._last_poll_ok = time.time()
                # Fresh client just created; any pending refresh signal is obsolete.
                self._needs_poll_refresh = False
                continue

            except Exception as e:
                signal.alarm(0)  # cancel any pending alarm
                err_str = str(e)
                # 409 Conflict = otra instancia corriendo — esperar más y resetear
                if "409" in err_str or "Conflict" in err_str:
                    logger.warning(
                        "409 Conflict detectado — otra instancia activa. "
                        "Esperando %ds antes de reintentar...", CONFLICT_RETRY_DELAY,
                    )
                    time.sleep(CONFLICT_RETRY_DELAY)
                    self._errors = 0
                    continue
                self._errors += 1
                logger.error("Poll (%d/%d): %s\n%s", self._errors,
                             MAX_CONSECUTIVE_ERRORS, e, traceback.format_exc())
                if self._errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.critical("MAX POLL ERRORS — shutting down gateway")
                    self._running = False
                    break
                time.sleep(RETRY_DELAY)

        signal.alarm(0)  # cleanup
        logger.info("Bot stopped | processed=%d | polls=%d | errors=%d",
                    self._processed, self._polls, self._handler_errors)

    @staticmethod
    def _write_heartbeat() -> None:
        try:
            hb_path = os.path.join(os.path.expanduser("~"), ".vectrax", "gateway_heartbeat")
            os.makedirs(os.path.dirname(hb_path), exist_ok=True)
            with open(hb_path, "w") as f:
                f.write(str(time.time()))
        except Exception:
            pass

    def stop(self):
        self._running = False

    # == Message handler (worker thread) ===================================

    def _handle(self, update: Dict) -> None:
        t_start = time.time()
        _uid_short = "??"
        _text_short = ""
        try:
            msg = update.get("message")
            if not msg:
                logger.debug("HANDLE skip: no message in update %s",
                             update.get("update_id", "?"))
                return
            cid = msg.get("chat", {}).get("id")
            if not cid:
                return
            uid = str(msg.get("from", {}).get("id", "unknown"))
            _uid_short = uid
            tg_uid = f"tg:{uid}"
            text = msg.get("text", "")
            _text_short = text[:40] if text else "(no text)"
            logger.info("RECV %s | %s", uid, _text_short)

            # Location
            loc = msg.get("location")
            if loc:
                lat, lng = loc.get("latitude", 0), loc.get("longitude", 0)
                if lat and lng:
                    try:
                        from vectrax.user_memory import store_user_location
                        store_user_location(tg_uid, lat, lng)
                        self._send(cid, "✅ Ubicación recibida.",
                                   reply_markup={"remove_keyboard": True})
                    except Exception:
                        pass
                if not text:
                    return

            # Voice
            voice = msg.get("voice") or msg.get("audio")
            if voice and not text:
                text = self._voice(cid, voice)
                if not text:
                    return

            # Photo — analyze with GPT-4o vision
            photo = msg.get("photo")

            if not text and not photo:
                return

            # === TIER CHECK: verificar límite diario ANTES de todo ===
            # Cuenta cada mensaje del usuario (texto, voz, foto, documento).
            # NO cuenta respuestas del bot, avisos del sistema ni mensajes internos.
            # Comandos /upgrade y /start pasan sin contar.
            _t = (text or "").strip()
            _t_lower = _t.lower().rstrip("!?.")
            _is_upgrade_cmd = _t_lower in ("upgrade", "/upgrade", "pro", "/pro")
            _is_start_cmd = _t_lower in ("/start", "start")

            if not self._is_creator(tg_uid) and not _is_upgrade_cmd and not _is_start_cmd:
                try:
                    from core.operator.user_tiers import check_access
                    access = check_access(tg_uid)
                    if not access.allowed:
                        if access.reason:
                            self._send(cid, access.reason)
                        # Silently ignore if already notified (reason is empty)
                        return
                except Exception:
                    pass

            # === CONTINUITY REENTRY: record activity to cancel pending reentry ===
            try:
                from core.continuity_reentry import record_activity
                record_activity(tg_uid)
            except Exception:
                pass

            if photo:
                self._handle_photo(cid, tg_uid, photo, caption=msg.get("caption", ""))
                self._processed += 1
                return

            if not text:
                return

            # === PRE-ONBOARDING: comandos que deben funcionar SIEMPRE ===
            # /upgrade, /pro, /help, /privacy pasan sin importar el estado
            # de onboarding. Un usuario nuevo debe poder pagar sin dar nombre.

            if _is_upgrade_cmd:
                try:
                    from services.billing.stripe_billing import create_checkout_session
                    url = create_checkout_session(tg_uid)
                    if url:
                        self._send(cid, (
                            "Vectrax PRO: memoria completa, voz, mapas, "
                            "mercado, sin l\u00edmites.\n\n"
                            f"Activar aqu\u00ed: {url}"
                        ))
                    else:
                        self._send(cid, "Sistema de pago en configuraci\u00f3n.")
                except Exception:
                    self._send(cid, "Sistema de pago en configuraci\u00f3n.")
                self._processed += 1
                return

            # === ONBOARDING — dos pasos: presencia → nombre ===
            _ob = self._get_onboarding_state(tg_uid)

            # Paso 1: usuario nuevo → presencia pura, sin comandos, esperar nombre
            if _ob["state"] == "none":
                self._send_presence(cid, tg_uid)
                return

            # Paso 2: esperando nombre → cualquier mensaje no-comando es el nombre
            if _ob["state"] == "awaiting_name" and not _t.startswith("/"):
                self._complete_onboarding(cid, tg_uid, _t)
                return

            # /start → comportamiento según estado
            if _t.lower() in ("/start", "start"):
                if _ob["state"] == "completed":
                    _name = _ob.get("name", "")
                    self._send(cid, f"Ya estoy aqu\u00ed, {_name}." if _name else "Ya estoy aqu\u00ed.")
                else:
                    self._send_presence(cid, tg_uid)
                return

            # === IDIOMA — preguntar si ambiguo, resolver si respuesta pendiente ===
            if not _t.startswith("/") and self._check_language_ambiguity(cid, tg_uid, _t):
                return

            # === LEAD COMMANDS
            if re.match(r"^/?lead\b", _t, re.IGNORECASE):
                self._handle_lead(cid, tg_uid, _t)
                return

            # === HELP COMMAND: /help — lista de comandos ===
            if re.match(r"^/?help\b", _t, re.IGNORECASE):
                try:
                    from core.language_gate import get_user_language
                    _hl = get_user_language(tg_uid, "")
                except Exception:
                    _hl = "es"
                if _hl == "en":
                    self._send(cid, (
                        "⚡ VECTRAX — Commands\n\n"
                        "📂 Leads:\n"
                        "/lead add <name> [note]\n"
                        "/lead view <name>\n"
                        "/lead summary\n"
                        "/lead follow <name>\n"
                        "/lead won <name>\n"
                        "/lead lost <name>\n\n"
                        "👥 Team:\n"
                        "/team new <name>\n"
                        "/team join <code>\n"
                        "/team note <text>\n\n"
                        "📊 System:\n"
                        "/vx stats\n"
                        "/vx up\n"
                        "/privacy\n\n"
                        "🧠 Natural language:\n"
                        "You can also write normally:\n"
                        '\"Follow up with Carlos\"\n'
                        '\"He says it\'s expensive\"\n'
                        '\"Call tomorrow\"\n\n'
                        "Vectrax understands."
                    ))
                else:
                    self._send(cid, (
                        "⚡ VECTRAX — Control\n\n"
                        "📂 Leads:\n"
                        "/lead add <nombre> [nota]\n"
                        "/lead view <nombre>\n"
                        "/lead summary\n"
                        "/lead follow <nombre>\n"
                        "/lead won <nombre>\n"
                        "/lead lost <nombre>\n\n"
                        "👥 Equipo:\n"
                        "/team new <nombre>\n"
                        "/team join <código>\n"
                        "/team note <texto>\n\n"
                        "📊 Sistema:\n"
                        "/vx stats\n"
                        "/vx up\n"
                        "/privacy\n\n"
                        "—\n\n"
                        "🌐 Capacidades:\n"
                        "• Buscar información online\n"
                        "• Encontrar direcciones y lugares\n"
                        "• Detectar silencios en conversaciones\n"
                        "• Sugerir el próximo paso automáticamente\n"
                        "• Recordarte seguimientos importantes\n"
                        "• Analizar lo que te dicen (precio, dudas, interés)\n\n"
                        "—\n\n"
                        "🧠 Uso natural:\n"
                        "Puedes escribir como hablas:\n"
                        '\"Buscar restaurante cerca\"\n'
                        '\"Seguimiento a Carlos mañana\"\n'
                        '\"Dice que está caro\"\n'
                        '\"Dónde queda este lugar\"\n\n'
                        "Vectrax lo entiende.\n\n"
                        "—\n\n"
                        "🔒 Privado. Sin ruido. Preciso."
                    ))
                return

            # === PRIVACY COMMAND: /privacy — soberanía de datos ===
            if re.match(r"^/?privacy\b", _t, re.IGNORECASE):
                try:
                    from core.language_gate import get_user_language
                    _pl = get_user_language(tg_uid, "")
                except Exception:
                    _pl = "es"
                if _pl == "en":
                    self._send(cid, (
                        "🔒 Your data sovereignty:\n\n"
                        "• Your data is stored exclusively on your private server\n"
                        "• Never shared with third parties\n"
                        "• No profiles sold or analyzed externally\n"
                        "• You can delete everything at any time\n"
                        "• AI processing uses only your data as context\n\n"
                        "Vectrax does not store message content — only abstract patterns."
                    ))
                else:
                    self._send(cid, (
                        "🔒 Soberanía de tus datos:\n\n"
                        "• Tus datos se guardan exclusivamente en tu servidor privado\n"
                        "• Nunca se comparten con terceros\n"
                        "• Sin perfiles vendidos ni analizados externamente\n"
                        "• Puedes borrar todo en cualquier momento\n"
                        "• El procesamiento de IA solo usa tu información como contexto\n\n"
                        "Vectrax no almacena contenido de mensajes — solo patrones abstractos."
                    ))
                return

            # === TASKS COMMANDS: /tasks — tareas programadas ===
            if re.match(r"^/?(?:tasks|remind|schedule|recordar|alarma)\b", _t, re.IGNORECASE):
                self._handle_tasks(cid, tg_uid, _t)
                return

            # === TEAM COMMANDS: /team — accesibles a todos los usuarios ===
            if re.match(r"^/?team\b", _t, re.IGNORECASE):
                self._handle_team(cid, tg_uid, _t)
                return

            # === CREATOR MODE: /vx commands (solo creador) ===
            if _t.startswith("/vx") or (self._is_creator(tg_uid) and _t.lower().startswith("vx ")):
                # Normalizar: si no tiene /, agregarla
                if not _t.startswith("/"):
                    _t = "/" + _t
                self._handle_vx(cid, tg_uid, _t)
                return

            # Tier management (creator only)
            tier_m = re.match(r"^tier\s+(free|pro|creator)\s+(.+)", text.strip(), re.I)
            if tier_m:
                try:
                    from core.operator.user_tiers import set_tier, Tier, can_use_feature
                    if not can_use_feature(tg_uid, "approve_rules"):
                        self._send(cid, "Solo el creador puede cambiar tiers.")
                        return
                    new_tier = Tier(tier_m.group(1).lower())
                    target = tier_m.group(2).strip()
                    if not target.startswith("tg:"):
                        target = f"tg:{target}"
                    set_tier(target, new_tier)
                    self._send(cid, f"✅ {target} → {new_tier.value.upper()}")
                except Exception as e:
                    self._send(cid, f"Error: {e}")
                return

            # Rule approval (CREATOR ONLY)
            m = re.match(r"^(?:aprobar|approve)\s+(RULE-\w+)", text.strip(), re.I)
            if m:
                if not self._is_creator(tg_uid):
                    pass  # treat as normal conversation, fall through to pipeline
                else:
                    try:
                        from core.learn.learned_rules import get_rules_store
                        rid = m.group(1).upper()
                        if get_rules_store().activate_rule(rid):
                            self._send(cid, f"✅ Regla {rid} activada.")
                        else:
                            self._send(cid, f"Regla {rid} no encontrada.")
                    except Exception as e:
                        self._send(cid, f"Error: {e}")
                    return

            # Idea approval / rejection (CREATOR ONLY)
            m_idea = re.match(
                r"^(?:aprobar|approve)\s+(IDEA-[A-F0-9]{8})(?:\s+(.+))?",
                text.strip(), re.I
            )
            if m_idea:
                if not self._is_creator(tg_uid):
                    pass  # treat as normal conversation
                else:
                    try:
                        from core.idea_store import get_idea_store
                        idea_id = m_idea.group(1).upper()
                        note    = (m_idea.group(2) or "").strip()
                        ok = get_idea_store().approve(idea_id, by=tg_uid, note=note)
                        if ok:
                            self._send(cid, f"✅ {idea_id} aprobada. Lista para implementar.")
                        else:
                            self._send(cid, f"No encontré {idea_id} o ya fue revisada.")
                    except Exception as e:
                        self._send(cid, f"Error: {e}")
                    return

            m_idea_r = re.match(
                r"^(?:rechazar|reject)\s+(IDEA-[A-F0-9]{8})(?:\s+(.+))?",
                text.strip(), re.I
            )
            if m_idea_r:
                if not self._is_creator(tg_uid):
                    pass  # treat as normal conversation
                else:
                    try:
                        from core.idea_store import get_idea_store
                        idea_id = m_idea_r.group(1).upper()
                        note    = (m_idea_r.group(2) or "").strip()
                        ok = get_idea_store().reject(idea_id, by=tg_uid, note=note)
                        if ok:
                            self._send(cid, f"❌ {idea_id} rechazada.")
                        else:
                            self._send(cid, f"No encontré {idea_id} o ya fue revisada.")
                    except Exception as e:
                        self._send(cid, f"Error: {e}")
                    return

            # === LANGUAGE POLICY: detectar idioma + instrucciones ===
            try:
                from core.operator.conversational_policy import (
                    apply_language_policy, detect_explicit_language_instruction,
                    SUPPORTED_LANGUAGES,
                )
                explicit = detect_explicit_language_instruction(text)
                if explicit:
                    apply_language_policy(tg_uid, text)
                    lang_name = SUPPORTED_LANGUAGES.get(explicit, explicit)
                    self._send(cid, f"OK. {lang_name}.")
                    self._processed += 1
                    return
                # Apply policy (detect/persist, no side effect on response)
                apply_language_policy(tg_uid, text)
            except Exception:
                pass

            # === IMAGE GENERATION: crear imágenes con gpt-image-1 ===
            try:
                from vectrax.integrations.vision import detect_generation_intent, generate_image
                _gen_prompt = detect_generation_intent(text)
                if _gen_prompt:
                    self._send(cid, "⏳ Generando imagen...")
                    _img_data = generate_image(_gen_prompt)
                    if _img_data:
                        # generate_image returns bytes (gpt-image-1 b64 response)
                        self._send_photo_bytes(cid, _img_data, caption=_gen_prompt[:200])
                        logger.info("IMG %s | %s", uid, _gen_prompt[:40])
                    else:
                        lang_img = "es"
                        try:
                            from core.language_gate import get_user_language
                            lang_img = get_user_language(tg_uid, text)
                        except Exception:
                            pass
                        if lang_img == "en":
                            self._send(cid, "Couldn't generate the image. Check OPENAI_API_KEY and project permissions.")
                        else:
                            self._send(cid, "No pude generar la imagen. Revisa OPENAI_API_KEY y permisos del proyecto.")
                    self._processed += 1
                    return
            except Exception as _img_exc:
                logger.warning("IMAGE GENERATION block failed: %s", _img_exc)

            # === WEATHER: respuesta directa de clima ===
            try:
                from vectrax.integrations.weather import detect_weather_intent, get_weather_text
                _weather_city = detect_weather_intent(text)
                if _weather_city:
                    _wlang = "es"
                    try:
                        from core.language_gate import get_user_language
                        _wlang = get_user_language(tg_uid, text)
                    except Exception:
                        pass
                    _weather_resp = get_weather_text(_weather_city, lang=_wlang)
                    self._send(cid, _weather_resp)
                    self._processed += 1
                    logger.info("WEATHER %s | %s → %d ch", uid, _weather_city, len(_weather_resp))
                    return
            except Exception:
                pass

            # === FAST-PATH: respuesta instantánea ===
            fast = self._fast(text, tg_uid)
            if fast:
                # Aplicar language gate al fast-path
                try:
                    from core.language_gate import enforce_language, get_user_language
                    _fast_lang = get_user_language(tg_uid, text)
                    fast = enforce_language(fast, _fast_lang, tg_uid)
                except Exception:
                    pass
                self._send(cid, fast)
                self._processed += 1
                # Places map pins (non-blocking, best-effort)
                self._places(cid, tg_uid, text)
                logger.info("FAST %s | %s → %d ch", uid, text[:30], len(fast))
                return

            # === SCHEDULING: recordatorios y tareas programadas ===
            if self._detect_schedule(cid, tg_uid, text):
                self._processed += 1
                return

            # === LOAD GOVERNOR + QUEUE GATE ===
            try:
                from core.operator.load_governor import get_load_governor
                gov = get_load_governor()
                gov.evaluate()  # actualiza nivel de presión

                if not gov.should_accept_any_job():
                    self._send(cid, "Sistema en mantenimiento. Intenta en un momento.")
                    logger.warning("RED: rejected job from %s", uid)
                    return

                if not gov.should_accept_heavy_job():
                    self._send(cid, "Sistema con alta carga. Solo consultas rápidas por ahora.")
                    logger.info("ORANGE: rejected heavy job from %s", uid)
                    return
            except Exception:
                pass

            try:
                from core.operator.system_monitor import should_accept_job
                accepted, reason = should_accept_job(tg_uid, text)
                if not accepted:
                    if reason == "duplicate":
                        logger.info("SKIP duplicate %s | %s", uid, text[:30])
                    elif reason == "queue_full":
                        self._send(cid, "Sistema ocupado. Intenta en un momento.")
                    return
            except Exception:
                pass

            # === QUEUE-PATH: fire-and-forget (worker envía directo) ===
            from core.transport.message_queue import enqueue
            msg_id = enqueue(tg_uid, cid, text, "telegram")
            self._processed += 1
            logger.info("QUEUED %s | %s → %s", uid, text[:30], msg_id)

        except Exception as e:
            self._handler_errors += 1
            logger.error(
                "HANDLE ERROR user=%s text=%s | %s\n%s",
                _uid_short, _text_short, e, traceback.format_exc(),
            )
            try:
                cid = update.get("message", {}).get("chat", {}).get("id")
                if cid:
                    self._send(cid, "Error interno. Intenta de nuevo.")
            except Exception:
                pass
        finally:
            elapsed = (time.time() - t_start) * 1000
            if elapsed > 2000:  # warn if handler takes >2s
                logger.warning("HANDLE SLOW user=%s | %.0fms | %s",
                               _uid_short, elapsed, _text_short)

    # == Sub-handlers ======================================================

    def _voice(self, cid: int, voice: Dict) -> str:
        fid = voice.get("file_id", "")
        if not fid:
            return ""
        try:
            info = self._tg("getFile", file_id=fid)
            if not info or "file_path" not in info:
                return ""
            url = f"https://api.telegram.org/file/bot{self._token}/{info['file_path']}"
            r = self._dl_http.get(url)
            r.raise_for_status()
            tmp = os.path.join(tempfile.gettempdir(), "vectrax_voice")
            os.makedirs(tmp, exist_ok=True)
            path = os.path.join(tmp, f"v_{fid[:12]}.ogg")
            with open(path, "wb") as f:
                f.write(r.content)
            text = self._transcribe(path)
            try:
                os.unlink(path)
            except OSError:
                pass
            if not text:
                self._send(cid, "No pude transcribir el audio.")
                return ""
            # Quality gate: si la transcripción es basura (caracteres
            # raros, demasiado corta, baja densidad alfabética), NO
            # procesar como mensaje real. Whisper a veces falla con
            # voice-notes muy cortos o ruidosos y devuelve cosas como
            # '.INNNl.hlد.دlلllll1.'. Si ese texto entra al pipeline,
            # el LLM inventa una respuesta genérica.
            if not _is_quality_transcription(text):
                logger.info("VOICE LOW-QUALITY skip | %r", text[:80])
                self._send(cid, "No te entendí bien. ¿Podés repetir?")
                return ""
            return text
        except Exception as e:
            logger.warning("Voice: %s", e)
            return ""

    @staticmethod
    def _transcribe(path: str) -> Optional[str]:
        key = os.environ.get("OPENAI_API_KEY", "")
        if key:
            try:
                import requests as _rq
                with open(path, "rb") as f:
                    r = _rq.post(
                        "https://api.openai.com/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {key}"},
                        files={"file": (os.path.basename(path), f, "audio/ogg")},
                        data={"model": "whisper-1", "language": "es"},
                        timeout=25,
                    )
                    r.raise_for_status()
                    return r.json().get("text", "").strip() or None
            except Exception:
                pass
        return None

    # == Creator commands (/vx) =============================================

    _CREATOR_ID = "tg:2030762343"  # Mario Bravo Castro

    @classmethod
    def _is_creator(cls, tg_uid: str) -> bool:
        """Verifica si el usuario es el creador (hardcoded + env override)."""
        env_id = os.environ.get("VX_CREATOR_ID", "")
        allowed = {cls._CREATOR_ID}
        if env_id:
            uid_str = env_id if env_id.startswith("tg:") else f"tg:{env_id}"
            allowed.add(uid_str)
        return tg_uid in allowed

    def _handle_vx(self, cid: int, tg_uid: str, text: str) -> None:
        """Procesa comandos /vx — solo para el creador."""
        if not self._is_creator(tg_uid):
            self._send(cid, "Acceso denegado.")
            return

        parts = text.split(None, 2)
        cmd = parts[1].lower() if len(parts) > 1 else "help"
        arg = parts[2].strip() if len(parts) > 2 else ""

        try:
            if cmd == "help":
                self._send(cid, (
                    "/vx help — esta ayuda\n"
                    "/vx lang <code> — forzar tu idioma (es/en/fr/...)\n"
                    "/vx lang <uid> <code> — forzar idioma de un usuario\n"
                    "/vx core — ver tu core_memory\n"
                    "/vx core <uid> — ver core_memory de un usuario\n"
                    "/vx memory — ver perfil + hechos\n"
                    "/vx stats — estado del sistema\n"
                    "/vx up — uptime + estado rápido\n"
                    "/vx market [snapshot|status] — mercado\n"
                    "/vx btc | eth | sol — precio directo\n"
                    "/vx fallbacks — intenciones fallidas (7 días)\n"
                    "/vx users — usuarios activos\n"
                    "/vx flush — limpiar cache de sesión\n"
                    "/vx sql <query> — consulta SQL directa (solo lectura)\n"
                    "/vx presencia [on|off] — modo Presencia Pura (zero tokens)"
                ))

            elif cmd == "lang":
                self._vx_lang(cid, tg_uid, arg)

            elif cmd == "core":
                target = arg if arg else tg_uid
                if not target.startswith("tg:"):
                    target = f"tg:{target}"
                from vectrax.core_memory import get_core_entries
                entries = get_core_entries(target)
                if entries:
                    lines = [f"Core memory ({target}): {len(entries)} entradas"]
                    for e in entries[:15]:
                        lines.append(f"  [{e['category']}] w={e['weight']:.2f} x{e['times_confirmed']} | {e['content']}")
                    self._send(cid, "\n".join(lines))
                else:
                    self._send(cid, f"Core vacío para {target}.")

            elif cmd == "memory":
                target = arg if arg else tg_uid
                if not target.startswith("tg:"):
                    target = f"tg:{target}"
                from vectrax.user_memory import get_user_profile, get_history_count
                profile = get_user_profile(target)
                count = get_history_count(target)
                from vectrax.fact_memory import _get_all_facts
                facts = _get_all_facts(target)
                lines = [f"Perfil ({target}):"]
                for k, v in profile.items():
                    if v:
                        lines.append(f"  {k}: {v}")
                lines.append(f"  interactions: {count}")
                if facts:
                    lines.append(f"\nHechos ({len(facts)}):")
                    for f in facts[:10]:
                        lines.append(f"  [{f['fact_type']}] {f['subject']}: {f['value']} | {f.get('detail', '')}")
                self._send(cid, "\n".join(lines))

            elif cmd == "team":
                # Redirigir al handler público de equipos
                # Formato: /vx team <subcmd> <arg>
                sub_parts = arg.split(None, 1)
                sub_cmd = sub_parts[0].lower() if sub_parts else "info"
                sub_arg = sub_parts[1] if len(sub_parts) > 1 else ""
                self._handle_team(cid, tg_uid, f"/team {sub_cmd} {sub_arg}".strip())

            elif cmd == "stats":
                self._send(cid, self._build_monitor_panel())

            elif cmd in ("market", "btc", "eth", "sol", "bnb", "xrp"):
                # /vx market [snapshot|status|watch]
                # /vx btc [price|trend|1h]
                # /vx eth, /vx sol, etc.
                from intents.market_intents import detect_market_intent, handle_market_intent
                # Construir query para detect_market_intent
                if cmd == "market":
                    raw = f"vx market {arg}" if arg else "vx market snapshot"
                else:
                    raw = f"{cmd} {arg}" if arg else cmd
                detected = detect_market_intent(raw)
                if not detected:
                    # Fallback: precio directo del ticker
                    detected = ("bitcoin_status" if cmd in ("btc", "bitcoin")
                                else "market_price",
                                {"symbol": cmd.upper() + "USDT"})
                intent_name, params = detected
                result = handle_market_intent(intent_name, params)
                if result.get("success") and result.get("response"):
                    self._send(cid, result["response"])
                elif result.get("data"):
                    import json
                    self._send(cid, json.dumps(result["data"], indent=2, ensure_ascii=False)[:4000])
                else:
                    self._send(cid, f"Error de mercado: {result.get('error', 'sin datos')}")

            elif cmd == "cycle":
                from core.operational_cycle import get_cycle_stats
                s = get_cycle_stats(days=7)
                lines = [
                    "🔄 Ciclo Operativo — 7 días",
                    "",
                    f"Total ciclos:  {s['total']}",
                    f"✅ Exitosos:     {s['success']} ({s['success_rate']}%)",
                    f"❌ Vacíos:       {s['empty']}",
                    f"🔍 Reescritos:   {s['rewritten']}",
                    f"⏱ Latencia avg: {s['avg_latency_ms']:.0f}ms",
                    "",
                    f"🚦 Ruta dominante: {s['top_route']}",
                    f"🚧 Intent difícil: {s['hard_intent']}",
                ]
                if s['routes_latency']:
                    lines.append("")
                    lines.append("Latencia por ruta:")
                    for r in s['routes_latency']:
                        lines.append(f"  {r['route']}: {r['avg_ms']:.0f}ms ({r['count']}x)")
                self._send(cid, "\n".join(lines))

            elif cmd == "up":
                # /vx up — uptime del sistema
                import datetime
                try:
                    from core.operator.system_monitor import collect_metrics
                    m = collect_metrics()
                    worker_status = (
                        f"✅ vivo ({m.worker_heartbeat_age_s:.0f}s)"
                        if m.worker_alive else "❌ MUERTO"
                    )
                    # Uptime del proceso gateway (tiempo desde que arrancamos)
                    _uptime = int(time.time() - getattr(self, '_start_time', time.time()))
                    _h = _uptime // 3600
                    _m = (_uptime % 3600) // 60
                    _s = _uptime % 60
                    _st_icon = "🟢" if m.status == "healthy" else "🟡"
                    self._send(cid, (
                        f"⏱ Gateway: {_h}h {_m}m {_s}s\n"
                        f"🔧 Worker: {worker_status}\n"
                        f"📎 Cola: {m.queue_pending} pend | {m.queue_processing} proc\n"
                        f"💾 RAM: {m.memory_mb} MB\n"
                        f"💬 Sesión: {self._processed} msgs\n"
                        f"{_st_icon} {m.status.upper()}"
                    ))
                except Exception as e:
                    self._send(cid, f"Error: {e}")

            elif cmd == "audit":
                from vectrax.response_auditor import (
                    activate_auditor, deactivate_auditor, is_auditor_active,
                )
                if arg.lower() in ("on", "activar", "start"):
                    activate_auditor(tg_uid)
                    self._send(cid, "🔍 Modo auditor activado. Vectrax evaluará sus respuestas antes de enviarlas.")
                elif arg.lower() in ("off", "desactivar", "stop"):
                    deactivate_auditor(tg_uid)
                    self._send(cid, "❌ Modo auditor desactivado.")
                else:
                    estado = "✅ ACTIVO" if is_auditor_active(tg_uid) else "❌ INACTIVO"
                    self._send(cid, (
                        f"🔍 Modo Auditor: {estado}\n\n"
                        "Evalúa cada respuesta antes de enviarla.\n"
                        "Si detecta genericidad la reescribe con datos reales.\n\n"
                        "/vx audit on  — activar\n"
                        "/vx audit off — desactivar"
                    ))

            elif cmd == "proactive":
                # Forzar scan proactivo manual (para testing)
                try:
                    from core.proactive_engine import run_proactive_scan
                    # Wrapper que usa el send del gateway
                    def _gw_send(chat_id: int, text: str) -> bool:
                        return self._send(chat_id, text)
                    n = run_proactive_scan(_gw_send)
                    self._send(cid, f"Scan proactivo completado. Mensajes enviados: {n}")
                except Exception as e:
                    self._send(cid, f"Error: {e}")

            elif cmd == "ideas":
                try:
                    from core.idea_store import get_idea_store, IdeaStatus
                    store = get_idea_store()
                    # Refrescar ideas desde todos los módulos
                    added = store.refresh()
                    show_all = arg.lower() in ("all", "todas", "todo")
                    panel = store.build_panel(n=5, show_all_statuses=show_all)
                    if sum(added.values()):
                        panel += f"\n\n+{sum(added.values())} ideas nuevas importadas."
                    self._send(cid, panel)
                except Exception as e:
                    self._send(cid, f"Error al cargar ideas: {e}")
                    logger.error("/vx ideas error: %s", e)

            elif cmd == "fallbacks":
                try:
                    from core.fallback_intents import get_top_fallbacks, get_summary
                    summary = get_summary(days=7)
                    top = get_top_fallbacks(limit=8, days=7)
                    lines = [
                        f"🔄 Fallbacks — últimos 7 días",
                        f"",
                        f"Total: {summary['total']}",
                        f"Intent más frecuente: {summary['top_intent']}",
                        f"Razón más común: {summary['top_reason']}",
                        f"Modo que más falla: {summary['top_mode']}",
                    ]
                    if top:
                        lines.append("")
                        lines.append("Top intents sin resolver:")
                        for item in top:
                            lines.append(f"  {item['intent_category']}: {item['count']}x")
                    self._send(cid, "\n".join(lines))
                except Exception as e:
                    self._send(cid, f"Error: {e}")

            elif cmd == "users":
                import sqlite3
                db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vault", "user_memory.db")
                conn = sqlite3.connect(db)
                rows = conn.execute(
                    "SELECT user_id, name, language FROM profiles ORDER BY updated_at DESC LIMIT 20"
                ).fetchall()
                conn.close()
                lines = ["Usuarios:"]
                for uid, name, lang in rows:
                    lines.append(f"  {uid} | {name or '(sin nombre)'} | {lang or '?'}")
                self._send(cid, "\n".join(lines))

            elif cmd == "presencia":
                from core.nucleus.presencia_pura import activate, deactivate, status
                if not arg or arg.lower() == "status":
                    s = status()
                    icon = "\u26ab" if s["active"] else "\u2705"
                    self._send(cid, (
                        f"{icon} Modo: {s['mode']}\n"
                        f"LLM bloqueado: {'Sí' if s['llm_blocked'] else 'No'}\n"
                        f"Búsqueda externa: {'Bloqueada' if s['online_blocked'] else 'Activa'}\n"
                        f"Convergencia: Activa (siempre)\n"
                        f"Memoria: Activa (siempre)\n"
                        + (f"Activado por: {s['activated_by']}\n" if s['activated_by'] else "")
                        + (f"Desde: {s['activated_at'][:19]}" if s['activated_at'] else "")
                    ))
                elif arg.lower() in ("on", "activar", "1"):
                    result = activate(activated_by=tg_uid)
                    self._send(cid, (
                        "\u26ab Presencia Pura activada.\n"
                        "Núcleo activo. Tokens externos bloqueados.\n"
                        "Solo rutas internas: memoria, identidad, convergencia."
                    ))
                elif arg.lower() in ("off", "desactivar", "0"):
                    result = deactivate(deactivated_by=tg_uid)
                    self._send(cid, (
                        "\u2705 Modo STANDARD restaurado.\n"
                        "Todos los motores disponibles."
                    ))
                else:
                    self._send(cid, (
                        "Uso:\n"
                        "/vx presencia       — ver estado\n"
                        "/vx presencia on    — activar\n"
                        "/vx presencia off   — desactivar"
                    ))

            elif cmd == "flush":
                from vectrax.identity_anchor import _session
                _session.clear_all()
                self._send(cid, "Cache de sesión limpiado.")

            elif cmd == "sql":
                if not arg:
                    self._send(cid, "Uso: /vx sql SELECT ...")
                    return
                if not arg.strip().upper().startswith("SELECT"):
                    self._send(cid, "Solo SELECT permitido.")
                    return
                import sqlite3
                db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vault", "user_memory.db")
                conn = sqlite3.connect(db)
                rows = conn.execute(arg).fetchall()
                conn.close()
                if rows:
                    lines = [str(r) for r in rows[:20]]
                    self._send(cid, "\n".join(lines))
                else:
                    self._send(cid, "(sin resultados)")

            else:
                self._send(cid, f"Comando /vx {cmd} no reconocido. Usa /vx help.")

        except Exception as e:
            self._send(cid, f"Error: {e}")
            logger.error("/vx %s error: %s", cmd, e)

    # == Lead commands =====================================================

    def _handle_lead(self, cid: int, tg_uid: str, text: str) -> None:
        """
        Comandos de seguimiento de leads.

        /lead add <nombre> [| contexto]   — agregar lead
        /lead list                         — ver todos los leads
        /lead contact <nombre> [nota]      — registrar contacto (reset timer)
        /lead proposal <nombre>            — marcar propuesta enviada
        /lead won <nombre>                 — cerrado con éxito
        /lead lost <nombre>                — cerrado/perdido
        /lead follow <nombre>              — generar mensaje de seguimiento ahora
        """
        from core.lead_tracker import (
            add_lead, update_lead_status, record_contact,
            get_leads, generate_followup, format_lead_list,
        )
        try:
            from core.language_gate import get_user_language
            lang = get_user_language(tg_uid, "")
        except Exception:
            lang = "es"

        clean = re.sub(r"^/?lead\s*", "", text, flags=re.IGNORECASE).strip()
        parts = clean.split(None, 2)
        sub = parts[0].lower() if parts else "list"
        arg = parts[1].strip() if len(parts) > 1 else ""
        extra = parts[2].strip() if len(parts) > 2 else ""

        try:
            if sub in ("add", "nuevo", "agregar"):
                if not arg:
                    self._send(cid, "Uso: /lead add <nombre> [contexto]")
                    return
                # Contexto puede venir separado por | o como tercer arg
                name = arg
                ctx = extra
                if "|" in arg:
                    parts2 = arg.split("|", 1)
                    name = parts2[0].strip()
                    ctx = parts2[1].strip()
                result = add_lead(tg_uid, name, context=ctx)
                if result["action"] == "updated":
                    self._send(cid, f"✅ {name} actualizado. Sigo el seguimiento.")
                else:
                    ctx_note = f" — {ctx}" if ctx else ""
                    self._send(cid, f"{name} guardado{ctx_note}.\nHaré seguimiento si no responde.")

            elif sub in ("summary", "resumen", "hot", "calientes"):
                # Vista rápida de leads calientes + preferencias conocidas
                leads = get_leads(tg_uid)
                if not leads:
                    self._send(cid, "No hay leads activos.")
                    return
                try:
                    from core.preference_tracker import build_contact_context
                except Exception:
                    build_contact_context = lambda u, n: ""

                # Ordenar por urgencia: más días sin contacto primero
                leads_sorted = sorted(leads, key=lambda l: l["days_silent"], reverse=True)
                status_icons = {
                    "contacted": "🟡",
                    "proposal_sent": "🔵",
                    "negotiating": "🟢",
                    "silent": "🔴",
                }
                lines = ["📊 Leads activos — prioridad:"]
                for lead in leads_sorted[:6]:
                    icon = status_icons.get(lead["status"], "⚪")
                    days = lead["days_silent"]
                    urgency = f" ⚠️ {days:.0f}d" if days >= 2 else " • reciente" if days < 0.5 else f" • {days:.0f}d"
                    ctx_note = f"\n     {lead['context'][:50]}" if lead.get("context") else ""
                    pref = build_contact_context(tg_uid, lead["name"])
                    pref_note = f"\n     💡 {pref}" if pref else ""
                    lines.append(f"\n{icon} {lead['name']}{urgency}{ctx_note}{pref_note}")
                self._send(cid, "\n".join(lines))

            elif sub in ("list", "lista", "ver", ""):
                leads = get_leads(tg_uid)
                if leads:
                    self._send(cid, format_lead_list(leads))
                else:
                    if lang == "en":
                        self._send(cid, "No active leads.\nAdd one with:\n/lead add name")
                    else:
                        self._send(cid, "No tienes leads activos.\nGuarda uno con:\n/lead add nombre")

            elif sub in ("contact", "contacte", "hablé", "llamé", "contacto"):
                if not arg:
                    self._send(cid, "Uso: /lead contact <nombre> [nota]")
                    return
                ok = record_contact(tg_uid, arg, note=extra)
                if ok:
                    self._send(cid, f"✅ Contacto registrado con {arg}. Timer reiniciado.")
                else:
                    self._send(cid, f"Lead '{arg}' no encontrado. Agrégalo con /lead add {arg}")

            elif sub in ("proposal", "propuesta", "envié"):
                if not arg:
                    self._send(cid, "Uso: /lead proposal <nombre>")
                    return
                ok = update_lead_status(tg_uid, arg, "proposal_sent")
                if ok:
                    self._send(cid, f"🔵 Propuesta enviada a {arg}. Vectrax te avisará si no responde en 3 días.")
                else:
                    self._send(cid, f"Lead '{arg}' no encontrado.")

            elif sub in ("won", "cerrado", "gano", "gané"):
                if not arg:
                    self._send(cid, "Uso: /lead won <nombre>")
                    return
                ok = update_lead_status(tg_uid, arg, "closed_won", note=extra)
                if ok:
                    self._send(cid, f"✅ {arg} cerrado. Felicidades.")
                else:
                    self._send(cid, f"Lead '{arg}' no encontrado.")

            elif sub in ("lost", "perdido", "perdi"):
                if not arg:
                    self._send(cid, "Uso: /lead lost <nombre>")
                    return
                ok = update_lead_status(tg_uid, arg, "closed_lost", note=extra)
                if ok:
                    self._send(cid, f"❌ {arg} marcado como perdido.")
                else:
                    self._send(cid, f"Lead '{arg}' no encontrado.")

            elif sub in ("view", "ver", "info", "detalle"):
                if not arg:
                    self._send(cid, "Uso: /lead view <nombre>")
                    return
                leads = get_leads(tg_uid)
                lead = next((l for l in leads if l["name"].lower() == arg.lower()), None)
                if not lead:
                    self._send(cid, f"Lead '{arg}' no encontrado.")
                    return
                try:
                    from core.preference_tracker import build_contact_context
                    pref = build_contact_context(tg_uid, lead["name"])
                except Exception:
                    pref = ""
                status_labels = {
                    "contacted": "Contactado",
                    "proposal_sent": "Propuesta enviada",
                    "negotiating": "En negociación",
                    "silent": "Sin respuesta",
                }
                days = lead["days_silent"]
                time_str = "hoy" if days < 0.5 else f"hace {days:.0f}d"
                next_action = (
                    "Hacer seguimiento" if days >= 2
                    else "Esperar respuesta" if lead["status"] == "proposal_sent"
                    else "Mantener contacto"
                )
                lines = [
                    f"👤 {lead['name']}",
                    f"   Estado: {status_labels.get(lead['status'], lead['status'])}",
                    f"   Último movimiento: {time_str}",
                ]
                if lead.get("context"):
                    lines.append(f"   Contexto: {lead['context'][:60]}")
                if pref:
                    lines.append(f"   Preferencia: {pref}")
                lines.append(f"   Siguiente acción: {next_action}")
                self._send(cid, "\n".join(lines))

            elif sub in ("follow", "seguimiento", "mensaje"):
                if not arg:
                    self._send(cid, "Uso: /lead follow <nombre>")
                    return
                leads = get_leads(tg_uid)
                lead = next((l for l in leads if l["name"].lower() == arg.lower()), None)
                if not lead:
                    self._send(cid, f"Lead '{arg}' no encontrado.")
                    return
                msg = generate_followup(
                    lead_name=lead["name"],
                    context=lead["context"],
                    days_silent=lead["days_silent"],
                    status=lead["status"],
                    lang=lang,
                )
                self._send(cid, f"💬 Mensaje sugerido para {lead['name']}:\n\n“{msg}”")

            else:
                self._send(cid, (
                    "Comandos:\n"
                    "  /lead add <nombre> [contexto]\n"
                    "  /lead summary\n"
                    "  /lead view <nombre>\n"
                    "  /lead contact <nombre>\n"
                    "  /lead proposal <nombre>\n"
                    "  /lead follow <nombre>\n"
                    "  /lead won / lost <nombre>"
                ))

        except Exception as e:
            self._send(cid, f"Error: {e}")
            logger.error("lead cmd error: %s", e)

    # == Onboarding — dos pasos idempotentes ===================================

    _welcomed: set = set()  # mantener por compatibilidad con código existente
    _lang_pending: set = set()  # usuarios esperando confirmar su idioma

    def _get_onboarding_state(self, tg_uid: str) -> dict:
        """Lee el estado de onboarding desde vault. Fail-safe: retorna none."""
        try:
            from vectrax.onboarding import get_state
            return get_state(tg_uid)
        except Exception as exc:
            logger.debug("onboarding get_state failed: %s", exc)
            return {"state": "none", "name": "", "welcome_sent": False}

    def _send_presence(self, cid: int, tg_uid: str) -> None:
        """
        Paso 1: presencia — Vectrax se presenta con identidad,
        sin comandos, sin instrucciones. Solo presencia.
        """
        self._send(cid,
            "Vectrax activo.\n\n"
            "Arquitectura de inteligencia persistente.\n"
            "Memoria contextual, aprendizaje continuo "
            "y coherencia operacional como n\u00facleo.\n\n"
            "\u00bfCu\u00e1l es el desaf\u00edo que vamos a resolver hoy?"
        )
        try:
            from vectrax.onboarding import set_welcome_sent
            set_welcome_sent(tg_uid)
        except Exception as exc:
            logger.debug("onboarding set_welcome_sent failed: %s", exc)
        self._welcomed.add(tg_uid)
        logger.info("Presence sent | user=%s", tg_uid[:20])

    def _complete_onboarding(self, cid: int, tg_uid: str, name_raw: str) -> None:
        """
        Paso 2: guardar nombre. Respuesta m\u00ednima, sin lista de comandos.
        Los comandos se revelan por contexto, no por dump.
        """
        # Limpiar: title-case, m\u00e1ximo 60 chars
        name = name_raw.strip()
        # Si el usuario escribi\u00f3 varias palabras tipo "soy Mario", extraer nombre
        import re as _re
        _name_match = _re.search(
            r"(?:soy|me\s+llamo|my\s+name\s+is|i'?m|je\s+m'?appelle|ich\s+bin|sono)\s+([A-Za-z\u00c0-\u024f]{2,})",
            name, _re.I
        )
        if _name_match:
            name = _name_match.group(1)
        # Title-case y recortar
        name = name.title()[:60]

        try:
            from vectrax.onboarding import set_completed
            set_completed(tg_uid, name)
        except Exception as exc:
            logger.debug("onboarding set_completed failed: %s", exc)

        self._send(cid, f"Registrado, {name}. Hablemos.")
        logger.info("Onboarding completed | user=%s | name=%s", tg_uid[:20], name)

    def _send_welcome(self, cid: int, tg_uid: str) -> None:
        """Alias de compatibilidad — delega a _send_presence."""
        self._send_presence(cid, tg_uid)

    # == Idioma — detectar ambigüedad y resolver =================================

    def _check_language_ambiguity(self, cid: int, tg_uid: str, text: str) -> bool:
        """
        Gestiona la claridad del idioma en tres pasos:

        1. Si el idioma del mensaje es claro → continuar (return False).
        2. Si es ambiguo y no hay idioma guardado → preguntar (return True).
        3. Si estaba pendiente la respuesta → guardar idioma y continuar (return False).

        Returns:
            True  — se hizo la pregunta; no procesar el mensaje.
            False — continuar el flujo normal.
        """
        # === Paso 3: este mensaje ES la respuesta a la pregunta de idioma ===
        if tg_uid in self._lang_pending:
            self._lang_pending.discard(tg_uid)
            # Guardar idioma detectado del mensaje-respuesta.
            # apply_language_policy también se llamará más adelante en el pipeline;
            # esta llamada garantiza que quede guardado aunque el mensaje sea corto.
            try:
                from core.operator.conversational_policy import apply_language_policy
                apply_language_policy(tg_uid, text)
            except Exception as _exc:
                logger.debug("lang apply failed after pending: %s", _exc)
            logger.info("Language resolved | user=%s | answer=%r", tg_uid[:20], text[:40])
            return False  # continuar: el pipeline responderá en el idioma guardado

        # === Saltar para mensajes cortos o comandos ===
        # is_mixed_language requiere ≥5 palabras; mensajes cortos no son ambiguüables.
        if text.startswith("/") or len(text.split()) < 5:
            return False

        # === Saltar si ya hay idioma guardado ===
        try:
            from core.operator.conversational_policy import _get_user_language
            if _get_user_language(tg_uid):
                return False
        except Exception:
            pass

        # === Paso 2: detectar ambigüedad ===
        try:
            from core.language_gate import detect_language, is_mixed_language
            lang_detected = detect_language(text)
            mixed = is_mixed_language(text)

            if mixed or lang_detected == "unknown":
                self._lang_pending.add(tg_uid)
                self._send(cid, "\u00bfEn qu\u00e9 idioma quieres que te responda?")
                logger.info(
                    "Language ambiguous — asked user | user=%s | mixed=%s | detected=%s",
                    tg_uid[:20], mixed, lang_detected,
                )
                return True  # detener; esperar respuesta
        except Exception as _exc:
            logger.debug("language ambiguity check failed: %s", _exc)

        return False

    # == Team commands (público) =============================================

    def _handle_team(self, cid: int, tg_uid: str, text: str) -> None:
        """
        Comandos de equipo — accesibles a TODOS los usuarios.

        Comandos:
          /team new <nombre>   — crear equipo
          /team join <código>  — unirse a un equipo
          /team invite         — ver código de invitación
          /team note <texto>   — agregar nota compartida
          /team info           — ver info del equipo
          /team members        — ver miembros
          /team leave          — salir del equipo
          /team upgrade        — activar plan de equipo
        """
        # Normalizar: quitar prefijo /team o team
        clean = re.sub(r"^/?(?:vx\s+)?team\s*", "", text, flags=re.IGNORECASE).strip()
        parts = clean.split(None, 1)
        sub = parts[0].lower() if parts else "info"
        arg = parts[1].strip() if len(parts) > 1 else ""

        try:
            from vectrax.team_memory import (
                create_team, join_team, leave_team, add_team_note,
                get_team_info, list_members, get_user_team,
            )

            if sub in ("new", "crear", "create"):
                if not arg:
                    self._send(cid, "Uso: /team new <nombre del equipo>")
                    return
                result = create_team(tg_uid, arg)
                self._send(cid, (
                    f"🏗 Equipo creado: {result['name']}\n"
                    f"🔑 Código de invitación: {result['join_code']}\n"
                    f"\nComparte este código con tu equipo.\n"
                    f"Ellos deben escribir: /team join {result['join_code']}"
                ))

            elif sub in ("join", "unirse", "unirme"):
                if not arg:
                    self._send(cid, "Uso: /team join <código>")
                    return
                result = join_team(tg_uid, arg.upper())
                if result["ok"]:
                    if result.get("already_member"):
                        self._send(cid, f"✅ Ya eres miembro de {result['team_name']}.")
                    else:
                        self._send(cid, f"✅ Te uniste a {result['team_name']}.\nYa puedes ver y guardar conocimiento compartido.")
                else:
                    self._send(cid, f"❌ {result['error']}")

            elif sub in ("invite", "invitar", "codigo", "código"):
                team = get_user_team(tg_uid)
                if not team:
                    self._send(cid, "No perteneces a ningún equipo. Crea uno con /team new <nombre>.")
                    return
                self._send(cid, (
                    f"👥 Equipo: {team['name']}\n"
                    f"🔑 Código: {team['join_code']}\n"
                    f"\nPara unirse: /team join {team['join_code']}"
                ))

            elif sub in ("note", "nota", "guardar", "save"):
                if not arg:
                    self._send(cid, "Uso: /team note <texto a guardar para el equipo>")
                    return
                result = add_team_note(tg_uid, arg)
                if result["ok"]:
                    self._send(cid, f"✅ Guardado en {result['team_name']}.")
                else:
                    self._send(cid, f"❌ {result['error']}")

            elif sub in ("info", ""):
                info = get_team_info(tg_uid)
                if not info:
                    self._send(cid, (
                        "No perteneces a ningún equipo.\n\n"
                        "Comandos:\n"
                        "  /team new <nombre> — crear equipo\n"
                        "  /team join <código> — unirte a uno"
                    ))
                    return
                tier_label = "🟢 ACTIVO" if info["tier"] == "team" else "⚪ FREE"
                self._send(cid, (
                    f"🏗 {info['name']}\n"
                    f"👥 Miembros: {info['member_count']}\n"
                    f"📌 Notas: {info['note_count']}\n"
                    f"💳 Plan: {tier_label}\n"
                    f"🔑 Código: {info['join_code']}\n"
                    f"👤 Tu rol: {info['role']}"
                ))

            elif sub in ("members", "miembros"):
                team = get_user_team(tg_uid)
                if not team:
                    self._send(cid, "No perteneces a ningún equipo.")
                    return
                members = list_members(team["team_id"])
                lines = [f"👥 Miembros de {team['name']}:"]
                for m in members:
                    role_icon = "🔑" if m["role"] == "owner" else "👤"
                    lines.append(f"  {role_icon} {m['name']} ({m['role']})")
                self._send(cid, "\n".join(lines))

            elif sub in ("leave", "salir", "exit"):
                team = get_user_team(tg_uid)
                if not team:
                    self._send(cid, "No perteneces a ningún equipo.")
                    return
                leave_team(tg_uid)
                self._send(cid, f"✅ Saliste de {team['name']}.")

            elif sub in ("upgrade", "pro", "pagar"):
                team = get_user_team(tg_uid)
                if not team:
                    self._send(cid, "Primero crea o únete a un equipo.")
                    return
                if team["role"] != "owner":
                    self._send(cid, "Solo el dueño del equipo puede activar el plan.")
                    return
                try:
                    from services.billing.stripe_billing import create_team_checkout_session
                    url = create_team_checkout_session(tg_uid, team["team_id"])
                    if url:
                        self._send(cid, (
                            f"Plan de equipo para {team['name']}\n\n"
                            f"• Todos los miembros activos tienen acceso ilimitado\n"
                            f"• Memoria compartida entre el equipo\n"
                            f"• Internet, voz, mapas, mercado\n\n"
                            f"Activar aquí: {url}"
                        ))
                    else:
                        self._send(cid, "Sistema de pago en configuración. Contacta al administrador.")
                except Exception:
                    self._send(cid, "Sistema de pago en configuración. Contacta al administrador.")

            else:
                self._send(cid, (
                    "Comandos de equipo:\n"
                    "  /team new <nombre> — crear equipo\n"
                    "  /team join <código> — unirte a uno\n"
                    "  /team invite — ver código de invitación\n"
                    "  /team note <texto> — guardar conocimiento compartido\n"
                    "  /team info — ver estado del equipo\n"
                    "  /team members — ver miembros\n"
                    "  /team leave — salir del equipo\n"
                    "  /team upgrade — activar plan de equipo"
                ))

        except Exception as e:
            self._send(cid, f"Error: {e}")
            logger.error("team cmd error: %s", e)

    def _build_monitor_panel(self) -> str:
        """Construye el panel de monitoreo compacto con números."""
        import sqlite3
        import datetime

        _db = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "vault", "user_memory.db",
        )

        # --- Memoria de usuarios ---
        users = interactions = core_count = facts_count = 0
        try:
            conn = sqlite3.connect(_db, timeout=3)
            users = conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM profiles"
            ).fetchone()[0]
            interactions = conn.execute(
                "SELECT COUNT(*) FROM interactions"
            ).fetchone()[0]
        except Exception:
            pass
        try:
            core_count = conn.execute(
                "SELECT COUNT(*) FROM core_memory"
            ).fetchone()[0]
        except Exception:
            pass
        try:
            facts_count = conn.execute(
                "SELECT COUNT(*) FROM user_facts"
            ).fetchone()[0]
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

        # --- Métricas del sistema ---
        try:
            from core.operator.system_monitor import collect_metrics
            m = collect_metrics()
        except Exception:
            m = None

        # --- Construir panel ---
        now_utc = datetime.datetime.utcnow().strftime("%H:%M UTC")
        status_icon = "🟢"
        status_label = "HEALTHY"
        if m:
            if m.status == "critical":
                status_icon, status_label = "🔴", "CRITICAL"
            elif m.status == "degraded":
                status_icon, status_label = "🟡", "DEGRADED"

        lines = [
            f"📊 VECTRAX MONITOR — {now_utc}",
            "",
            f"👥 Usuarios:      {users}",
            f"💬 Interacciones: {interactions}",
            f"🧠 Core entries:  {core_count}",
            f"📋 Hechos:        {facts_count}",
            f"📨 Sesión:        {self._processed} msgs",
        ]

        if m:
            worker_status = (
                f"✅ vivo ({m.worker_heartbeat_age_s:.0f}s)"
                if m.worker_alive
                else "❌ MUERTO"
            )
            lines += [
                "",
                "🔄 Cola:",
                f"   ⏳ Pendientes:  {m.queue_pending}",
                f"   ⚙️  Procesando: {m.queue_processing}",
                f"   ✅ Completados: {m.queue_done}",
                f"   ❌ Errores:     {m.queue_error}",
                "",
                f"⚡ Latencia: avg {m.avg_latency_s}s | max {m.max_latency_s}s",
                f"💾 RAM:      {m.memory_mb} MB",
                f"👤 Activos:  {m.active_users} (últ. 5min)",
                f"🔧 Worker:   {worker_status}",
            ]

        lines += [
            "",
            f"{status_icon} Estado: {status_label}",
        ]

        return "\n".join(lines)

    def _vx_lang(self, cid: int, tg_uid: str, arg: str) -> None:
        """Forzar idioma: /vx lang es | /vx lang tg:123 fr"""
        parts = arg.split()
        if len(parts) == 1:
            # /vx lang es → cambiar MI idioma
            lang = parts[0].lower()
            target = tg_uid
        elif len(parts) == 2:
            # /vx lang tg:123 fr → cambiar idioma de otro usuario
            target = parts[0] if parts[0].startswith("tg:") else f"tg:{parts[0]}"
            lang = parts[1].lower()
        else:
            self._send(cid, "Uso: /vx lang <code> | /vx lang <uid> <code>")
            return

        valid = {"es", "en", "fr", "it", "de", "pt", "nl"}
        if lang not in valid:
            self._send(cid, f"Idioma inválido. Opciones: {', '.join(sorted(valid))}")
            return

        # Forzar en DB + sesión
        try:
            from core.operator.conversational_policy import _save_user_language
            _save_user_language(target, lang)
        except Exception:
            pass
        try:
            from vectrax.identity_anchor import _session
            _session.lock_language(target, lang)
        except Exception:
            pass
        # Actualizar perfil también
        import sqlite3
        db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vault", "user_memory.db")
        try:
            conn = sqlite3.connect(db)
            conn.execute("UPDATE profiles SET language = ? WHERE user_id = ?", (lang, target))
            conn.commit()
            conn.close()
        except Exception:
            pass
        self._send(cid, f"✅ {target} → {lang}")

    # == Scheduling: detect and handle scheduled tasks ====================

    _SCHEDULE_PATTERN = re.compile(
        r'(?:despi[eé]rtame|recu[eé]rdame|av[ií]same|rem[ií]ndme'
        r'|wake me|remind me|alert me|alarma'
        r'|todas? las ma[ñn]anas|every (?:morning|day|monday|tuesday|wednesday|thursday|friday)'
        r'|cada (?:ma[ñn]ana|d[ií]a|lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)'
        r'|dame (?:el|la|los|las) .+? (?:todas?|cada|every)'
        r'|en \d+\s*(?:minutos?|horas?|min|hr)\s+(?:av[ií]same|recu[eé]rdame|remind))',
        re.IGNORECASE,
    )

    def _detect_schedule(self, cid: int, tg_uid: str, text: str) -> bool:
        """
        Detect scheduling intent in natural language and create task.
        Returns True if handled (message was a scheduling instruction).
        """
        if not self._SCHEDULE_PATTERN.search(text):
            return False

        try:
            from core.scheduler import add_task, format_task_confirmation

            # Get user timezone (default Miami)
            tz = "America/New_York"
            try:
                from vectrax.user_memory import get_user_profile
                profile = get_user_profile(tg_uid)
                if profile.get("timezone"):
                    tz = profile["timezone"]
            except Exception:
                pass

            task = add_task(tg_uid, cid, text, tz=tz)
            if task:
                confirmation = format_task_confirmation(task, tz=tz)
                self._send(cid, confirmation)
                logger.info("SCHED %s | %s → task #%d", tg_uid[:15], text[:30], task["id"])
                return True
        except Exception as e:
            logger.error("_detect_schedule error: %s", e)

        return False

    def _handle_tasks(self, cid: int, tg_uid: str, text: str) -> None:
        """
        Handle /tasks, /remind, /schedule commands.

        /tasks              — list active tasks
        /tasks del <id>     — delete a task
        /remind <text>      — create a reminder
        """
        clean = re.sub(
            r'^/?(?:tasks|remind|schedule|recordar|alarma)\s*',
            '', text, flags=re.IGNORECASE,
        ).strip()

        try:
            from core.scheduler import (
                add_task, list_tasks, delete_task,
                format_task_confirmation, format_task_list,
            )

            # /tasks del <id>
            m_del = re.match(r'(?:del|delete|eliminar|borrar|cancelar)\s+(\d+)', clean, re.I)
            if m_del:
                task_id = int(m_del.group(1))
                ok = delete_task(tg_uid, task_id)
                if ok:
                    self._send(cid, f"✅ Tarea #{task_id} eliminada.")
                else:
                    self._send(cid, f"Tarea #{task_id} no encontrada.")
                return

            # /tasks (no args) — list
            if not clean:
                tasks = list_tasks(tg_uid)
                self._send(cid, format_task_list(tasks))
                return

            # /remind <instruction> — create task
            tz = "America/New_York"
            try:
                from vectrax.user_memory import get_user_profile
                profile = get_user_profile(tg_uid)
                if profile.get("timezone"):
                    tz = profile["timezone"]
            except Exception:
                pass

            task = add_task(tg_uid, cid, clean, tz=tz)
            if task:
                self._send(cid, format_task_confirmation(task, tz=tz))
            else:
                self._send(cid, "No pude interpretar la instrucción. Ejemplo:\n/remind mañana a las 7am llamar a Carlos")

        except Exception as e:
            self._send(cid, f"Error: {e}")
            logger.error("_handle_tasks error: %s", e)

    def _handle_photo(self, cid: int, tg_uid: str, photo_list: list, caption: str = "") -> None:
        """Handle photos: face registration, face recognition, or visual analysis."""
        try:
            from vectrax.integrations.vision import analyze_image, get_telegram_photo_url

            # Telegram sends multiple sizes; pick the largest
            best = max(photo_list, key=lambda p: p.get("file_size", 0))
            file_id = best.get("file_id", "")
            if not file_id:
                return

            # === FACE REGISTRATION: "Este soy yo" / "Esta es Ana" ===
            try:
                from vectrax.integrations.face_memory import (
                    detect_registration, register_face, download_photo,
                    recognize_faces,
                )
                reg = detect_registration(caption)
                if reg:
                    reg_type, reg_name = reg
                    # Get the person's name
                    if reg_type == "self":
                        try:
                            from vectrax.user_memory import get_user_profile
                            profile = get_user_profile(tg_uid)
                            reg_name = profile.get("name", "") or "Creador"
                        except Exception:
                            reg_name = "Creador"

                    # Download photo bytes
                    img_data = download_photo(self._token, file_id)
                    if img_data:
                        result = register_face(tg_uid, reg_name, img_data)
                        if result:
                            self._send(cid, (
                                f"📸 {reg_name} registrado en memoria visual.\n"
                                f"Vectrax no olvidará esa cara."
                            ))
                            logger.info("FACE REG %s | %s", tg_uid[:15], reg_name)
                        else:
                            self._send(cid, "No pude registrar la cara. Intenta con otra foto.")
                    else:
                        self._send(cid, "No pude descargar la imagen.")
                    return
            except Exception as e:
                logger.debug("Face registration check failed: %s", e)

            # Get direct URL for analysis
            url = get_telegram_photo_url(self._token, file_id)
            if not url:
                self._send(cid, "No pude acceder a la imagen.")
                return

            # === FACE RECOGNITION: check if known people appear ===
            recognized_names = []
            try:
                from vectrax.integrations.face_memory import recognize_faces
                recognized = recognize_faces(tg_uid, url)
                if recognized:
                    recognized_names = recognized
            except Exception:
                pass

            # Get user language
            lang = "es"
            try:
                from core.language_gate import get_user_language
                lang = get_user_language(tg_uid, caption or "")
            except Exception:
                pass

            # Get user context for richer analysis
            user_ctx = ""
            try:
                from vectrax.user_memory import get_user_profile
                profile = get_user_profile(tg_uid)
                name = profile.get("name", "")
                if name:
                    user_ctx = f"Usuario: {name}"
            except Exception:
                pass

            # Add recognized people to context + custom prompt
            vision_prompt = caption
            if recognized_names:
                names_str = ", ".join(recognized_names)
                user_ctx += (
                    f"\nIMPORTANTE: Ya identifiqué a estas personas en la foto: {names_str}. "
                    f"Reférete a ellas por nombre. NO digas que no puedes identificar personas."
                )
                if not caption:
                    vision_prompt = f"En esta foto aparece {names_str}. Analiza la imagen."

            # Analyze with vision (incluyendo nonce + recent-responses
            # como contexto anti-repetición para que el LLM evite
            # repetir estructuras ya usadas con este usuario)
            result = analyze_image(
                url,
                user_prompt=vision_prompt,
                lang=lang,
                user_context=user_ctx,
                user_id=tg_uid,
            )

            # Visual Humanizer: convertir descripción cruda en texto humano
            # (máximo 4 frases, sin "la imagen muestra", con continuidad si
            # hay rostros conocidos). Reemplaza el viejo header
            # "👤 Reconocido: ..." por mención natural integrada.
            if result:
                try:
                    from core.voice.visual_humanizer import humanize_visual
                    user_name_for_visual = ""
                    try:
                        from vectrax.user_memory import get_user_profile
                        user_name_for_visual = (
                            get_user_profile(tg_uid).get("name", "") or ""
                        )
                    except Exception:
                        pass
                    humanized = humanize_visual(
                        result,
                        faces=recognized_names,
                        user_name=user_name_for_visual,
                        lang=lang,
                    )
                    if humanized:
                        result = humanized
                except Exception as exc:
                    logger.debug("visual_humanizer skipped: %s", exc)

            # === Anti-Repetition Filter ====================================
            # Drop de clichés prohibidos (redes sociales) y rotación
            # estructural si la respuesta es muy parecida a las últimas
            # 10 enviadas a este user. Defensa en profundidad: aunque
            # el LLM ignore el system prompt, la salida no llega.
            if result:
                try:
                    from core.voice.anti_repetition import (
                        filter_response, record_response,
                    )
                    filtered = filter_response(result, tg_uid, lang=lang)
                    if filtered:
                        result = filtered
                    # else: dejamos el result original; el caller decide
                except Exception as exc:
                    logger.debug("anti_repetition skipped: %s", exc)

            if result:
                try:
                    from core.language_gate import enforce_language
                    result = enforce_language(result, lang, tg_uid)
                except Exception:
                    pass
                self._send(cid, result)
                # Registrar la salida final en el ring buffer del user
                # (después del send para no contaminar el filter del propio
                # request actual).
                try:
                    from core.voice.anti_repetition import record_response
                    record_response(tg_uid, result)
                except Exception:
                    pass
                logger.info("VISION %s | %d ch | faces=%s | caption=%s",
                            tg_uid[:15], len(result),
                            recognized_names or "none",
                            caption[:30] if caption else "(none)")
            else:
                self._send(cid, "No pude analizar la imagen.")

        except Exception as e:
            logger.error("_handle_photo error: %s", e)
            self._send(cid, "Error procesando la imagen.")

    def _places(self, cid: int, tg_uid: str, text: str) -> None:
        try:
            from vectrax.integrations.place_search import detect_place_intent
            if not detect_place_intent(text):
                return
            from vectrax.user_memory import get_user_location
            loc = get_user_location(tg_uid)
            if loc:
                from vectrax.integrations.place_search import search_places
                r = search_places(text, user_location=loc)
                if r.get("found") and r.get("results"):
                    for p in r["results"][:3]:
                        self._venue(cid, p)
            else:
                # Sin ubicación — pedir solo si el usuario quiere resultados cercanos
                from vectrax.integrations.place_search import _wants_nearby
                if _wants_nearby(text):
                    self._send(
                        cid, "Comparte tu ubicación para resultados cercanos:",
                        reply_markup={
                            "keyboard": [[{"text": "📍 Compartir ubicación", "request_location": True}]],
                            "resize_keyboard": True, "one_time_keyboard": True,
                        },
                    )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Voice transcription quality gate
# ---------------------------------------------------------------------------

def _is_quality_transcription(text: str) -> bool:
    """True si la transcripción parece tener contenido linguistic real.

    Filtros (todos deben pasar):
      1. Longitud mínima de letras: >= 4 letras alfabéticas (no
         puntuación).
      2. Densidad alfabética: >= 60% de caracteres son letras o
         espacios. Whisper sobre ruido devuelve mucha puntuación y
         caracteres raros.
      3. No es solo puntuación / dígitos / un solo carácter repetido.
      4. Tiene al menos 1 palabra de >= 2 letras.
      5. No mezcla scripts incompatibles (latin + arabic + cyrillic
         simultáneamente — indicador fuerte de fallo de whisper).

    Si pasa todos los filtros, se considera transcripción válida y
    el pipeline procede normalmente.
    """
    if not text or not text.strip():
        return False
    s = text.strip()

    # 1. Letras mínimas
    letters = sum(1 for c in s if c.isalpha())
    if letters < 4:
        return False

    # 2. Densidad alfabética + espacios
    alpha_or_space = sum(1 for c in s if c.isalpha() or c.isspace())
    if alpha_or_space / max(1, len(s)) < 0.60:
        return False

    # 4. Al menos una palabra >= 2 letras
    import re as _re
    words = _re.findall(r"[A-Za-z\u00C0-\u017F]{2,}", s)
    if not words:
        return False

    # 5. Mezcla de scripts incompatibles — si aparecen >= 2 de:
    #    latin / arabic / cyrillic / cjk en la MISMA transcripción,
    #    es casi siempre alucinación de whisper sobre ruido.
    has_latin = any(
        c.isalpha() and ord(c) < 0x0500 and (
            ord(c) < 0x0370 or 0x1E00 <= ord(c) <= 0x1EFF
        )
        for c in s
    )
    has_arabic = any(0x0600 <= ord(c) <= 0x06FF for c in s)
    has_cyrillic = any(0x0400 <= ord(c) <= 0x04FF for c in s)
    has_cjk = any(0x4E00 <= ord(c) <= 0x9FFF for c in s)
    scripts = sum([has_latin, has_arabic, has_cyrillic, has_cjk])
    if scripts >= 2:
        return False

    return True


def _load_token() -> str:
    env = _PROJECT_ROOT / ".env"
    if env.exists():
        load_dotenv(env)
    t = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not t:
        logger.error("TELEGRAM_BOT_TOKEN not found")
        sys.exit(1)
    return t


def main():
    logger.info("=== Vectrax Telegram Gateway (Scalable) ===")
    bot = TelegramGateway(_load_token())
    signal.signal(signal.SIGINT, lambda s, f: bot.stop())
    signal.signal(signal.SIGTERM, lambda s, f: bot.stop())
    bot.run()


if __name__ == "__main__":
    main()
