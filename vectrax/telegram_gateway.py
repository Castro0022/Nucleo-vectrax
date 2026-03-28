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
import os
import re
import signal
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("vectrax.telegram_gateway")

TELEGRAM_API = "https://api.telegram.org/bot{token}"
POLL_TIMEOUT = 30
RETRY_DELAY = 5
MAX_CONSECUTIVE_ERRORS = 10
RESPONSE_WAIT_TIMEOUT = 25.0
WORKERS = 6


class TelegramGateway:
    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN vacío")
        self._token = token
        self._base = TELEGRAM_API.format(token=token)
        self._offset: int = 0
        self._running: bool = False
        self._processed: int = 0
        self._errors: int = 0
        self._poll_http = httpx.Client(
            timeout=httpx.Timeout(POLL_TIMEOUT + 10, connect=10),
        )
        self._send_http = httpx.Client(
            timeout=httpx.Timeout(15, connect=5),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        self._dl_http = httpx.Client(timeout=30)
        self._pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="tg")
        logger.info("Gateway ready (%d workers, queue-based)", WORKERS)

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

    def _send(self, cid: int, text: str, **extra) -> bool:
        if not text:
            return False
        if len(text) > 4096:
            text = text[:4093] + "..."
        return self._tg("sendMessage", chat_id=cid, text=text, **extra) is not None

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
            "es": "¿Qué necesitas?",
            "en": "What do you need?",
            "fr": "De quoi as-tu besoin?",
            "it": "Di cosa hai bisogno?",
            "de": "Was brauchst du?",
            "pt": "Do que você precisa?",
            "nl": "Wat heb je nodig?",
        }
        lang = _GREETINGS.get(t)
        if lang:
            greeting = {"es": "Hola", "en": "Hello", "fr": "Bonjour",
                        "it": "Ciao", "de": "Hallo", "pt": "Olá", "nl": "Hoi"}
            g = greeting.get(lang, "Hola")
            q = _GREETING_REPLIES.get(lang, "¿Qué necesitas?")
            return f"{g} {name}. {q}" if name else f"{g}. {q}"

        # Agradecimientos (multilingüe)
        _THANKS = {
            "gracias": "De nada", "muchas gracias": "De nada", "mil gracias": "De nada",
            "thanks": "You're welcome", "thank you": "You're welcome",
            "merci": "De rien", "merci beaucoup": "De rien",
            "grazie": "Prego", "grazie mille": "Prego",
            "danke": "Bitte", "danke schön": "Bitte", "danke schon": "Bitte",
            "obrigado": "De nada", "obrigada": "De nada",
            "dank je": "Graag gedaan", "bedankt": "Graag gedaan",
        }
        reply = _THANKS.get(t)
        if reply:
            return f"{reply}, {name}." if name else f"{reply}."

        # Despedidas (multilingüe)
        _BYES = {
            "chao": "Hasta luego", "adiós": "Hasta luego", "adios": "Hasta luego",
            "hasta luego": "Hasta luego", "nos vemos": "Nos vemos", "chau": "Chau",
            "bye": "Goodbye", "goodbye": "Goodbye", "see you": "See you",
            "au revoir": "Au revoir", "salut": "fr_bye", "a bientôt": "A bientôt",
            "arrivederci": "Arrivederci", "ciao ciao": "Ciao",
            "tschüss": "Tschüss", "tschuss": "Tschüss", "auf wiedersehen": "Auf Wiedersehen",
            "tchau": "Tchau", "até logo": "Até logo",
            "doei": "Doei", "tot ziens": "Tot ziens",
        }
        bye = _BYES.get(t)
        if bye and bye != "fr_bye":
            return f"{bye}, {name}." if name else f"{bye}."

        # Confirmaciones (multilingüe)
        if re.match(
            r"^(?:ok|okay|s[ií]|listo|entendido|perfecto|vale|claro"
            r"|yes|sure|got it|oui|d'accord|va bene|ja|genau|sim|certo)$", t,
        ):
            return "Entendido."

        # Identidad de Vectrax (multilingüe)
        if re.search(
            r"(?:c[oó]mo te llamas|cu[aá]l es tu nombre|who are you"
            r"|what(?:'?s| is) your name|qui[eé]n eres|tu nombre"
            r"|tienes nombre|dime tu nombre|como te digo"
            r"|comment (?:tu )?t'appelles|quel est ton nom|qui es[- ]tu"
            r"|come ti chiami|qual [eè] il tuo nome|chi sei"
            r"|wie hei[sß]t du|wer bist du|wie ist dein name"
            r"|como (?:voc[eê]|te) (?:se )?chama|qual [eé] (?:o )?(?:seu|teu) nome"
            r"|hoe heet je|wie ben je)", t,
        ):
            return "Soy Vectrax Core. Mi creador es Mario Bravo Castro."

        if re.search(
            r"(?:qu[eé] eres|what are you|qu'est[- ]ce que tu es"
            r"|cosa sei|was bist du|o que voc[eê] [eé]|wat ben je)", t,
        ):
            return (
                "Soy Vectrax, un sistema cognitivo autónomo con memoria gravitacional. "
                "Mi creador es Mario Bravo Castro."
            )

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

    # == Polling loop (NEVER blocks) =======================================

    def run(self) -> None:
        self._running = True
        logger.info("Bot started — queue-based polling")
        while self._running:
            try:
                r = self._poll_http.post(
                    f"{self._base}/getUpdates",
                    json={"offset": self._offset, "timeout": POLL_TIMEOUT,
                          "allowed_updates": ["message"]},
                )
                r.raise_for_status()
                d = r.json()
                updates = d.get("result", []) if d.get("ok") else []
                self._errors = 0
                for u in updates:
                    uid = u.get("update_id", 0)
                    self._offset = uid + 1
                    self._pool.submit(self._handle, u)
            except Exception as e:
                self._errors += 1
                logger.error("Poll (%d/%d): %s", self._errors, MAX_CONSECUTIVE_ERRORS, e)
                if self._errors >= MAX_CONSECUTIVE_ERRORS:
                    self._running = False
                    break
                time.sleep(RETRY_DELAY)
        logger.info("Bot stopped | processed=%d", self._processed)

    def stop(self):
        self._running = False

    # == Message handler (worker thread) ===================================

    def _handle(self, update: Dict) -> None:
        try:
            msg = update.get("message")
            if not msg:
                return
            cid = msg.get("chat", {}).get("id")
            if not cid:
                return
            uid = str(msg.get("from", {}).get("id", "unknown"))
            tg_uid = f"tg:{uid}"
            text = msg.get("text", "")

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

            if not text:
                return

            # Rule approval
            m = re.match(r"^(?:aprobar|approve)\s+(RULE-\w+)", text.strip(), re.I)
            if m:
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

            # === FAST-PATH: respuesta instantánea ===
            fast = self._fast(text, tg_uid)
            if fast:
                self._send(cid, fast)
                self._processed += 1
                # Places map pins (non-blocking, best-effort)
                self._places(cid, tg_uid, text)
                logger.info("FAST %s | %s → %d ch", uid, text[:30], len(fast))
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
            logger.error("Handler: %s", e)
            try:
                cid = update.get("message", {}).get("chat", {}).get("id")
                if cid:
                    self._send(cid, "Error interno. Intenta de nuevo.")
            except Exception:
                pass

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
            if text:
                return text
            self._send(cid, "No pude transcribir el audio.")
            return ""
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
                self._send(
                    cid, "Para darte resultados cercanos, comparte tu ubicación:",
                    reply_markup={
                        "keyboard": [[{"text": "📍 Compartir ubicación", "request_location": True}]],
                        "resize_keyboard": True, "one_time_keyboard": True,
                    },
                )
        except Exception:
            pass


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
