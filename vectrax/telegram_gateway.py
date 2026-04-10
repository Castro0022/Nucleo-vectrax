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
RETRY_DELAY = 15          # espera 15s entre reintentos (antes 5s)
MAX_CONSECUTIVE_ERRORS = 10
CONFLICT_RETRY_DELAY = 60  # 409 Conflict: espera 60s antes de reintentar
RESPONSE_WAIT_TIMEOUT = 25.0
WORKERS = 6
HEARTBEAT_INTERVAL = 10  # seconds between heartbeat writes


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
        self._start_time: float = time.time()
        self._poll_http = httpx.Client(
            timeout=httpx.Timeout(POLL_TIMEOUT + 10, connect=10),
        )
        self._send_http = httpx.Client(
            timeout=httpx.Timeout(15, connect=5),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        self._dl_http = httpx.Client(timeout=30)
        self._pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="tg")
        self._heartbeat_thread: threading.Thread | None = None
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
        if re.search(
            r"(?:c[oó]mo te llamas|cu[áa]l es tu nombre|who are you"
            r"|what(?:'?s| is) your name|qui[eé]n eres"
            r"|qu[eé] (?:eres|es vectrax|hace|ofrece|puedes hacer|puedo hacer contigo)"
            r"|para qu[eé] sirves|c[oó]mo funciona[s]?"
            r"|what is vectrax|what are you|what can you do"
            r"|qu[eé] hace[s]?|cu[aá]les son tus capacidades"
            r"|chi sei|cosa sei|was bist du|qui es[- ]tu|wat ben je)", t,
        ):
            _lang = "es"
            try:
                from vectrax.resolver import _detect_lang
                _lang = _detect_lang(text)
            except Exception:
                pass
            try:
                from vectrax.core_identity import get_product_identity
                return get_product_identity(_lang)
            except Exception:
                return "Vectrax es tu memoria inteligente. Recuerda todo lo que le dices y te ayuda a decidir mejor con el tiempo."

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

    # == Heartbeat (background thread) =======================================

    def _heartbeat_loop(self) -> None:
        """Background daemon thread: writes heartbeat every HEARTBEAT_INTERVAL seconds.

        Decoupled from the polling cycle so the heartbeat stays fresh even when
        getUpdates blocks for 30-40s.  Only stops when self._running becomes False.
        """
        while self._running:
            self._write_heartbeat()
            # Sleep in small increments so we notice _running=False quickly
            for _ in range(HEARTBEAT_INTERVAL * 2):
                if not self._running:
                    break
                time.sleep(0.5)

    # == Polling loop (NEVER blocks) =======================================

    def run(self) -> None:
        self._running = True

        # Start dedicated heartbeat thread (daemon — dies with main process)
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, name="gw-heartbeat", daemon=True,
        )
        self._heartbeat_thread.start()

        logger.info("Bot started — queue-based polling (heartbeat thread active)")
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
                err_str = str(e)
                # 409 Conflict = otra instancia corriendo — esperar más y resetear
                if "409" in err_str or "Conflict" in err_str:
                    logger.warning(
                        "409 Conflict detectado — otra instancia activa. "
                        "Esperando %ds antes de reintentar...", CONFLICT_RETRY_DELAY,
                    )
                    time.sleep(CONFLICT_RETRY_DELAY)
                    self._errors = 0   # resetear: el conflicto se resolverá solo
                    continue
                self._errors += 1
                logger.error("Poll (%d/%d): %s", self._errors, MAX_CONSECUTIVE_ERRORS, e)
                if self._errors >= MAX_CONSECUTIVE_ERRORS:
                    self._running = False
                    break
                time.sleep(RETRY_DELAY)
        logger.info("Bot stopped | processed=%d", self._processed)

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

            # === BIENVENIDA — primer mensaje de usuario nuevo ===
            _t = text.strip()
            if self._is_new_user(tg_uid):
                self._send_welcome(cid, tg_uid)
                # Continuar procesando el mensaje normalmente

            # === /start — siempre muestra bienvenida ===
            if _t.lower() in ("/start", "start"):
                self._send_welcome(cid, tg_uid)
                return

            # === LEAD COMMANDS: /lead — accesibles a todos los usuarios ===
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

            # === TIER CHECK: verificar acceso del usuario ===
            try:
                from core.operator.user_tiers import check_access, can_use_feature
                access = check_access(tg_uid)
                if not access.allowed:
                    self._send(cid, access.reason)
                    return
            except Exception:
                pass

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
                    "/vx sql <query> — consulta SQL directa (solo lectura)"
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

    # == Bienvenida — primer mensaje ==========================================

    _welcomed: set = set()  # cache en sesión (se resetea al reiniciar)

    def _is_new_user(self, tg_uid: str) -> bool:
        """
        True si es la primera vez que este usuario escribe.
        Verifica contra SQLite para sobrevivir reinicios.
        """
        if tg_uid in self._welcomed:
            return False
        try:
            import sqlite3 as _sq
            _db = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "vault", "user_memory.db",
            )
            conn = _sq.connect(_db, timeout=2)
            count = conn.execute(
                "SELECT COUNT(*) FROM interactions WHERE user_id=?", (tg_uid,)
            ).fetchone()[0]
            conn.close()
            if count == 0:
                return True
        except Exception:
            pass
        self._welcomed.add(tg_uid)
        return False

    def _send_welcome(self, cid: int, tg_uid: str) -> None:
        """Envía el mensaje de bienvenida y registra al usuario."""
        # Detectar idioma del sistema o usar español por defecto
        try:
            from core.language_gate import get_user_language
            lang = get_user_language(tg_uid, "")
        except Exception:
            lang = "es"

        messages = {
            "es": "Hola. Soy Vectrax.\nGuarda un seguimiento con:\n/lead add nombre\n\n👉 /help para ver todos los comandos",
            "en": "Hey. I'm Vectrax.\nSave a follow-up with:\n/lead add name\n\n👉 /help to see all commands",
            "fr": "Salut. Je suis Vectrax.\nEnregistre un suivi avec:\n/lead add prénom",
            "it": "Ciao. Sono Vectrax.\nSalva un follow-up con:\n/lead add nome",
            "de": "Hallo. Ich bin Vectrax.\nSpeichere einen Kontakt mit:\n/lead add Name",
            "pt": "Olá. Sou Vectrax.\nGuarda um seguimento com:\n/lead add nome",
        }
        msg = messages.get(lang, messages["es"])
        self._send(cid, msg)
        self._welcomed.add(tg_uid)
        logger.info("Welcome sent | user=%s | lang=%s", tg_uid[:20], lang)

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
