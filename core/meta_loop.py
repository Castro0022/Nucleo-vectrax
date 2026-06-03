"""
Vectrax Meta-Loop
=================
Post-action reflection hook. Runs after each daemon cycle to
evaluate system behavior and record observations into cognitive state.

Reflection layers:
  1. Activity  — was something ingested this cycle?
  2. Health    — any errors accumulated?
  3. Rhythm    — cycle cadence and uptime awareness.
  4. Ideas     — auto-refresh IdeaStore every 15 min + creator alerts.
  5. Observation — autonomous universe observation, persisted to ledger.

Privacidad:
  - No almacena contenido de mensajes ni datos personales.
  - Las alertas solo incluyen metadata abstracta de ideas (ID, título, score).
"""

import logging
import os
import time
from datetime import datetime
from typing import Optional

from core import state_manager

logger = logging.getLogger("vectrax.meta_loop")

RUNTIME_DIR = os.path.expanduser("~/.vectrax")
LOG_FILE = os.path.join(RUNTIME_DIR, "vectrax.log")

# IdeaStore refresh cadence
_IDEA_REFRESH_INTERVAL = 900   # 15 minutes
_MAX_ALERTS_PER_CYCLE  = 3     # máx. alertas por refresh

# Módulo-level state
_last_idea_refresh: float = 0.0
_alerted_idea_ids: set = set()  # evitar reenviar la misma idea


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _compute_uptime(boot_time_str):
    """Return human-readable uptime from boot_time ISO string."""
    if not boot_time_str:
        return "unknown"
    try:
        boot = datetime.fromisoformat(boot_time_str)
        delta = datetime.now() - boot
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes = remainder // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except (ValueError, TypeError):
        return "unknown"


# ---------------------------------------------------------------------------
# Telegram helper (fire-and-forget, no gateway dependency)
# ---------------------------------------------------------------------------

def _send_telegram(chat_id: str, text: str) -> bool:
    """
    Envía un mensaje Telegram directamente al creador via Bot API.
    Usa TELEGRAM_BOT_TOKEN del entorno. Retorna True si tuvo éxito.
    Solo se usa para alertas proactivas de meta_loop.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token or not chat_id:
        logger.debug("_send_telegram: sin token o chat_id, skip.")
        return False
    try:
        import httpx
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = httpx.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as exc:
        logger.debug("_send_telegram failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# IdeaStore refresh + alertas proactivas
# ---------------------------------------------------------------------------

def _run_idea_refresh() -> int:
    """
    Refresca el IdeaStore desde todas las fuentes y envía alertas al creador
    para ideas nuevas de prioridad HIGH o CRITICAL.
    Retorna número de alertas enviadas.
    """
    global _last_idea_refresh, _alerted_idea_ids
    try:
        from core.idea_store import get_idea_store, IdeaPriority, IdeaStatus

        store = get_idea_store()
        added = store.refresh()
        new_total = sum(added.values())

        if new_total:
            logger.info(
                "meta_loop: IdeaStore refreshed — +%d ideas (%s)",
                new_total, added,
            )

        # Identificar ideas HIGH/CRITICAL pendientes no alertadas
        creator_chat_id = os.getenv("TELEGRAM_CREATOR_CHAT_ID", "")
        if not creator_chat_id:
            return 0

        urgent = [
            idea for idea in store.pending()
            if idea.priority in (IdeaPriority.CRITICAL, IdeaPriority.HIGH)
            and idea.idea_id not in _alerted_idea_ids
        ]

        # Ordenar por priority_score desc, limitar a _MAX_ALERTS_PER_CYCLE
        urgent.sort(key=lambda i: -i.priority_score)
        to_alert = urgent[:_MAX_ALERTS_PER_CYCLE]

        alerts_sent = 0
        for idea in to_alert:
            priority_icons = {"critical": "🔴", "high": "🟠"}
            icon = priority_icons.get(idea.priority.value, "🟠")
            msg = (
                f"{icon} <b>Nueva idea {idea.priority.value.upper()}</b>\n"
                f"<code>{idea.idea_id}</code>\n"
                f"{idea.title}\n"
                f"Componente: {idea.affected_component}\n"
                f"Score: {idea.priority_score:.2f}\n\n"
                f"Para aprobar: <code>aprobar {idea.idea_id}</code>"
            )
            if _send_telegram(creator_chat_id, msg):
                _alerted_idea_ids.add(idea.idea_id)
                alerts_sent += 1
                logger.info(
                    "meta_loop: alerta enviada al creador — %s [%s]",
                    idea.idea_id, idea.priority.value,
                )

        return alerts_sent

    except Exception as exc:
        logger.debug("meta_loop._run_idea_refresh error: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Autonomous observation
# ---------------------------------------------------------------------------

def _run_autonomous_observation() -> int:
    """Run the autonomous observer. Returns count of observations recorded."""
    try:
        from core.self_observation.autonomous_observer import observe_and_record
        return observe_and_record()
    except Exception as exc:
        logger.debug("meta_loop._run_autonomous_observation error: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Main reflect function
# ---------------------------------------------------------------------------

def reflect(ingested_count=0):
    """
    Run one reflection cycle. Called after each daemon poll.

    Args:
        ingested_count: number of files ingested in this cycle.

    Returns:
        dict with reflection summary.
    """
    global _last_idea_refresh

    state = state_manager.load()

    # --- Layer 1: Activity ---
    activity = "idle"
    if ingested_count > 0:
        activity = f"active ({ingested_count} ingested)"

    # --- Layer 2: Health ---
    errors = state.get("errors_since_boot", 0)
    if errors == 0:
        health = "nominal"
    elif errors < 5:
        health = f"degraded ({errors} errors)"
    else:
        health = f"critical ({errors} errors)"

    # --- Layer 3: Rhythm ---
    cycles = state.get("cycles", 0)
    uptime = _compute_uptime(state.get("boot_time"))

    reflection = {
        "timestamp": _now_iso(),
        "activity": activity,
        "health": health,
        "cycles": cycles,
        "uptime": uptime,
    }

    # --- Layer 4: Ideas (auto-refresh cada 15 min) ---
    now = time.time()
    idea_alerts = 0
    if now - _last_idea_refresh >= _IDEA_REFRESH_INTERVAL:
        _last_idea_refresh = now
        idea_alerts = _run_idea_refresh()
        reflection["idea_alerts_sent"] = idea_alerts

    # --- Layer 5: Autonomous observation (every cycle) ---
    obs_count = _run_autonomous_observation()
    if obs_count > 0:
        reflection["observations_recorded"] = obs_count

    # Persist reflection into state
    state["last_reflection"] = reflection
    state_manager.save(state)

    return reflection
