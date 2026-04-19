"""
Vectrax Cognitive Bridge — Continuidad Cognitiva
====================================================
Conecta la memoria con el scheduler automáticamente.

Cuando el usuario dice "tengo reunión con Carlos el viernes",
el sistema:
  1. fact_memory guarda: date, Carlos, viernes, reunión
  2. cognitive_bridge detecta el hecho temporal →
     - Cruza con TODO lo que sabe de Carlos (es cliente, le debes $500, etc.)
     - Crea recordatorio automático con contexto enriquecido
  3. El viernes, el scheduler envía:
     "Mario, hoy tienes reunión con Carlos.
      Contexto: cliente, le debes propuesta del logo ($500)."

Principio: una sola frase del usuario → memoria + acción futura + contexto cruzado.

Se llama automáticamente después de store_facts() en user_memory.store_memory().
NO toca ni modifica fact_memory ni scheduler — solo los conecta.

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
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.cognitive_bridge")

_USER_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "user_memory.db",
)

# Time words that indicate a fact has a temporal component
_TEMPORAL_FACT_TYPES = {"date", "decision"}

# Day name mapping for building readable messages
_DAY_NAMES = {
    "lunes": "lunes", "martes": "martes", "miércoles": "miércoles",
    "miercoles": "miércoles", "jueves": "jueves", "viernes": "viernes",
    "sábado": "sábado", "sabado": "sábado", "domingo": "domingo",
    "mañana": "mañana", "hoy": "hoy", "pasado mañana": "pasado mañana",
}


def bridge_facts_to_scheduler(
    user_id: str,
    chat_id: int,
    text: str,
    stored_count: int,
) -> int:
    """
    After facts are stored, check if any have temporal info
    and automatically create enriched scheduler tasks.

    Args:
        user_id: Telegram user ID (e.g. "tg:123456")
        chat_id: Telegram chat ID for sending the reminder
        text: Original user message
        stored_count: Number of facts that were stored

    Returns:
        Number of automatic tasks created.
    """
    if stored_count == 0:
        return 0

    # Don't bridge if the user explicitly used scheduling keywords
    # (the gateway already handles those via _detect_schedule)
    if _is_explicit_schedule(text):
        return 0

    # Get the most recently stored facts for this user
    recent_facts = _get_recent_facts(user_id, limit=5, max_age_seconds=10)
    if not recent_facts:
        return 0

    created = 0
    for fact in recent_facts:
        if fact["fact_type"] not in _TEMPORAL_FACT_TYPES:
            continue

        # Already has a scheduler task for this exact content?
        if _task_already_exists(user_id, fact):
            continue

        # Build enriched reminder with cross-context
        enriched = _build_enriched_reminder(user_id, fact)
        if not enriched:
            continue

        # Create scheduler task
        task = _create_auto_task(user_id, chat_id, fact, enriched)
        if task:
            created += 1
            logger.info(
                "BRIDGE | auto-task #%d for %s | %s → %s",
                task["id"], user_id[:15], fact["value"][:20], enriched["message"][:40],
            )

    return created


def _is_explicit_schedule(text: str) -> bool:
    """Check if text uses explicit scheduling keywords (already handled by gateway)."""
    t = text.lower()
    return bool(re.search(
        r'(?:despi[eé]rtame|recu[eé]rdame|av[ií]same|wake me|remind me'
        r'|todas las ma[ñn]anas|every morning|cada d[ií]a)',
        t,
    ))


def _get_recent_facts(user_id: str, limit: int = 5, max_age_seconds: int = 10) -> List[Dict]:
    """Get facts stored in the last N seconds (from the current store_facts call)."""
    cutoff = time.time() - max_age_seconds
    try:
        conn = sqlite3.connect(_USER_DB, timeout=3)
        rows = conn.execute(
            "SELECT id, fact_type, subject, value, detail, raw_input, timestamp "
            "FROM user_facts "
            "WHERE user_id = ? AND timestamp >= ? AND status = 'active' "
            "ORDER BY timestamp DESC LIMIT ?",
            (user_id, cutoff, limit),
        ).fetchall()
        conn.close()
        return [
            {
                "id": r[0], "fact_type": r[1], "subject": r[2],
                "value": r[3], "detail": r[4], "raw_input": r[5],
                "timestamp": r[6],
            }
            for r in rows
        ]
    except Exception:
        return []


def _task_already_exists(user_id: str, fact: Dict) -> bool:
    """Check if a scheduler task already covers this fact (avoid duplicates)."""
    try:
        from core.scheduler import _conn as sched_conn
        conn = sched_conn()
        # Check by original_text similarity (within last hour)
        cutoff = time.time() - 3600
        row = conn.execute(
            "SELECT 1 FROM scheduled_tasks "
            "WHERE user_id = ? AND enabled = 1 AND created_at >= ? "
            "AND original_text = ?",
            (user_id, cutoff, fact.get("raw_input", "")),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def _get_person_context(user_id: str, name: str) -> List[str]:
    """
    Get ALL known context about a person from memory.
    Searches: user_facts, core_memory, profiles.
    Returns list of context strings.
    """
    if not name or name in ("_general", "_self", ""):
        return []

    context = []
    try:
        conn = sqlite3.connect(_USER_DB, timeout=3)

        # Facts about this person (money, dates, relationships, etc.)
        rows = conn.execute(
            "SELECT fact_type, subject, value, detail FROM user_facts "
            "WHERE user_id = ? AND LOWER(subject) = LOWER(?) AND status = 'active' "
            "ORDER BY timestamp DESC LIMIT 10",
            (user_id, name),
        ).fetchall()

        for ft, subj, val, det in rows:
            if ft == "money":
                context.append(f"te debe ${val}" + (f" ({det})" if det else ""))
            elif ft == "lend":
                context.append(f"le prestaste ${val}" + (f" ({det})" if det else ""))
            elif ft == "person":
                context.append(f"es tu {val}")
            elif ft == "price":
                context.append(f"le cobras ${val}")
            elif ft == "note":
                context.append(val[:60])

        # Core memory about this person
        rows2 = conn.execute(
            "SELECT content FROM core_memory "
            "WHERE user_id = ? AND LOWER(content) LIKE LOWER(?) "
            "ORDER BY weight DESC LIMIT 5",
            (user_id, f"%{name}%"),
        ).fetchall()

        for (content,) in rows2:
            # Avoid duplicating what we already have
            short = content[:80].strip(".")
            if short and not any(short.lower() in c.lower() for c in context):
                context.append(short)

        # Lead tracker info
        try:
            from core.lead_tracker import get_leads
            leads = get_leads(user_id)
            lead = next((l for l in leads if l["name"].lower() == name.lower()), None)
            if lead:
                days = lead.get("days_silent", 0)
                status = lead.get("status", "")
                ctx = lead.get("context", "")
                lead_info = f"lead ({status})"
                if days > 1:
                    lead_info += f", {int(days)}d sin contacto"
                if ctx:
                    lead_info += f", {ctx[:40]}"
                context.append(lead_info)
        except Exception:
            pass

        conn.close()
    except Exception as exc:
        logger.debug("_get_person_context failed: %s", exc)

    return context


def _build_enriched_reminder(user_id: str, fact: Dict) -> Optional[Dict]:
    """
    Build an enriched reminder message with cross-context.

    Returns dict with:
        message: the enriched text for the reminder
        time_text: temporal expression from the fact
    """
    subject = fact.get("subject", "")
    value = fact.get("value", "")  # temporal expression (e.g., "viernes", "mañana a las 9")
    detail = fact.get("detail", "")

    if not value:
        return None

    # Build the base reminder
    is_person = (
        subject
        and subject != "_general"
        and subject != "_self"
        and subject[0].isupper()
    )

    # Get person context for cross-referencing
    person_context = []
    if is_person:
        person_context = _get_person_context(user_id, subject)

    # Build message parts
    parts = []

    # Main event
    if is_person:
        event_desc = detail if detail and detail.lower() != subject.lower() else ""
        if event_desc:
            parts.append(f"{event_desc} con {subject}")
        else:
            parts.append(f"Evento con {subject}")
    elif detail:
        parts.append(detail)
    else:
        parts.append("Evento programado")

    # Cross-context (the magic)
    if person_context:
        # Max 3 context items to keep it clean
        ctx_items = person_context[:3]
        parts.append("Contexto: " + " · ".join(ctx_items))

    message = "\n".join(parts)

    return {
        "message": message,
        "time_text": value,
    }


def _create_auto_task(
    user_id: str,
    chat_id: int,
    fact: Dict,
    enriched: Dict,
) -> Optional[Dict]:
    """Create a scheduler task from an enriched fact."""
    try:
        from core.scheduler import add_task, parse_schedule

        # Build a scheduling instruction from the fact's temporal info
        time_text = enriched["time_text"]
        message = enriched["message"]

        # Get user timezone
        tz = "America/New_York"
        try:
            conn = sqlite3.connect(_USER_DB, timeout=2)
            row = conn.execute(
                "SELECT language FROM profiles WHERE user_id = ?", (user_id,),
            ).fetchone()
            conn.close()
            # Could also check for timezone field if it exists
        except Exception:
            pass

        # Parse the temporal expression
        schedule_text = f"recuérdame {time_text}"
        parsed = parse_schedule(schedule_text, tz=tz)
        if not parsed:
            return None

        # Override the message with our enriched version
        parsed["message"] = message
        parsed["action_type"] = "reminder"

        # Store directly in scheduler DB
        from core.scheduler import _conn as sched_conn
        db = sched_conn()
        db.execute(
            """INSERT INTO scheduled_tasks
               (user_id, chat_id, action_type, message, original_text,
                schedule_type, time_expr, days_of_week, next_run, timezone, enabled, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
            (
                user_id, chat_id, "reminder", message,
                fact.get("raw_input", ""),
                parsed["schedule_type"], parsed["time_expr"],
                parsed["days_of_week"], parsed["next_run"], tz, time.time(),
            ),
        )
        task_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.commit()
        db.close()

        return {
            "id": task_id,
            "message": message,
            "time_expr": parsed["time_expr"],
            "next_run": parsed["next_run"],
        }

    except Exception as exc:
        logger.error("_create_auto_task failed: %s", exc)
        return None
