"""
Vectrax Scheduler — Tareas Programadas
=========================================
Permite a los usuarios programar recordatorios y tareas recurrentes
desde Telegram usando lenguaje natural.

Ejemplos:
  "Despiértame mañana a las 7am"
  "Recuérdame llamar a Carlos el viernes"
  "Dame el tráfico todas las mañanas a las 6:30"
  "En 30 minutos avísame"

Arquitectura:
  - SQLite table en vault/scheduler.db
  - parse_schedule() convierte texto natural en schedule
  - run_scheduler_tick() corre cada 60s en el pipeline worker
  - add_task() crea tareas desde el gateway

Creado: 2026-04-16
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.scheduler")

_SCHEDULER_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "scheduler.db",
)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    chat_id       INTEGER NOT NULL,
    action_type   TEXT NOT NULL DEFAULT 'reminder',
    message       TEXT NOT NULL,
    original_text TEXT NOT NULL DEFAULT '',
    schedule_type TEXT NOT NULL DEFAULT 'once',
    time_expr     TEXT NOT NULL DEFAULT '',
    days_of_week  TEXT NOT NULL DEFAULT '',
    next_run      REAL NOT NULL,
    timezone      TEXT NOT NULL DEFAULT 'America/New_York',
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sched_next ON scheduled_tasks(next_run, enabled);
CREATE INDEX IF NOT EXISTS idx_sched_user ON scheduled_tasks(user_id, enabled);
"""

TICK_INTERVAL = 60  # seconds between scheduler checks


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_SCHEDULER_DB), exist_ok=True)
    conn = sqlite3.connect(_SCHEDULER_DB, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.executescript(_CREATE_SQL)
    return conn


# ---------------------------------------------------------------------------
# Time parsing — Natural language → schedule
# ---------------------------------------------------------------------------

_DAY_NAMES_ES = {
    "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2,
    "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5, "domingo": 6,
}
_DAY_NAMES_EN = {
    "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
}
_ALL_DAYS = {**_DAY_NAMES_ES, **_DAY_NAMES_EN}

# Months (Spanish)
_MONTHS_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def _extract_time(text: str) -> Tuple[int, int]:
    """Extract hour:minute from text. Returns (hour, minute) or (9, 0) as default."""
    t = text.lower()

    # "7:30 am", "7:30am", "7:30 pm", "19:30"
    m = re.search(r'(\d{1,2})[:\.](\d{2})\s*(am|pm|a\.m|p\.m)?', t)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        ampm = (m.group(3) or "").replace(".", "")
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        return (h, mi)

    # "7am", "7 am", "7pm", "7 pm"
    m = re.search(r'(\d{1,2})\s*(am|pm|a\.m|p\.m)', t)
    if m:
        h = int(m.group(1))
        ampm = m.group(2).replace(".", "")
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        return (h, 0)

    # "a las 7", "a las 19", "at 7"
    m = re.search(r'(?:a las|at)\s+(\d{1,2})(?::(\d{2}))?', t)
    if m:
        h = int(m.group(1))
        mi = int(m.group(2)) if m.group(2) else 0
        # If h <= 6, likely PM (e.g., "a las 5" = 5pm)
        if h <= 6:
            h += 12
        return (h, mi)

    # Bare "las 7", "las 19:30"
    m = re.search(r'las\s+(\d{1,2})(?::(\d{2}))?', t)
    if m:
        h = int(m.group(1))
        mi = int(m.group(2)) if m.group(2) else 0
        if h <= 6:
            h += 12
        return (h, mi)

    return (9, 0)  # default: 9:00 AM


def _extract_relative_minutes(text: str) -> Optional[int]:
    """Extract 'en N minutos/horas' → minutes from now."""
    t = text.lower()
    m = re.search(r'en\s+(\d+)\s*(?:minuto|min)', t)
    if m:
        return int(m.group(1))
    m = re.search(r'en\s+(\d+)\s*(?:hora|hour|hr)', t)
    if m:
        return int(m.group(1)) * 60
    m = re.search(r'in\s+(\d+)\s*(?:minute|min)', t)
    if m:
        return int(m.group(1))
    m = re.search(r'in\s+(\d+)\s*(?:hour|hr)', t)
    if m:
        return int(m.group(1)) * 60
    return None


def _is_recurring(text: str) -> bool:
    """Detect if the text describes a recurring schedule."""
    t = text.lower()
    return bool(re.search(
        r'(?:todas? las|every|cada|diario|diaria|daily|semanal|weekly'
        r'|todos los d[ií]as|every day|every morning|cada ma[ñn]ana)',
        t,
    ))


def _extract_weekdays(text: str) -> List[int]:
    """Extract specific weekdays from text. Returns list of weekday ints (0=Mon)."""
    t = text.lower()
    days = []
    for name, wd in _ALL_DAYS.items():
        if name in t:
            days.append(wd)
    return sorted(set(days))


def _extract_reminder_text(text: str) -> str:
    """Extract the 'what to remind' part from the scheduling instruction."""
    t = text.strip()

    # Remove scheduling prefixes
    t = re.sub(
        r'^(?:despi[eé]rtame|recu[eé]rdame|av[ií]same|rem[ií]ndme|wake me(?: up)?'
        r'|remind me(?:\s+to)?|alerta|alert me)\s*',
        '', t, flags=re.IGNORECASE,
    ).strip()

    # Remove time expressions
    t = re.sub(r'(?:ma[ñn]ana|tomorrow|hoy|today)\s*', '', t, flags=re.IGNORECASE).strip()
    t = re.sub(r'(?:a las|at)\s+\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm|a\.m|p\.m)?\s*', '', t, flags=re.IGNORECASE).strip()
    t = re.sub(r'\d{1,2}[:.]\d{2}\s*(?:am|pm)?\s*', '', t, flags=re.IGNORECASE).strip()
    t = re.sub(r'\d{1,2}\s*(?:am|pm)\s*', '', t, flags=re.IGNORECASE).strip()
    t = re.sub(r'(?:en\s+\d+\s*(?:minutos?|horas?|min|hr|hours?|minutes?))\s*', '', t, flags=re.IGNORECASE).strip()
    t = re.sub(r'(?:el\s+)?(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\s*', '', t, flags=re.IGNORECASE).strip()
    t = re.sub(r'(?:todas? las ma[ñn]anas|every (?:morning|day)|cada d[ií]a|diario)\s*', '', t, flags=re.IGNORECASE).strip()
    t = re.sub(r'(?:cada|every)\s+\w+\s*', '', t, flags=re.IGNORECASE).strip()

    # Clean up leftover connectors
    t = re.sub(r'^(?:que|de|para|to|a)\s+', '', t, flags=re.IGNORECASE).strip()
    t = re.sub(r'^[,.\-—]+\s*', '', t).strip()

    return t if t else ""


def parse_schedule(text: str, tz: str = "America/New_York") -> Optional[Dict[str, Any]]:
    """
    Parse natural language schedule instruction.

    Returns dict with:
        schedule_type: "once" | "recurring"
        next_run: float (UTC timestamp)
        time_expr: "HH:MM"
        days_of_week: "0,1,2,3,4" (empty = daily)
        message: extracted reminder text
        action_type: "reminder" | "info_traffic" | "info_weather" | "info_market"
    """
    t = text.lower().strip()

    # Detect action type
    action_type = "reminder"
    if re.search(r'(?:tr[aá]fico|traffic|ruta|commute)', t):
        action_type = "info_traffic"
    elif re.search(r'(?:clima|weather|temperatura|temperature|llover|lluvia|rain)', t):
        action_type = "info_weather"
    elif re.search(r'(?:mercado|market|btc|bitcoin|eth|crypto|cripto|bolsa|stock)', t):
        action_type = "info_market"

    # Try to compute timezone offset
    try:
        from zoneinfo import ZoneInfo
        user_tz = ZoneInfo(tz)
    except Exception:
        user_tz = None

    now = datetime.now()
    if user_tz:
        try:
            from datetime import timezone
            now = datetime.now(user_tz).replace(tzinfo=None)
        except Exception:
            pass

    # Relative time: "en 30 minutos"
    rel_min = _extract_relative_minutes(text)
    if rel_min is not None:
        next_run = now + timedelta(minutes=rel_min)
        reminder = _extract_reminder_text(text)
        if not reminder and action_type == "reminder":
            reminder = "⏰ Recordatorio"
        return {
            "schedule_type": "once",
            "next_run": next_run.timestamp() if not user_tz else _to_utc(next_run, user_tz),
            "time_expr": next_run.strftime("%H:%M"),
            "days_of_week": "",
            "message": reminder,
            "action_type": action_type,
        }

    # Extract time
    hour, minute = _extract_time(text)

    # Is it recurring?
    recurring = _is_recurring(text)
    weekdays = _extract_weekdays(text)

    # Build next_run
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if recurring:
        if weekdays:
            # Next occurrence of the specified weekday(s)
            today_wd = now.weekday()
            future_days = []
            for wd in weekdays:
                diff = (wd - today_wd) % 7
                if diff == 0 and target <= now:
                    diff = 7
                future_days.append(diff)
            days_ahead = min(future_days) if future_days else 1
            target = now.replace(hour=hour, minute=minute, second=0) + timedelta(days=days_ahead)
        else:
            # Daily — next occurrence
            if target <= now:
                target += timedelta(days=1)
    else:
        # One-shot
        if re.search(r'ma[ñn]ana|tomorrow', t):
            target = now.replace(hour=hour, minute=minute, second=0) + timedelta(days=1)
        elif re.search(r'hoy|today', t):
            target = now.replace(hour=hour, minute=minute, second=0)
            if target <= now:
                target += timedelta(days=1)
        elif weekdays:
            today_wd = now.weekday()
            wd = weekdays[0]
            diff = (wd - today_wd) % 7
            if diff == 0 and target <= now:
                diff = 7
            target = now.replace(hour=hour, minute=minute, second=0) + timedelta(days=diff)
        else:
            if target <= now:
                target += timedelta(days=1)

    # Extract reminder text
    reminder = _extract_reminder_text(text)
    if not reminder:
        if action_type == "info_traffic":
            reminder = "🚗 Estado del tráfico"
        elif action_type == "info_weather":
            reminder = "🌤 Estado del clima"
        elif action_type == "info_market":
            reminder = "📈 Estado del mercado"
        else:
            reminder = "⏰ Recordatorio"

    # Convert to UTC if timezone available
    next_run_ts = target.timestamp()
    if user_tz:
        next_run_ts = _to_utc(target, user_tz)

    days_str = ",".join(str(d) for d in weekdays) if weekdays else ""

    return {
        "schedule_type": "recurring" if recurring else "once",
        "next_run": next_run_ts,
        "time_expr": f"{hour:02d}:{minute:02d}",
        "days_of_week": days_str,
        "message": reminder,
        "action_type": action_type,
    }


def _to_utc(dt: datetime, tz) -> float:
    """Convert naive datetime in user tz to UTC timestamp."""
    try:
        localized = dt.replace(tzinfo=tz)
        return localized.timestamp()
    except Exception:
        return dt.timestamp()


# ---------------------------------------------------------------------------
# Task CRUD
# ---------------------------------------------------------------------------

def add_task(
    user_id: str,
    chat_id: int,
    text: str,
    tz: str = "America/New_York",
) -> Optional[Dict[str, Any]]:
    """Create a scheduled task from natural language text."""
    parsed = parse_schedule(text, tz=tz)
    if not parsed:
        return None

    try:
        db = _conn()
        db.execute(
            """INSERT INTO scheduled_tasks
               (user_id, chat_id, action_type, message, original_text,
                schedule_type, time_expr, days_of_week, next_run, timezone, enabled, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
            (
                user_id, chat_id, parsed["action_type"], parsed["message"],
                text, parsed["schedule_type"], parsed["time_expr"],
                parsed["days_of_week"], parsed["next_run"], tz, time.time(),
            ),
        )
        task_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.commit()
        db.close()

        return {
            "id": task_id,
            "action_type": parsed["action_type"],
            "message": parsed["message"],
            "schedule_type": parsed["schedule_type"],
            "time_expr": parsed["time_expr"],
            "days_of_week": parsed["days_of_week"],
            "next_run": parsed["next_run"],
        }
    except Exception as exc:
        logger.error("add_task failed: %s", exc)
        return None


def add_task_structured(
    user_id: str,
    chat_id: int,
    content: str,
    when_text: str,
    tz: str = "America/New_York",
) -> Optional[Dict[str, Any]]:
    """Crea una tarea programada a partir de parámetros YA estructurados
    (Diseño A/B/C, Frente C): `content` es el texto del recordatorio, ya sin
    ruido conversacional; `when_text` es la expresión temporal en lenguaje
    natural tal como la escribió el usuario ("mañana", "el viernes a las
    7am", "en 30 minutos").

    Reutiliza `parse_schedule()` ÚNICAMENTE para resolver FECHA/HORA a partir
    de `when_text` — eso sigue siendo NLP de fecha legítimo ("mañana" →
    timestamp), no interpretación de mensaje conversacional completo. NUNCA
    vuelve a extraer el texto del recordatorio desde `when_text`/`content`
    vía `_extract_reminder_text()` — `content` se usa tal cual.
    """
    parsed = parse_schedule(when_text, tz=tz) or {}
    schedule_type = parsed.get("schedule_type", "once")
    next_run = parsed.get("next_run") or (time.time() + 86400)
    time_expr = parsed.get("time_expr", "09:00")
    days_of_week = parsed.get("days_of_week", "")
    action_type = parsed.get("action_type", "reminder")
    message = (content or "").strip() or parsed.get("message") or "\u23f0 Recordatorio"
    original_text = f"{content} | {when_text}".strip(" |")

    try:
        db = _conn()
        db.execute(
            """INSERT INTO scheduled_tasks
               (user_id, chat_id, action_type, message, original_text,
                schedule_type, time_expr, days_of_week, next_run, timezone, enabled, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
            (
                user_id, chat_id, action_type, message,
                original_text, schedule_type, time_expr,
                days_of_week, next_run, tz, time.time(),
            ),
        )
        task_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.commit()
        db.close()
        return {
            "id": task_id,
            "action_type": action_type,
            "message": message,
            "schedule_type": schedule_type,
            "time_expr": time_expr,
            "days_of_week": days_of_week,
            "next_run": next_run,
        }
    except Exception as exc:
        logger.error("add_task_structured failed: %s", exc)
        return None


def list_tasks(user_id: str) -> List[Dict[str, Any]]:
    """List active tasks for a user."""
    try:
        db = _conn()
        rows = db.execute(
            "SELECT id, action_type, message, schedule_type, time_expr, "
            "days_of_week, next_run, timezone FROM scheduled_tasks "
            "WHERE user_id=? AND enabled=1 ORDER BY next_run",
            (user_id,),
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def delete_task(user_id: str, task_id: int) -> bool:
    """Delete (disable) a task."""
    try:
        db = _conn()
        n = db.execute(
            "UPDATE scheduled_tasks SET enabled=0 WHERE id=? AND user_id=?",
            (task_id, user_id),
        ).rowcount
        db.commit()
        db.close()
        return n > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Scheduler tick — runs every 60s in the pipeline worker
# ---------------------------------------------------------------------------

_DAY_NAMES_SHORT = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def _compute_next_run(task: Dict, user_tz=None) -> float:
    """Compute next run timestamp for a recurring task."""
    time_expr = task["time_expr"]
    days_of_week = task["days_of_week"]

    try:
        h, m = [int(x) for x in time_expr.split(":")]
    except Exception:
        h, m = 9, 0

    now = datetime.now()
    if user_tz:
        try:
            now = datetime.now(user_tz).replace(tzinfo=None)
        except Exception:
            pass

    if days_of_week:
        # Specific weekdays
        target_days = [int(d) for d in days_of_week.split(",")]
        today_wd = now.weekday()
        today_time = now.replace(hour=h, minute=m, second=0, microsecond=0)

        min_diff = 8  # larger than any week offset
        for wd in target_days:
            diff = (wd - today_wd) % 7
            if diff == 0:
                if today_time > now:
                    diff = 0
                else:
                    diff = 7
            if diff < min_diff:
                min_diff = diff

        target = now.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=min_diff)
    else:
        # Daily
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)

    if user_tz:
        return _to_utc(target, user_tz)
    return target.timestamp()


def run_scheduler_tick(tg_send_fn) -> int:
    """
    Check and execute due scheduled tasks.
    Called every TICK_INTERVAL seconds from the pipeline worker.

    Args:
        tg_send_fn: function(chat_id: int, text: str) → bool

    Returns:
        Number of tasks executed.
    """
    executed = 0
    now = time.time()

    try:
        db = _conn()
        due_tasks = db.execute(
            "SELECT * FROM scheduled_tasks WHERE enabled=1 AND next_run <= ?",
            (now,),
        ).fetchall()
    except Exception as exc:
        logger.debug("scheduler tick: DB error: %s", exc)
        return 0

    for row in due_tasks:
        task = dict(row)
        try:
            # Build the message to send
            msg = _build_task_message(task)

            # Send to Telegram
            ok = tg_send_fn(task["chat_id"], msg)
            if ok:
                executed += 1
                logger.info(
                    "SCHED | sent %s to %s | %s",
                    task["action_type"], task["user_id"][:20], task["message"][:40],
                )

            # Update: disable if once, compute next_run if recurring
            if task["schedule_type"] == "recurring":
                # Load user timezone
                user_tz = None
                try:
                    from zoneinfo import ZoneInfo
                    user_tz = ZoneInfo(task["timezone"])
                except Exception:
                    pass
                next_run = _compute_next_run(task, user_tz)
                db.execute(
                    "UPDATE scheduled_tasks SET next_run=? WHERE id=?",
                    (next_run, task["id"]),
                )
            else:
                db.execute(
                    "UPDATE scheduled_tasks SET enabled=0 WHERE id=?",
                    (task["id"],),
                )

        except Exception as exc:
            logger.error("scheduler task %d failed: %s", task["id"], exc)
            # Disable broken task to avoid infinite retries
            db.execute(
                "UPDATE scheduled_tasks SET enabled=0 WHERE id=?",
                (task["id"],),
            )

    try:
        db.commit()
        db.close()
    except Exception:
        pass

    return executed


def _build_task_message(task: Dict) -> str:
    """Build the Telegram message for a task execution."""
    action = task["action_type"]
    msg = task["message"]

    if action == "info_market":
        # Try to get live market data
        try:
            from intents.market_intents import detect_market_intent, handle_market_intent
            detected = detect_market_intent(msg)
            if detected:
                result = handle_market_intent(detected[0], detected[1])
                if result.get("success") and result.get("response"):
                    return f"📈 Mercado programado:\n{result['response']}"
            # Default: BTC
            result = handle_market_intent("bitcoin_status", {"symbol": "BTCUSDT"})
            if result.get("success") and result.get("response"):
                return f"📈 Mercado:\n{result['response']}"
        except Exception:
            pass
        return f"📈 {msg}"

    if action == "info_weather":
        try:
            from vectrax.integrations.weather import get_weather_text
            # Extract city from message or default to Miami
            import re
            city_m = re.search(r'(?:en|in)\s+([A-Za-záéíóúñ\s]+)', msg)
            city = city_m.group(1).strip().title() if city_m else "Miami"
            return get_weather_text(city, lang="es")
        except Exception:
            pass
        return f"🌤 {msg}"

    if action == "info_traffic":
        return f"🚗 {msg}\n(Integración de tráfico pendiente — conectar API)"

    # Default: reminder
    return f"🔔 {msg}"


# ---------------------------------------------------------------------------
# Formatting helpers (for gateway responses)
# ---------------------------------------------------------------------------

def format_task_confirmation(task: Dict, tz: str = "America/New_York") -> str:
    """Format a user-friendly confirmation message after creating a task."""
    stype = task["schedule_type"]
    time_expr = task["time_expr"]
    action = task["action_type"]
    msg = task["message"]
    days = task.get("days_of_week", "")

    # Icon by type
    icons = {
        "reminder": "🔔",
        "info_traffic": "🚗",
        "info_weather": "🌤",
        "info_market": "📈",
    }
    icon = icons.get(action, "📌")

    if stype == "recurring":
        if days:
            day_labels = []
            for d in days.split(","):
                try:
                    day_labels.append(_DAY_NAMES_SHORT[int(d)])
                except (IndexError, ValueError):
                    pass
            when = f"Cada {', '.join(day_labels)} a las {time_expr}"
        else:
            when = f"Todos los días a las {time_expr}"
    else:
        # One-shot: show date
        try:
            next_dt = datetime.fromtimestamp(task["next_run"])
            try:
                from zoneinfo import ZoneInfo
                user_tz = ZoneInfo(tz)
                from datetime import timezone as _tz
                next_dt = datetime.fromtimestamp(task["next_run"], tz=user_tz)
            except Exception:
                pass
            when = next_dt.strftime("%d/%m %H:%M")
        except Exception:
            when = time_expr

    lines = [f"{icon} Programado: {msg}", f"📅 {when}"]
    if stype == "recurring":
        lines.append("🔄 Recurrente")

    return "\n".join(lines)


def format_task_list(tasks: List[Dict], tz: str = "America/New_York") -> str:
    """Format list of tasks for display."""
    if not tasks:
        return "No tienes tareas programadas."

    icons = {
        "reminder": "🔔",
        "info_traffic": "🚗",
        "info_weather": "🌤",
        "info_market": "📈",
    }

    lines = ["📋 Tareas programadas:"]
    for t in tasks:
        icon = icons.get(t["action_type"], "📌")
        stype = "🔄" if t["schedule_type"] == "recurring" else "1️⃣"
        time_str = t["time_expr"]
        days = t.get("days_of_week", "")
        if days:
            day_labels = []
            for d in days.split(","):
                try:
                    day_labels.append(_DAY_NAMES_SHORT[int(d)])
                except (IndexError, ValueError):
                    pass
            time_str += f" ({', '.join(day_labels)})"

        lines.append(f"\n{icon} #{t['id']} | {time_str} {stype}")
        lines.append(f"   {t['message'][:50]}")

    lines.append("\nEliminar: /tasks del <número>")
    return "\n".join(lines)
