#!/usr/bin/env python3
"""
Vectrax Pipeline Worker — Fire & Deliver
============================================
Proceso independiente que:
  1. Lee mensajes de la cola SQLite
  2. Procesa con ExternalGateway
  3. Envía la respuesta DIRECTO a Telegram (sin pasar por gateway)
  4. Envía mapas de lugares si aplica

El gateway no espera. Este worker es el que entrega.

Creado: 2026-03-28
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import httpx

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
_env = _ROOT / ".env"
if _env.exists():
    load_dotenv(_env)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("vectrax.pipeline_worker")

# === RotatingFileHandler con rotación size-based ====================
# Antes: FileHandler crecía sin límite — worker.log llegaba a 100MB+ y
# operational_reflection escaneaba todo cada call (lentitud).
# Ahora: 10MB max + 5 backups = ~50MB total, autorrotación automática.
from logging.handlers import RotatingFileHandler
try:
    _WORKER_LOG_PATH = os.path.join(
        os.path.expanduser("~"), ".vectrax", "worker.log",
    )
    os.makedirs(os.path.dirname(_WORKER_LOG_PATH), exist_ok=True)
    _MAX_BYTES = int(os.environ.get(
        "VECTRAX_WORKER_LOG_MAX_BYTES", str(10 * 1024 * 1024),
    ))
    _BACKUP_COUNT = int(os.environ.get(
        "VECTRAX_WORKER_LOG_BACKUPS", "5",
    ))
    _file_handler = RotatingFileHandler(
        _WORKER_LOG_PATH,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    _file_handler.setLevel(logging.INFO)
    _file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    # Anadir al root para capturar tambien telegram_dispatch, voice, etc.
    logging.getLogger().addHandler(_file_handler)
except Exception as _e:
    print(f"[worker] could not attach FileHandler: {_e}", file=sys.stderr)

# Redacción de secretos en logs: httpx loguea la URL completa de cada request y
# las llamadas a Telegram llevan el bot token en la ruta. Esto lo enmascara en
# stdout y en worker.log (se instala tras adjuntar el FileHandler).
try:
    from core.log_redaction import install_redaction
    install_redaction()
except Exception:
    pass

POLL_INTERVAL = 0.3
CLEANUP_INTERVAL = 60
# Real-world worst case: vision+LLM+TTS takes 16-30s legitimately.
# 20s caused premature timeouts; 45s is safe with stuck recovery at 180s.
MSG_TIMEOUT = 45
HEARTBEAT_INTERVAL = 10
# Per-stage slow threshold (seconds) — logs STAGE_SLOW warning
STAGE_SLOW_THRESHOLD = 15.0
# Hard timeout per stage (seconds) — kills the stage if exceeded
CONVERGENCE_TIMEOUT = 10.0
GATEWAY_TIMEOUT = 30.0
# Memory watchdog interval (seconds)
MEMORY_WATCHDOG_INTERVAL = 30
# Max drain wait before hard exit (seconds)
MEMORY_DRAIN_TIMEOUT = 30
# Main loop watchdog: if main loop doesn't tick in this many seconds,
# force exit. Catches silent deadlocks (e.g. all threads stuck +
# pool.submit blocks waiting for a free slot).
MAIN_LOOP_WATCHDOG_TIMEOUT = 60
# Stuck processing recovery interval (seconds)
STUCK_RECOVERY_INTERVAL = 120

# Dynamic concurrency: scale with available RAM
try:
    from core.scalability_guard import get_optimal_concurrency
    CONCURRENT = get_optimal_concurrency()
except Exception:
    CONCURRENT = int(os.environ.get("VX_WORKER_CONCURRENT", "3"))

_HEARTBEAT_PATH = os.path.join(
    os.path.expanduser("~"), ".vectrax", "worker_heartbeat",
)


def _write_heartbeat() -> None:
    """Write current timestamp to heartbeat file."""
    try:
        os.makedirs(os.path.dirname(_HEARTBEAT_PATH), exist_ok=True)
        with open(_HEARTBEAT_PATH, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass

# Telegram API client for sending responses directly
_TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_TG_BASE = f"https://api.telegram.org/bot{_TG_TOKEN}" if _TG_TOKEN else ""
_TG_HTTP = httpx.Client(
    timeout=httpx.Timeout(15, connect=5),
    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
) if _TG_TOKEN else None


# ---------------------------------------------------------------------------
# Telegram send (worker sends directly, no gateway dependency)
# ---------------------------------------------------------------------------

def _tg_send(chat_id: int, text: str, **kwargs) -> bool:
    """Send message directly to Telegram, then dispatch voice.

    Pipeline-worker path. Symmetric with TelegramGateway._send: tras
    sendMessage exitoso, dispara TTS + sendAudio en background a través
    de core.voice.telegram_dispatch. Garantiza que respuestas QUEUED
    (la mayoría) también viajen con voz, no solo el fast-path.

    User Sovereignty Engine: cada envío pasa por require_authorization()
    declarando un SendReason. Default USER_REPLY. Callers de motores
    proactivos / schedulers pasan _sovereignty_reason= explícito.
    En modo observe (default) no se bloquea, sólo se loggea. En modo
    enforce se respetan las reglas de consent.
    """
    if not _TG_HTTP or not text:
        return False
    if len(text) > 4096:
        text = text[:4093] + "..."

    # === Telegram Guard — block telemetry/metrics from reaching chat ======
    _is_user_reply = kwargs.pop("_is_user_reply", False)
    _guard_reason = kwargs.pop("_guard_reason", "user_reply" if _is_user_reply else "")
    try:
        from core.telegram_guard import should_send_to_user
        if not should_send_to_user(text, is_user_reply=_is_user_reply, reason=_guard_reason):
            logger.info(
                "TG_GUARD suppressed chat=%s reason=%s | %s",
                chat_id, _guard_reason, text[:50].replace("\n", " "),
            )
            return True  # pretend sent — caller doesn't need to know
    except Exception:
        pass

    # === User Sovereignty Engine — gate de salida =========================
    try:
        from core.sovereignty import require_authorization, SendReason
        reason = kwargs.pop("_sovereignty_reason", SendReason.USER_REPLY)
        sov_user_id = kwargs.pop("_sovereignty_user_id", None)
        decision = require_authorization(
            chat_id=int(chat_id),
            reason=reason,
            user_id=sov_user_id,
            payload_size=len(text),
        )
        if not decision.is_allowed:
            logger.warning(
                "sovereignty: blocked send chat=%s reason=%s rule=%s",
                chat_id, reason.value, decision.rule,
            )
            return False
    except Exception as _se:
        # Sovereignty engine NUNCA debe romper un envío legítimo.
        # Cualquier fallo aquí cae a comportamiento legacy.
        logger.debug("sovereignty bypass on _tg_send: %s", _se)

    # === GUARDED TELEGRAM CALL =======================================
    # All Telegram sends go through ExternalCallGuard.
    # If Telegram is down/slow, the call times out cleanly (12s max)
    # and the circuit breaker prevents hammering a dead API.
    def _do_tg_send():
        r = _TG_HTTP.post(
            f"{_TG_BASE}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )
        r.raise_for_status()
        return r.json()

    sent_message_id = None
    try:
        from core.external_call_guard import guarded_call
        body = guarded_call("telegram", _do_tg_send, fallback=None)
    except Exception:
        body = None
    if body is None:
        return False
    ok = body.get("ok", False)
    if ok:
        sent_message_id = (body.get("result") or {}).get("message_id")

    # Class G runtime observation — register response hash for the detector
    if ok:
        try:
            from core.recovery.detectors.hardcoded_handler_runtime import (
                register_response,
            )
            register_response(user_id=str(chat_id), text=text, lang="")
        except Exception:
            pass

        # Voice dispatch — unified path con reply_to_message_id para
        # anclar el audio al texto correcto. Si el TTS llega fuera de
        # orden, Telegram lo muestra anclado al mensaje correcto.
        try:
            from core.voice.telegram_dispatch import dispatch_audio_async
            dispatch_audio_async(
                chat_id=chat_id,
                text=text,
                http_client=_TG_HTTP,
                reply_to_message_id=sent_message_id,
            )
        except Exception as exc:
            logger.debug("audio dispatch swallowed: %s", exc)

    return ok


def _tg_venue(chat_id: int, place: dict) -> bool:
    """Send venue (map pin) directly to Telegram."""
    if not _TG_HTTP:
        return False
    lat, lng = place.get("lat", 0), place.get("lng", 0)
    if not lat or not lng:
        return False
    title = place.get("nombre", "Lugar")
    extras = []
    if place.get("rating"):
        extras.append(f"{place['rating']}★")
    if place.get("distancia_label"):
        extras.append(place["distancia_label"])
    if extras:
        title = f"{title} — {' — '.join(extras)}"
    try:
        r = _TG_HTTP.post(
            f"{_TG_BASE}/sendVenue",
            json={
                "chat_id": chat_id, "latitude": lat, "longitude": lng,
                "title": title,
                "address": place.get("direccion", "") or "Sin dirección",
            },
        )
        return r.json().get("ok", False)
    except Exception:
        return False


def _tg_chat_action(chat_id: int, action: str = "typing") -> None:
    """Best-effort Telegram chat action (e.g. 'typing…').

    Pure UX signal (no message content) → reduces PERCEIVED latency while the
    cognitive pipeline runs, without touching the pipeline. Routed through
    ExternalCallGuard so a slow/down Telegram never adds latency to the reply
    path. Never raises. Disable with VX_TYPING_INDICATOR=0.
    """
    if not _TG_HTTP:
        return
    if os.environ.get("VX_TYPING_INDICATOR", "1").strip().lower() in ("0", "false", "off", "no"):
        return

    def _do():
        r = _TG_HTTP.post(
            f"{_TG_BASE}/sendChatAction",
            json={"chat_id": chat_id, "action": action},
        )
        r.raise_for_status()
        return True

    try:
        from core.external_call_guard import guarded_call
        guarded_call("telegram", _do, timeout=5, fallback=None)
    except Exception:
        pass


def _observe_runtime_error(exc: Exception, where: str = "pipeline_worker") -> None:
    """Record a real production runtime fault as a quality phenomenon.

    Makes worker failures/timeouts VISIBLE in the Universe (Pilar D) as failure
    stars, instead of the quality block staying empty in prod. Best-effort:
    never raises (wrapped), additive, and disable-able with VX_RUNTIME_OBSERVER=0.
    Honors the quality_observer privacy contract: only the exception CLASS name
    is stored (never the message/values).
    """
    if os.environ.get("VX_RUNTIME_OBSERVER", "1").strip().lower() in ("0", "false", "off", "no"):
        return
    try:
        from core.self_observation.quality_observer import record_runtime_error
        record_runtime_error(
            type(exc).__name__, where=where, module=where, severity="warning",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Process one message
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Scale Governor middleware helpers (opt-in via SCALE_GOVERNOR_ENABLED=1)
# ---------------------------------------------------------------------------

def _resolve_user_tier(user_id: str):
    """Best-effort tier resolution. Defaults to FREE.

    Lee `tier` o `plan` desde user_memory si existe. INTERNAL para el
    creador (Mario). Cualquier excepción cae a FREE.
    """
    from core.scale import Tier
    creator_uid = os.environ.get("VX_CREATOR_ID", "") or "2030762343"
    norm_uid = (user_id or "").replace("tg:", "")
    if norm_uid == creator_uid:
        return Tier.INTERNAL
    try:
        from vectrax.user_memory import get_user_profile
        prof = get_user_profile(user_id) or {}
        raw = (prof.get("tier") or prof.get("plan") or "").upper().strip()
        if raw in ("PRO", "BUSINESS", "INTERNAL", "FREE"):
            return Tier(raw)
    except Exception:
        pass
    return Tier.FREE


def _quick_intent(content: str) -> str:
    """Reusa el classifier del VoiceEngine. Defensive."""
    try:
        from core.voice.intent import classify_intent
        return classify_intent(content or "")
    except Exception:
        return "casual"


def _scale_governor_preflight(msg):
    """Si SCALE_GOVERNOR_ENABLED=1, corre la decisión del governor antes
    del pipeline. Devuelve (decision, sg) o (None, None) si está off o
    falló.

    El caller usa decision.cache_hit / decision.rate_limited para corto
    circuitar el pipeline.
    """
    if os.environ.get("SCALE_GOVERNOR_ENABLED") != "1":
        return None, None
    try:
        from core.scale import ScaleGovernor
        if not hasattr(_scale_governor_preflight, "_sg"):
            _scale_governor_preflight._sg = ScaleGovernor()
        sg = _scale_governor_preflight._sg
        tier = _resolve_user_tier(msg.user_id)
        intent = _quick_intent(msg.content)
        decision = sg.select_minimal_pipeline(
            user_id=msg.user_id,
            tier=tier,
            intent=intent,
            message=msg.content,
        )
        logger.info(
            "SG | user=%s tier=%s intent=%s profile=%s model=%s cache=%s rl=%s",
            msg.user_id, tier.value, intent,
            decision.profile.value, decision.chosen_model,
            decision.cache_hit, decision.rate_limited,
        )
        return decision, sg
    except Exception as exc:
        logger.debug("scale_governor preflight skipped: %s", exc)
        return None, None


def _get_rss_mb() -> float:
    """Get current process RSS in MB. Cross-platform (Linux + macOS)."""
    # Linux: /proc/self/status
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except Exception:
        pass
    # macOS: resource.getrusage
    try:
        import resource
        import platform
        ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if platform.system() == "Darwin":
            return ru / (1024 * 1024)  # bytes → MB
        return ru / 1024  # kB → MB
    except Exception:
        pass
    return 0.0


def _stage_timer(name: str, msg_id: str):
    """Context manager that times a pipeline stage and logs it."""
    import contextlib

    @contextlib.contextmanager
    def _timer():
        t = time.time()
        yield
        elapsed = time.time() - t
        if elapsed > STAGE_SLOW_THRESHOLD:
            logger.warning(
                "STAGE_SLOW %s | %s | %.1fs (>%.0fs)",
                msg_id, name, elapsed, STAGE_SLOW_THRESHOLD,
            )
        else:
            logger.info("STAGE %s | %s | %.2fs", msg_id, name, elapsed)
    return _timer()


def _process_one(msg):
    """Process a single queued message: pipeline → send to Telegram."""
    from core.transport.message_queue import mark_done, mark_error
    from core.operator.external_gateway import ExternalGateway

    if not hasattr(_process_one, "_gw"):
        _process_one._gw = ExternalGateway()
    gw = _process_one._gw

    t0 = time.time()
    ram_before = _get_rss_mb()
    try:
        # === SCALE GOVERNOR PREFLIGHT (opt-in) ===
        with _stage_timer("scale_governor", msg.id):
            decision, sg = _scale_governor_preflight(msg)

        # Cache hit → responde y termina sin tocar el pipeline
        if decision is not None and decision.cache_hit and decision.cached_response:
            _tg_send(msg.chat_id, decision.cached_response, _is_user_reply=True)
            mark_done(msg.id, decision.cached_response)
            elapsed = time.time() - t0
            logger.info(
                "SG_CACHE_HIT %s | %.3fs | %d ch | %s",
                msg.id, elapsed, len(decision.cached_response),
                msg.content[:30],
            )
            return True

        # Rate limited → responde con mensaje de límite y termina
        if decision is not None and decision.rate_limited:
            _tg_send(msg.chat_id, decision.rate_limit_message, _is_user_reply=True)
            mark_done(msg.id, decision.rate_limit_message)
            elapsed = time.time() - t0
            logger.info(
                "SG_RATE_LIMITED %s | %.3fs | %s",
                msg.id, elapsed, msg.content[:30],
            )
            return True

        # Diagnostic: log el input que llega al pipeline cognitivo
        logger.info(
            "PROC_IN msg_id=%s user=%s p=%d content=%r",
            msg.id, msg.user_id, getattr(msg, 'priority', 1),
            (msg.content or "")[:120],
        )

        # Acuse inmediato "escribiendo…": baja la latencia PERCIBIDA mientras
        # corre el pipeline, sin tocarlo. Acotado por el guard; desactivable
        # con VX_TYPING_INDICATOR=0.
        _tg_chat_action(msg.chat_id, "typing")

        # ── CICLO DE CONVERGENCIA TOTAL (hard timeout) ────────────────────
        # Non-fatal: if convergence hangs or times out, pipeline continues.
        _conv_record = None
        with _stage_timer("convergence_cycle", msg.id):
            try:
                from concurrent.futures import ThreadPoolExecutor as _TPE, TimeoutError as _TE
                from core.convergence_hook import run_convergence_cycle, should_block
                with _TPE(max_workers=1) as _conv_pool:
                    _conv_future = _conv_pool.submit(
                        run_convergence_cycle,
                        msg.content,
                        source="telegram",
                        channel=msg.channel or "telegram",
                        owner=msg.user_id,
                        metadata={"msg_id": msg.id},
                    )
                    _conv_record = _conv_future.result(timeout=CONVERGENCE_TIMEOUT)
            except _TE:
                logger.warning(
                    "CONV_TIMEOUT %s | convergence_cycle exceeded %.0fs",
                    msg.id, CONVERGENCE_TIMEOUT,
                )
            except Exception as _ce:
                logger.debug("convergence_cycle error (skipped): %s", _ce)
        if _conv_record is not None:
            try:
                from core.convergence_hook import should_block
                if should_block(_conv_record):
                    logger.warning(
                        "CONV_BLOCK msg_id=%s user=%s",
                        msg.id, msg.user_id,
                    )
                    mark_done(msg.id, "blocked_by_convergence")
                    return True
            except Exception:
                pass
        # ───────────────────────────────────────────────────────────────────────

        # ── EXTERNAL GATEWAY (subprocess isolation) ───────────────────
        # Uses multiprocessing.Process so the gateway can be KILLED if it
        # hangs. ThreadPoolExecutor.result(timeout) raises TimeoutError but
        # the thread keeps running — Python can't kill threads.
        # Process.kill() actually terminates the stuck code.
        result = None
        with _stage_timer("external_gateway", msg.id):
            try:
                import multiprocessing as _mp
                _result_q = _mp.Queue(maxsize=1)

                def _gw_worker(_q, _uid, _content, _channel):
                    """Run gateway in isolated process."""
                    try:
                        # Re-init gateway in this process (fresh state)
                        from core.operator.external_gateway import ExternalGateway
                        _gw = ExternalGateway()
                        _r = _gw.receive_message(
                            user_id=_uid,
                            content=_content,
                            channel=_channel,
                        )
                        _q.put({
                            "response": _r.response,
                            "source": _r.source,
                            "processed": _r.processed,
                            "event_id": _r.event_id,
                        })
                    except Exception as _e:
                        _q.put({"response": "", "source": "error", "error": str(_e)})

                _proc = _mp.Process(
                    target=_gw_worker,
                    args=(_result_q, msg.user_id, msg.content, msg.channel),
                    daemon=True,
                )
                _proc.start()
                _proc.join(timeout=GATEWAY_TIMEOUT)

                if _proc.is_alive():
                    # Process hung — kill it for real
                    logger.warning(
                        "GW_TIMEOUT %s | external_gateway exceeded %.0fs — killing subprocess PID %d",
                        msg.id, GATEWAY_TIMEOUT, _proc.pid,
                    )
                    _proc.kill()
                    _proc.join(timeout=5)
                elif not _result_q.empty():
                    _raw = _result_q.get_nowait()
                    from core.operator.external_gateway import GatewayResult
                    result = GatewayResult(
                        response=_raw.get("response", ""),
                        source=_raw.get("source", ""),
                        processed=_raw.get("processed", False),
                        event_id=_raw.get("event_id", ""),
                        user_id=msg.user_id,
                        channel=msg.channel,
                    )
                # Clean up
                try:
                    _result_q.close()
                except Exception:
                    pass
            except Exception as _ge:
                logger.warning("external_gateway subprocess error: %s", _ge)
        elapsed = time.time() - t0

        response = ""
        if result is not None:
            if result.source == "memory":
                response = result.response
            else:
                response = (result.response or "").strip()
        # Respuesta vacía = silencio. No mandar ruido al usuario.

        # Diagnostic: log la respuesta cruda + source
        logger.info(
            "PROC_OUT msg_id=%s source=%s len=%d response=%r",
            msg.id, result.source if result else "timeout",
            len(response), response[:200],
        )

        # === LANGUAGE ENFORCEMENT (prevent language leaks) ===
        if response:
            with _stage_timer("language_enforce", msg.id):
                try:
                    from core.language_gate import enforce_language, get_user_language
                    user_lang = get_user_language(msg.user_id, msg.content)
                    response = enforce_language(response, user_lang, msg.user_id)
                except Exception as _le:
                    logger.debug("Language enforce skipped: %s", _le)

        # === ANTI-REPETITION FILTER ===================================
        # Strip de clichés prohibidos (redes sociales / community manager)
        # y rotación estructural si la respuesta es muy similar a las
        # últimas 10 enviadas al user. Si el filter devuelve None la
        # respuesta era 100% cliché — silencio antes que ruido.
        if response:
            with _stage_timer("anti_repetition", msg.id):
                try:
                    from core.voice.anti_repetition import filter_response
                    filtered = filter_response(response, msg.user_id)
                    if filtered:
                        response = filtered
                    else:
                        logger.info(
                            "anti_repetition: blocked all-cliche response for %s",
                            msg.user_id,
                        )
                        response = ""
                except Exception as _ae:
                    logger.debug("anti_repetition skipped: %s", _ae)

        # === SCALE GOVERNOR: cache la respuesta para futuras idénticas ===
        if (
            decision is not None
            and sg is not None
            and response
            and not decision.cache_hit
            and not decision.rate_limited
            and len(response) < 1000
        ):
            try:
                sg.semantic_cache_set(decision.cache_key, response, ttl=300)
            except Exception:
                pass

        # === GRACEFUL DEGRADATION: never leave user with silence ===
        if not response:
            try:
                from core.language_gate import get_user_language
                _dl = get_user_language(msg.user_id, msg.content)
            except Exception:
                _dl = "es"

            # Detect greeting — don't show "capacity limited" for "Hola"
            import re as _gd_re
            _is_greeting = bool(_gd_re.match(
                r"^\s*(?:hola|hey|hi|hello|buenos?\s+d[ií]as?|buenas?"
                r"|buen d[ií]a|good\s+(?:morning|afternoon|evening)"
                r"|que\s+tal|qu[eé]\s+tal|saludos|yo|ey)\s*[.!?]*\s*$",
                msg.content.strip(), _gd_re.IGNORECASE,
            ))
            if _is_greeting:
                try:
                    from vectrax.identity_anchor import get_anchored_identity
                    _anch = get_anchored_identity(msg.user_id)
                    _gn = _anch.name.split()[0] if _anch and _anch.has_name else ""
                except Exception:
                    _gn = ""
                if _dl == "en":
                    response = f"Hey{f' {_gn}' if _gn else ''}. What do you need?"
                else:
                    response = f"Hola{f' {_gn}' if _gn else ''}. ¿En qué trabajamos?"
            else:
                if _dl == "en":
                    response = (
                        "Processing capacity is limited right now. "
                        "Your message has been recorded. Try again shortly."
                    )
                else:
                    response = (
                        "Capacidad de procesamiento limitada en este momento. "
                        "Tu mensaje quedó registrado. Intenta de nuevo en unos minutos."
                    )
            logger.info(
                "GRACEFUL_DEGRADATION %s | greeting=%s | user=%s",
                msg.id, _is_greeting, msg.user_id,
            )

        # === ENVIAR DIRECTO A TELEGRAM ===
        sent = _tg_send(msg.chat_id, response, _is_user_reply=True)

        # === REGISTRAR EN ANTI-REPETITION RING BUFFER =================
        # Tras un envío exitoso, grabamos la salida en el ring buffer
        # del user. Solo grabamos cuando se envió (sent=True) y la
        # respuesta no es vacía — evita contaminar el buffer con
        # respuestas que el user nunca vio.
        if sent and response:
            try:
                from core.voice.anti_repetition import record_response
                record_response(msg.user_id, response)
            except Exception as _re:
                logger.debug("record_response skipped: %s", _re)

        # === GRAVITY KERNEL — SHADOW MODE (opt-in, read-only, no-fatal) ===
        # Observa la interacción ya resuelta y registra la evaluación del
        # kernel (Ritmo + Causa/Efecto) comparada con la acción real. NO altera
        # la respuesta. Desactivado por defecto: VX_GRAVITY_KERNEL_SHADOW=1.
        try:
            from core import gravity_kernel
            if gravity_kernel.is_enabled():
                gravity_kernel.observe(
                    content=msg.content or "",
                    user_id=str(msg.user_id),
                    result_source=(result.source if result else None),
                    response_sent=bool(sent),
                    response_len=len(response or ""),
                    source="telegram_worker",
                )
        except Exception as _gke:
            logger.debug("gravity_kernel shadow skipped: %s", _gke)

        # === MAPAS DE LUGARES ===
        try:
            from vectrax.integrations.place_search import detect_place_intent
            if detect_place_intent(msg.content):
                from vectrax.user_memory import get_user_location
                loc = get_user_location(msg.user_id)
                if loc:
                    from vectrax.integrations.place_search import search_places
                    pr = search_places(msg.content, user_location=loc)
                    if pr.get("found") and pr.get("results"):
                        for p in pr["results"][:3]:
                            _tg_venue(msg.chat_id, p)
        except Exception:
            pass

        mark_done(msg.id, response)

        ram_after = _get_rss_mb()
        ram_delta = ram_after - ram_before
        logger.info(
            "DONE %s | %.1fs | %d ch | sent=%s | RAM %.0f→%.0fMB (%+.0f) | %s",
            msg.id, elapsed, len(response), sent,
            ram_before, ram_after, ram_delta,
            msg.content[:30],
        )
        if ram_delta > 50:
            logger.warning(
                "MEM_LEAK %s | +%.0fMB in single message | %s",
                msg.id, ram_delta, msg.content[:60],
            )

        # === EXPLICIT MESSAGE LIFECYCLE END ============================
        # Force garbage collection to free embeddings, LLM buffers,
        # convergence objects. Without this, Python accumulates ~100MB
        # per message batch (circular refs from sentence_transformers,
        # httpx responses, torch tensors).
        msg_id_for_log = msg.id
        try:
            del msg
            del response
            del result
        except Exception:
            pass
        try:
            import gc
            gc.collect()
        except Exception:
            pass
        return True

    except Exception as exc:
        elapsed = time.time() - t0
        logger.error("ERROR %s | %.1fs | %s", msg.id, elapsed, exc)

        # Universe visibility (Pilar D): este fallo real se vuelve una estrella
        # de fallo. Aditivo, defensivo, flag VX_RUNTIME_OBSERVER.
        _observe_runtime_error(exc, where="pipeline_worker")

        # Intentar notificar al usuario del error
        _tg_send(msg.chat_id, "Error procesando tu mensaje. Intenta de nuevo.")

        mark_error(msg.id, str(exc)[:300])
        return False


# ---------------------------------------------------------------------------
# Main worker loop
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Main loop watchdog — detects silent deadlocks
# ---------------------------------------------------------------------------

import threading as _wd_threading

_watchdog_last_tick = 0.0
_watchdog_lock = _wd_threading.Lock()


def _watchdog_tick() -> None:
    """Called by the main loop every iteration to signal liveness."""
    global _watchdog_last_tick
    with _watchdog_lock:
        _watchdog_last_tick = time.time()


def _watchdog_thread(timeout: float) -> None:
    """Background thread that force-exits if main loop is stuck.

    Root cause: when all ThreadPoolExecutor threads are stuck on LLM
    calls, pool.submit() blocks waiting for a free slot. This blocks
    the main loop, which stops the heartbeat. The supervisor can't
    detect the hang because the heartbeat file stops updating silently.

    This watchdog detects that condition and force-exits, allowing
    the supervisor to restart the worker cleanly.
    """
    while True:
        time.sleep(timeout / 2)
        with _watchdog_lock:
            last = _watchdog_last_tick
        if last == 0.0:
            continue  # not started yet
        age = time.time() - last
        if age > timeout:
            logger.critical(
                "MAIN_LOOP_WATCHDOG | main loop stuck for %.0fs > %.0fs — force exit",
                age, timeout,
            )
            # Force exit — supervisor will restart us
            os._exit(1)


# ---------------------------------------------------------------------------
# Stuck processing recovery
# ---------------------------------------------------------------------------

def _recover_stuck_processing(max_age_s: int = 90) -> int:
    """Reset messages stuck in 'processing' status back to 'error'.

    When the worker crashes or a thread hangs, messages can stay in
    'processing' forever. This function recovers them.

    Args:
        max_age_s: Messages in 'processing' longer than this are stuck.

    Returns:
        Number of messages recovered.
    """
    try:
        import sqlite3
        _qdb = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            '..', 'vault', 'message_queue.db',
        )
        _qdb = os.path.normpath(_qdb)
        conn = sqlite3.connect(_qdb, timeout=5)
        count = conn.execute(
            "UPDATE queue SET status='error', error='stuck_processing_recovered' "
            "WHERE status='processing' AND started_at > 0 AND started_at < ?",
            (time.time() - max_age_s,),
        ).rowcount
        conn.commit()
        conn.close()
        if count:
            logger.warning("STUCK_RECOVERY | reset %d stuck processing messages", count)
        return count
    except Exception as exc:
        logger.debug("Stuck recovery failed: %s", exc)
        return 0


def run_worker() -> None:
    from core.transport.message_queue import dequeue, mark_error, cleanup

    running = True
    processed = 0
    last_cleanup = time.time()
    pool = ThreadPoolExecutor(max_workers=CONCURRENT, thread_name_prefix="pw")
    active = {}  # msg_id → (future, start_time)
    # Semaphore to prevent pool.submit() from blocking the main loop.
    # When all CONCURRENT threads are busy, acquire() returns False
    # instead of blocking — main loop continues writing heartbeat.
    _pool_semaphore = _wd_threading.Semaphore(CONCURRENT)

    def _stop(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    last_heartbeat = 0.0
    # FIX (cuelgue crónico del worker): los timers periódicos se inicializan en
    # el momento de ARRANQUE, no en 0.0. Con 0.0, en la PRIMERA iteración del
    # main loop disparaban TODOS a la vez (market_learn + freight_learn +
    # gravity_sync + trading_conv + reentry + digest + scheduler) de forma
    # síncrona, bloqueando el main loop ~39s — más que WORKER_HEARTBEAT_MAX_AGE
    # (30s) — así que el supervisor mataba al worker antes de estabilizarlo y el
    # ciclo se repetía cada ~53s indefinidamente (martilleando además eToro en
    # cada reinicio). Inicializando en _startup, cada motor espera su intervalo
    # completo y el worker alcanza estado estable de inmediato.
    _startup = time.time()
    last_proactive = _startup  # última ejecución del motor proactivo
    last_scheduler = _startup  # última ejecución del scheduler
    last_reentry = _startup    # última ejecución del reentry check
    last_digest = _startup     # última ejecución del router digest
    last_market_learn = _startup  # ciclo de aprendizaje de mercado (cada 30 min)
    last_freight_learn = _startup  # freight learning cycle (every 6h)
    last_gravity_sync  = _startup  # gravity index → vectrax.db sync (every 6h)
    last_trading_conv  = _startup  # trading convergence learner (every 24h)
    last_mem_check = _startup  # memory watchdog
    last_stuck_recovery = _startup  # stuck processing recovery

    # Start main loop watchdog thread
    _wd = _wd_threading.Thread(
        target=_watchdog_thread,
        args=(MAIN_LOOP_WATCHDOG_TIMEOUT,),
        daemon=True,
        name="main_loop_watchdog",
    )
    _wd.start()
    logger.info("Main loop watchdog started (timeout=%ds)", MAIN_LOOP_WATCHDOG_TIMEOUT)

    # Discard stale messages from previous sessions (>90s old)
    _STALE_AGE = 90  # was 300s — lowered to catch crash-orphaned messages faster
    try:
        import sqlite3
        _qdb = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            '..', 'vault', 'message_queue.db')
        _qdb = os.path.normpath(_qdb)
        _conn = sqlite3.connect(_qdb, timeout=5)
        _stale_count = _conn.execute(
            "UPDATE queue SET status='error', error='stale_on_startup' "
            "WHERE status IN ('pending','processing') AND created_at < ?",
            (time.time() - _STALE_AGE,)
        ).rowcount
        _conn.commit()
        _conn.close()
        if _stale_count:
            logger.info("Discarded %d stale messages on startup", _stale_count)
    except Exception as _se:
        logger.debug("Stale cleanup failed: %s", _se)

    logger.info("Worker started (PID %d, %d concurrent, fire-and-deliver)", os.getpid(), CONCURRENT)

    # --- Scheduler DB init: ensure table exists before first tick ---
    try:
        from core.scheduler import _conn as _sched_conn
        _sc = _sched_conn()
        _sc.close()
        logger.info("Scheduler DB initialized")
    except Exception as _si:
        logger.debug("Scheduler DB init skipped: %s", _si)

    # --- LLM warm-up: pre-initialize ExternalGateway to avoid cold-start ---
    try:
        from core.operator.external_gateway import ExternalGateway
        t_warm = time.time()
        _process_one._gw = ExternalGateway()
        logger.info("LLM warm-up: ExternalGateway initialized in %.1fs", time.time() - t_warm)
    except Exception as _we:
        logger.warning("LLM warm-up failed (will init on first message): %s", _we)

    while running:
        try:
            # Signal watchdog: main loop is alive
            _watchdog_tick()

            # Check completed/timed-out
            done_ids = []
            for mid, (fut, t0) in list(active.items()):
                if fut.done():
                    _pool_semaphore.release()
                    try:
                        if fut.result():
                            processed += 1
                    except Exception:
                        pass
                    done_ids.append(mid)
                elif time.time() - t0 > MSG_TIMEOUT:
                    logger.warning("TIMEOUT %s (>%ds)", mid, MSG_TIMEOUT)
                    mark_error(mid, f"Timeout after {MSG_TIMEOUT}s")
                    _observe_runtime_error(
                        TimeoutError(f"message exceeded {MSG_TIMEOUT}s"),
                        where="pipeline_worker.timeout",
                    )
                    # fut.cancel() doesn't kill threads — but we still
                    # release the semaphore so the main loop can continue.
                    # The orphaned thread will eventually finish or be
                    # killed when the worker restarts.
                    _pool_semaphore.release()
                    fut.cancel()
                    done_ids.append(mid)
            for mid in done_ids:
                active.pop(mid, None)

            # Dequeue if capacity — use semaphore instead of len(active)
            # to prevent pool.submit() from blocking the main loop when
            # all threads are stuck.
            if _pool_semaphore.acquire(blocking=False):
                msg = dequeue()
                if msg:
                    # Skip messages older than 2 minutes (stale from crash/restart)
                    msg_age = time.time() - msg.created_at
                    if msg_age > 120:
                        logger.warning("SKIP stale %s | %.0fs old | %s", msg.id, msg_age, msg.content[:30])
                        mark_error(msg.id, f"stale_{msg_age:.0f}s")
                        _pool_semaphore.release()
                    else:
                        logger.info("DEQUEUE %s | %s | chat=%d", msg.id, msg.content[:30], msg.chat_id)
                        future = pool.submit(_process_one, msg)
                        active[msg.id] = (future, time.time())
                else:
                    _pool_semaphore.release()
                    time.sleep(POLL_INTERVAL)
            else:
                # All threads busy — don't block, just wait
                time.sleep(0.1)

            # Heartbeat
            if time.time() - last_heartbeat > HEARTBEAT_INTERVAL:
                _write_heartbeat()
                last_heartbeat = time.time()

            # Cleanup
            if time.time() - last_cleanup > CLEANUP_INTERVAL:
                cleanup()
                last_cleanup = time.time()

            # Stuck processing recovery (every 120s)
            if time.time() - last_stuck_recovery > STUCK_RECOVERY_INTERVAL:
                _recover_stuck_processing(max_age_s=90)
                last_stuck_recovery = time.time()

            # Motor proactivo — anticipa y avisa (cada 10 minutos)
            try:
                from core.proactive_engine import run_proactive_scan, CHECK_INTERVAL
                from core.sovereignty import SendReason as _SR
                # OPT-IN: el motor proactivo enviaba follow-ups automaticos
                # al user (cada 10 min, max 1 por usuario cada 6h). Mario
                # los percibia como 'el bot habla del anterior despues del
                # ultimo'. Default OFF; activar con PROACTIVE_ENGINE_ENABLED=1.
                # Además ahora cada envío declara reason=PROACTIVE_INSIGHT,
                # así que en modo enforce el Sovereignty Engine bloquea
                # automaticamente a los users sin consent activo.
                if (
                    os.environ.get("PROACTIVE_ENGINE_ENABLED") == "1"
                    and time.time() - last_proactive > CHECK_INTERVAL
                ):
                    def _proactive_send(cid, text):
                        return _tg_send(
                            cid, text,
                            _sovereignty_reason=_SR.PROACTIVE_INSIGHT,
                            _sovereignty_user_id=f"tg:{cid}",
                            _guard_reason="proactive",
                        )
                    n = run_proactive_scan(_proactive_send)
                    if n:
                        logger.info("Proactive: %d messages sent", n)
                    last_proactive = time.time()
            except Exception as _pe:
                logger.debug("Proactive engine error (passthrough): %s", _pe)
                last_proactive = time.time()  # evitar loop de errores

            # Market learning cycle — observe, record, learn patterns (every 30 min)
            # CRITICAL: runs in a thread with timeout. Before this fix,
            # run_learning_cycle() ran directly in the main loop and blocked
            # the heartbeat for up to 540s (36 HTTP calls × 15s timeout).
            try:
                _MARKET_LEARN_INTERVAL = 1800  # 30 min
                _MARKET_LEARN_TIMEOUT = 60     # max 60s for the whole cycle
                if time.time() - last_market_learn > _MARKET_LEARN_INTERVAL:
                    from concurrent.futures import ThreadPoolExecutor as _MLP, TimeoutError as _MLT
                    from connectors.etoro.learning_engine import run_learning_cycle
                    watchlist_env = os.environ.get("ETORO_WATCHLIST", "")
                    symbols = watchlist_env.split(",") if watchlist_env else None
                    with _MLP(max_workers=1) as _ml_pool:
                        _ml_future = _ml_pool.submit(run_learning_cycle, symbols)
                        try:
                            summary = _ml_future.result(timeout=_MARKET_LEARN_TIMEOUT)
                            if summary.get("signals_recorded", 0) > 0:
                                logger.info(
                                    "Market learn: %d signals, %d patterns",
                                    summary["signals_recorded"],
                                    summary.get("patterns_total", 0),
                                )
                        except _MLT:
                            logger.warning(
                                "MARKET_LEARN_TIMEOUT | learning cycle exceeded %ds",
                                _MARKET_LEARN_TIMEOUT,
                            )
                    last_market_learn = time.time()
            except Exception as _ml:
                logger.debug("Market learn error (passthrough): %s", _ml)
                last_market_learn = time.time()

            # Freight learning cycle — pattern accumulation + elevation (every 6h)
            # Runs in an isolated ThreadPool with 120s timeout.
            # Provider selected by FREIGHT_FEED_PROVIDER (default: simulator).
            # Fault-isolated: never crashes the main loop.
            try:
                _FREIGHT_LEARN_INTERVAL = 21600  # 6h
                _FREIGHT_LEARN_TIMEOUT  = 120    # max 2min for the whole cycle
                if time.time() - last_freight_learn > _FREIGHT_LEARN_INTERVAL:
                    from concurrent.futures import ThreadPoolExecutor as _FLP, TimeoutError as _FLT
                    from connectors.freight.learning_cycle import run_learning_cycle as _freight_cycle
                    with _FLP(max_workers=1) as _fl_pool:
                        _fl_fut = _fl_pool.submit(_freight_cycle)
                        try:
                            _fl_sum = _fl_fut.result(timeout=_FREIGHT_LEARN_TIMEOUT)
                            logger.info(
                                "Freight learn: provider=%s ingested=%d stars=%d→%d "
                                "mature=%d elevated=%d %.1fs",
                                _fl_sum.get("provider"),
                                _fl_sum.get("events_ingested", 0),
                                _fl_sum.get("stars_before", 0),
                                _fl_sum.get("stars_after", 0),
                                _fl_sum.get("mature_stars", 0),
                                _fl_sum.get("patterns_elevated", 0),
                                _fl_sum.get("elapsed_s", 0),
                            )
                        except _FLT:
                            logger.warning(
                                "FREIGHT_LEARN_TIMEOUT | cycle exceeded %ds",
                                _FREIGHT_LEARN_TIMEOUT,
                            )
                    last_freight_learn = time.time()
            except Exception as _fl:
                logger.debug("Freight learn error (passthrough): %s", _fl)
                last_freight_learn = time.time()

            # Gravity sync — mature domain patterns → vectrax.db stars + mass recompute (every 6h)
            # Bridges the two star systems: gravity_index.json → visual universe.
            # Also recomputes stale masses (stuck at MIN_MASS=0.01).
            try:
                _GRAVITY_SYNC_INTERVAL = 21600  # 6h
                if time.time() - last_gravity_sync > _GRAVITY_SYNC_INTERVAL:
                    from core.gravity_sync import run_sync as _gravity_sync
                    _gs = _gravity_sync()
                    if _gs.get("patterns_promoted", 0) > 0 or _gs.get("stars_mass_updated", 0) > 0:
                        logger.info(
                            "Gravity sync: promoted=%d mass_updated=%d %.1fs",
                            _gs.get("patterns_promoted", 0),
                            _gs.get("stars_mass_updated", 0),
                            _gs.get("elapsed_s", 0),
                        )
                    last_gravity_sync = time.time()
            except Exception as _gs_exc:
                logger.debug("Gravity sync error (passthrough): %s", _gs_exc)
                last_gravity_sync = time.time()

            # Trading convergence learner — observe drift, generate proposals (every 24h)
            # Never mutates auto_executor config. Proposals stored in
            # ~/.vectrax/etoro_learner_proposals.jsonl for creator review.
            try:
                _TRADING_CONV_INTERVAL = 86400  # 24h
                if time.time() - last_trading_conv > _TRADING_CONV_INTERVAL:
                    from connectors.etoro.convergence_learner import run_observation_cycle as _conv_cycle
                    _conv_sum = _conv_cycle()
                    if _conv_sum.get("proposals_generated", 0) > 0:
                        logger.info(
                            "Trading convergence learner: %d proposals | drifts=%s | wr=%.1f%%",
                            _conv_sum["proposals_generated"],
                            _conv_sum.get("drift_kinds", []),
                            _conv_sum.get("observed_wr_pct", 0),
                        )
                    last_trading_conv = time.time()
            except Exception as _tc:
                logger.debug("Trading convergence learner error (passthrough): %s", _tc)
                last_trading_conv = time.time()

            # Presence nudges — 3-nudge state machine per user (every 10 min)
            # nudge #1: 12-20h silence | nudge #2: 3-5d | nudge #3: 21-30d → DORMANT
            try:
                from core.continuity_reentry import check_reentry
                from core.sovereignty import SendReason as _SR3
                if time.time() - last_reentry > 600:  # every 10 min
                    def _reentry_send(cid, text):
                        return _tg_send(
                            cid, text,
                            _sovereignty_reason=_SR3.PROACTIVE_INSIGHT,
                            _sovereignty_user_id=f"tg:{cid}",
                            _guard_reason="reentry",
                        )
                    n = check_reentry(_reentry_send)
                    if n:
                        logger.info("Reentry: %d messages sent", n)
                    last_reentry = time.time()
            except Exception as _re:
                logger.debug("Reentry check error (passthrough): %s", _re)
                last_reentry = time.time()

            # Router telemetry digest — stored in logs/dashboard only (silent mode)
            try:
                from core.observability.router_telemetry import run_digest, _DIGEST_INTERVAL
                if time.time() - last_digest > _DIGEST_INTERVAL:
                    def _digest_send(cid, text):
                        return _tg_send(cid, text, _guard_reason="digest")
                    run_digest(_digest_send)
                    last_digest = time.time()
            except Exception as _de:
                logger.debug("Router digest error (passthrough): %s", _de)
                last_digest = time.time()

            # Scheduler — tareas programadas (cada 60s)
            try:
                from core.scheduler import run_scheduler_tick, TICK_INTERVAL
                from core.sovereignty import SendReason as _SR2
                if time.time() - last_scheduler > TICK_INTERVAL:
                    def _scheduler_send(cid, text):
                        return _tg_send(
                            cid, text,
                            _sovereignty_reason=_SR2.SCHEDULED_REMINDER,
                            _sovereignty_user_id=f"tg:{cid}",
                            _guard_reason="scheduled",
                        )
                    n = run_scheduler_tick(_scheduler_send)
                    if n:
                        logger.info("Scheduler: %d tasks executed", n)
                    last_scheduler = time.time()
            except Exception as _se:
                logger.debug("Scheduler error (passthrough): %s", _se)
                last_scheduler = time.time()

            # Heartbeat refresh tras la batería de motores periódicos: si un
            # ciclo periódico tardó varios segundos, evita que el supervisor lo
            # confunda con un cuelgue (el main loop sí avanzó esta iteración).
            if time.time() - last_heartbeat > HEARTBEAT_INTERVAL:
                _write_heartbeat()
                last_heartbeat = time.time()

            # === MEMORY WATCHDOG (every 30s) ==============================
            # check_worker_memory() existed in scalability_guard but was
            # never called. Now we call it periodically and self-restart
            # if RAM exceeds the threshold (default 1200MB).
            if time.time() - last_mem_check > MEMORY_WATCHDOG_INTERVAL:
                last_mem_check = time.time()
                try:
                    from core.scalability_guard import check_worker_memory
                    ram_mb, should_restart = check_worker_memory(os.getpid())
                    if should_restart:
                        logger.warning(
                            "MEMORY_WATCHDOG: %.0fMB — initiating graceful restart",
                            ram_mb,
                        )
                        # 1. Stop accepting new messages
                        running = False
                        # 2. Drain active futures (max MEMORY_DRAIN_TIMEOUT)
                        drain_start = time.time()
                        while active and (time.time() - drain_start) < MEMORY_DRAIN_TIMEOUT:
                            done_drain = []
                            for mid, (fut, _t0) in list(active.items()):
                                if fut.done():
                                    done_drain.append(mid)
                            for mid in done_drain:
                                active.pop(mid, None)
                            if active:
                                time.sleep(0.5)
                        if active:
                            logger.warning(
                                "MEMORY_WATCHDOG: %d messages still active after drain — hard exit",
                                len(active),
                            )
                        pool.shutdown(wait=False)
                        logger.info(
                            "MEMORY_WATCHDOG: restarting process (PID %d, RAM %.0fMB)",
                            os.getpid(), ram_mb,
                        )
                        # 3. Re-exec the process — Docker/supervisor will catch exit
                        try:
                            os.execv(sys.executable, [sys.executable] + sys.argv)
                        except Exception:
                            os._exit(1)
                except Exception as _mw:
                    logger.debug("Memory watchdog check failed: %s", _mw)

        except Exception as exc:
            logger.error("Worker loop: %s", exc)
            time.sleep(1)

    pool.shutdown(wait=False)
    logger.info("Worker stopped | processed=%d", processed)


if __name__ == "__main__":
    run_worker()
