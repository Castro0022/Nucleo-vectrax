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

from __future__ import annotations

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
_last_obs_alert_id: int = 0     # track last observation alerted

# Rate limiter for observation alerts
_OBS_ALERT_MAX_PER_MIN = 5      # max alerts per minute (Telegram rate limit ~30/s)
_OBS_ALERT_MAX_PER_CYCLE = 3    # max alerts per meta_loop cycle
_obs_alert_timestamps: list = []  # timestamps of recent alerts for rate limiting
_last_ram_snapshot: float = 0.0   # last hourly RAM recording
_RAM_SNAPSHOT_INTERVAL = 3600     # 1 hour


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

    IMPORTANT: pasa por telegram_guard para respetar silent mode.
    Antes enviaba directo sin filtro — causaba que eventos internos
    (gravity/universe_growth, observaciones INFO) llegaran al chat.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token or not chat_id:
        logger.debug("_send_telegram: sin token o chat_id, skip.")
        return False

    # Strip HTML tags for guard evaluation (guard checks plain text)
    import re as _re
    plain = _re.sub(r"<[^>]+>", "", text)

    # Telegram Guard — block telemetry/metrics/observations
    try:
        from core.telegram_guard import should_send_to_user
        if not should_send_to_user(plain, reason="observation"):
            logger.info(
                "meta_loop: GUARD blocked alert to %s | %s",
                chat_id, plain[:60].replace("\n", " "),
            )
            return True  # pretend sent — observation is logged, just not sent
    except Exception:
        pass

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
# Observation alerts (Layer 6)
# ---------------------------------------------------------------------------

# Observation types that trigger Telegram alerts
_ALERT_OBS_TYPES = {
    # severity: warning/critical always alert
    "worker_state",  "error_spike",  "snapshot_failure",
    # trade events
    "trade_executed", "trade_validation", "position_closed",
    # mode transitions and worker incidents (operational)
    "mode_transition",
    "worker_incident", "worker_diagnosis",
    # REMOVED: universe_growth, convergence_detected, daily_reflection
    # These are informational, not actionable. They were flooding the
    # creator's chat with internal telemetry ("Universo gravitacional
    # creció +1 estrellas"). Observations are still recorded in the
    # ledger and visible on the dashboard.
}

def _is_rate_limited() -> bool:
    """Check if we've exceeded the per-minute alert rate limit."""
    now = time.time()
    cutoff = now - 60
    # Prune old timestamps
    while _obs_alert_timestamps and _obs_alert_timestamps[0] < cutoff:
        _obs_alert_timestamps.pop(0)
    return len(_obs_alert_timestamps) >= _OBS_ALERT_MAX_PER_MIN


def _send_observation_alerts() -> int:
    """Check recent observations and send Telegram alerts for critical ones.

    Rate limits:
      - Max 5 alerts per minute (prevents Telegram 429 Too Many Requests)
      - Max 3 alerts per cycle (prevents burst flooding)
      - severity=critical bypasses per-cycle limit (never suppressed)
      - Remaining alerts deferred to next cycle (ID tracking ensures no loss)
    """
    global _last_obs_alert_id
    creator_chat_id = os.getenv("TELEGRAM_CREATOR_CHAT_ID", "")
    if not creator_chat_id:
        return 0
    try:
        from core.self_observation.observation_ledger import get_recent
        recent = get_recent(limit=20)
        if not recent:
            return 0

        # Process oldest first so _last_obs_alert_id advances correctly
        recent.sort(key=lambda o: o.get("id", 0))

        sent = 0
        deferred = 0
        for obs in recent:
            obs_id = obs.get("id", 0)
            if obs_id <= _last_obs_alert_id:
                continue

            severity = obs.get("severity", "info")
            obs_type = obs.get("obs_type", "")

            # Alert on warning/critical severity OR specific trade/anomaly types
            should_alert = (
                severity in ("warning", "critical")
                or obs_type in _ALERT_OBS_TYPES
            )
            if not should_alert:
                _last_obs_alert_id = max(_last_obs_alert_id, obs_id)
                continue

            # Rate limit check (critical bypasses per-cycle limit)
            is_critical = severity == "critical"
            if _is_rate_limited():
                deferred += 1
                break  # stop processing, retry next cycle
            if not is_critical and sent >= _OBS_ALERT_MAX_PER_CYCLE:
                deferred += 1
                break  # defer remaining to next cycle

            # Build alert message
            icons = {"critical": "\U0001f534", "warning": "\u26a0\ufe0f", "info": "\U0001f535"}
            icon = icons.get(severity, "\U0001f535")
            domain = obs.get("domain", "?")
            summary = obs.get("summary", "")[:120]
            star = obs.get("star_id", "")
            ts = obs.get("timestamp", "")[:16]

            msg = f"{icon} <b>Observaci\u00f3n [{severity.upper()}]</b>\n"
            msg += f"<code>{domain}/{obs_type}</code>\n"
            msg += f"{summary}"
            if star:
                msg += f"\n\u2b50 {star}"
            msg += f"\n<i>{ts}</i>"

            if _send_telegram(creator_chat_id, msg):
                sent += 1
                _obs_alert_timestamps.append(time.time())
                logger.info(
                    "meta_loop: observation alert sent — %s/%s [%s]",
                    domain, obs_type, severity,
                )

            _last_obs_alert_id = max(_last_obs_alert_id, obs_id)

        if deferred:
            logger.info(
                "meta_loop: %d alerts deferred (rate limit: %d/%d per min, %d/%d per cycle)",
                deferred, len(_obs_alert_timestamps), _OBS_ALERT_MAX_PER_MIN,
                sent, _OBS_ALERT_MAX_PER_CYCLE,
            )

        return sent
    except Exception as exc:
        logger.debug("meta_loop._send_observation_alerts error: %s", exc)
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

    # --- Layer 6: Telegram alerts for critical observations ---
    obs_alerts = _send_observation_alerts()
    if obs_alerts > 0:
        reflection["observation_alerts_sent"] = obs_alerts

    # --- Layer 7: Hourly RAM snapshot ---
    obs_ram = _record_ram_snapshot()
    if obs_ram:
        reflection["ram_snapshot"] = obs_ram

    # --- Layer 8: Daily evolution snapshot (once per day) ---
    try:
        from core.self_observation.evolution_memory import record_daily_snapshot, get_snapshot
        if not get_snapshot(0):  # no snapshot for today yet
            snap = record_daily_snapshot()
            reflection["evolution_snapshot"] = True
    except Exception as _ev:
        logger.debug("evolution snapshot failed: %s", _ev)

    # Persist reflection into state
    state["last_reflection"] = reflection
    state_manager.save(state)

    return reflection


def _record_ram_snapshot() -> dict | None:
    """Record worker RAM to observation ledger every hour.

    After 24 samples, generates a stability report.
    """
    global _last_ram_snapshot
    now = time.time()
    if now - _last_ram_snapshot < _RAM_SNAPSHOT_INTERVAL:
        return None
    _last_ram_snapshot = now

    try:
        import os
        from pathlib import Path

        # Find worker PID and RAM
        worker_ram = 0.0
        worker_pid = 0
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
                if "pipeline_worker" not in cmdline:
                    continue
                for line in Path(f"/proc/{pid}/status").read_text().split("\n"):
                    if line.startswith("VmRSS:"):
                        worker_ram = int(line.split()[1]) / 1024
                        worker_pid = int(pid)
                        break
            except Exception:
                continue
            if worker_ram > 0:
                break

        if worker_ram == 0:
            return None

        snapshot = {
            "pid": worker_pid,
            "ram_mb": round(worker_ram, 1),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }

        # Record to observation ledger
        from core.self_observation.observation_ledger import record
        record(
            domain="operator",
            obs_type="ram_snapshot",
            summary=f"Worker RAM: {worker_ram:.0f}MB (PID {worker_pid})",
            star_id=f"worker:pipeline_worker",
            evidence=snapshot,
        )

        logger.info("RAM_SNAPSHOT | worker PID %d: %.0fMB", worker_pid, worker_ram)
        return snapshot

    except Exception as exc:
        logger.debug("RAM snapshot failed: %s", exc)
        return None
