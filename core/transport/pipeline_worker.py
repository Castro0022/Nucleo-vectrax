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

POLL_INTERVAL = 0.3
CLEANUP_INTERVAL = 60
MSG_TIMEOUT = 20
CONCURRENT = 3
HEARTBEAT_INTERVAL = 10

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

def _tg_send(chat_id: int, text: str) -> bool:
    """Send message directly to Telegram."""
    if not _TG_HTTP or not text:
        return False
    if len(text) > 4096:
        text = text[:4093] + "..."
    try:
        r = _TG_HTTP.post(
            f"{_TG_BASE}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )
        r.raise_for_status()
        return r.json().get("ok", False)
    except Exception as exc:
        logger.warning("TG send failed: %s", exc)
        return False


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


# ---------------------------------------------------------------------------
# Process one message
# ---------------------------------------------------------------------------

def _process_one(msg):
    """Process a single queued message: pipeline → send to Telegram."""
    from core.transport.message_queue import mark_done, mark_error
    from core.operator.external_gateway import ExternalGateway

    if not hasattr(_process_one, "_gw"):
        _process_one._gw = ExternalGateway()
    gw = _process_one._gw

    t0 = time.time()
    try:
        result = gw.receive_message(
            user_id=msg.user_id,
            content=msg.content,
            channel=msg.channel,
        )
        elapsed = time.time() - t0

        response = ""
        if result.source == "memory":
            response = result.response
        else:
            response = (result.response or "").strip()
        # Respuesta vacía = silencio. No mandar ruido al usuario.

        # === LANGUAGE ENFORCEMENT (prevent language leaks) ===
        if response:
            try:
                from core.language_gate import enforce_language, get_user_language
                user_lang = get_user_language(msg.user_id, msg.content)
                response = enforce_language(response, user_lang, msg.user_id)
            except Exception as _le:
                logger.debug("Language enforce skipped: %s", _le)

        # === ENVIAR DIRECTO A TELEGRAM ===
        sent = _tg_send(msg.chat_id, response)

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

        logger.info(
            "DONE %s | %.1fs | %d ch | sent=%s | %s",
            msg.id, elapsed, len(response), sent, msg.content[:30],
        )
        return True

    except Exception as exc:
        elapsed = time.time() - t0
        logger.error("ERROR %s | %.1fs | %s", msg.id, elapsed, exc)

        # Intentar notificar al usuario del error
        _tg_send(msg.chat_id, "Error procesando tu mensaje. Intenta de nuevo.")

        mark_error(msg.id, str(exc)[:300])
        return False


# ---------------------------------------------------------------------------
# Main worker loop
# ---------------------------------------------------------------------------

def run_worker() -> None:
    from core.transport.message_queue import dequeue, mark_error, cleanup

    running = True
    processed = 0
    last_cleanup = time.time()
    pool = ThreadPoolExecutor(max_workers=CONCURRENT, thread_name_prefix="pw")
    active = {}  # msg_id → (future, start_time)

    def _stop(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    last_heartbeat = 0.0
    last_proactive = 0.0  # última ejecución del motor proactivo
    last_scheduler = 0.0  # última ejecución del scheduler

    # Discard stale messages from previous sessions (>5 min old)
    _STALE_AGE = 300  # 5 minutes
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
            # Check completed/timed-out
            done_ids = []
            for mid, (fut, t0) in list(active.items()):
                if fut.done():
                    try:
                        if fut.result():
                            processed += 1
                    except Exception:
                        pass
                    done_ids.append(mid)
                elif time.time() - t0 > MSG_TIMEOUT:
                    logger.warning("TIMEOUT %s (>%ds)", mid, MSG_TIMEOUT)
                    mark_error(mid, f"Timeout after {MSG_TIMEOUT}s")
                    fut.cancel()
                    done_ids.append(mid)
            for mid in done_ids:
                active.pop(mid, None)

            # Dequeue if capacity
            if len(active) < CONCURRENT:
                msg = dequeue()
                if msg:
                    # Skip messages older than 2 minutes (stale from crash/restart)
                    msg_age = time.time() - msg.created_at
                    if msg_age > 120:
                        logger.warning("SKIP stale %s | %.0fs old | %s", msg.id, msg_age, msg.content[:30])
                        mark_error(msg.id, f"stale_{msg_age:.0f}s")
                    else:
                        logger.info("DEQUEUE %s | %s | chat=%d", msg.id, msg.content[:30], msg.chat_id)
                        future = pool.submit(_process_one, msg)
                        active[msg.id] = (future, time.time())
                else:
                    time.sleep(POLL_INTERVAL)
            else:
                time.sleep(0.1)

            # Heartbeat
            if time.time() - last_heartbeat > HEARTBEAT_INTERVAL:
                _write_heartbeat()
                last_heartbeat = time.time()

            # Cleanup
            if time.time() - last_cleanup > CLEANUP_INTERVAL:
                cleanup()
                last_cleanup = time.time()

            # Motor proactivo — anticipa y avisa (cada 10 minutos)
            try:
                from core.proactive_engine import run_proactive_scan, CHECK_INTERVAL
                if time.time() - last_proactive > CHECK_INTERVAL:
                    n = run_proactive_scan(_tg_send)
                    if n:
                        logger.info("Proactive: %d messages sent", n)
                    last_proactive = time.time()
            except Exception as _pe:
                logger.debug("Proactive engine error (passthrough): %s", _pe)
                last_proactive = time.time()  # evitar loop de errores

            # Scheduler — tareas programadas (cada 60s)
            try:
                from core.scheduler import run_scheduler_tick, TICK_INTERVAL
                if time.time() - last_scheduler > TICK_INTERVAL:
                    n = run_scheduler_tick(_tg_send)
                    if n:
                        logger.info("Scheduler: %d tasks executed", n)
                    last_scheduler = time.time()
            except Exception as _se:
                logger.debug("Scheduler error (passthrough): %s", _se)
                last_scheduler = time.time()

        except Exception as exc:
            logger.error("Worker loop: %s", exc)
            time.sleep(1)

    pool.shutdown(wait=False)
    logger.info("Worker stopped | processed=%d", processed)


if __name__ == "__main__":
    run_worker()
