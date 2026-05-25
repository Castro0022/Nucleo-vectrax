"""
core/continuity_reentry.py — Continuidad proactiva por silencio.

Reglas:
  1. Vectrax puede enviar UNA sola reentrada contextual después de 12-20h de silencio.
  2. El tiempo es aleatorio (random entre 12-20h) para parecer natural.
  3. Si el usuario vuelve antes → cancelar la reentrada.
  4. Si el usuario no responde a la reentrada → no insistir hasta nueva actividad.
  5. Solo el creador puede activar/desactivar este sistema.

Flujo:
  - Cada tick del worker (cada 10 min), check_reentry() revisa usuarios silenciosos.
  - Para cada usuario: si silencio >= reentry_after_hours Y no se envió ya → enviar.
  - record_activity() se llama cuando el usuario envía un mensaje → cancela reentrada pendiente.

Persistencia: SQLite en vault/continuity_reentry.db

Creado: 2026-05-25
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import random
import sqlite3
import time
from typing import Callable, Optional

logger = logging.getLogger("vectrax.continuity_reentry")

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "continuity_reentry.db",
)

_CREATE = """
CREATE TABLE IF NOT EXISTS reentry_state (
    user_id             TEXT PRIMARY KEY,
    last_activity       REAL NOT NULL DEFAULT 0,
    reentry_after_hours REAL NOT NULL DEFAULT 0,
    reentry_sent        INTEGER NOT NULL DEFAULT 0,
    reentry_sent_at     REAL NOT NULL DEFAULT 0
);
"""

# Global enable flag — controlled by creator only
_ENABLED = os.environ.get("CONTINUITY_REENTRY_ENABLED", "1") == "1"

# Silence window (hours)
MIN_SILENCE_HOURS = 12
MAX_SILENCE_HOURS = 20


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=3)
    conn.executescript(_CREATE)
    return conn


# ---------------------------------------------------------------------------
# Activity tracking — called from telegram_gateway on EVERY user message
# ---------------------------------------------------------------------------

def record_activity(user_id: str) -> None:
    """Record that a user just sent a message.
    
    This resets the reentry timer:
    - Updates last_activity to now
    - Assigns a new random reentry delay (12-20h)
    - Clears reentry_sent flag so the next silence cycle can trigger
    """
    now = time.time()
    delay = random.uniform(MIN_SILENCE_HOURS, MAX_SILENCE_HOURS)
    try:
        conn = _conn()
        conn.execute(
            """INSERT INTO reentry_state (user_id, last_activity, reentry_after_hours, reentry_sent)
               VALUES (?, ?, ?, 0)
               ON CONFLICT(user_id) DO UPDATE SET
               last_activity = ?, reentry_after_hours = ?, reentry_sent = 0""",
            (user_id, now, delay, now, delay),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("record_activity failed: %s", exc)


# ---------------------------------------------------------------------------
# Reentry scan — called from pipeline_worker every 10 min
# ---------------------------------------------------------------------------

def check_reentry(tg_send_fn: Callable[[int, str], bool]) -> int:
    """Scan all users and send ONE reentry message if silence threshold exceeded.
    
    Returns number of messages sent.
    """
    if not _ENABLED:
        return 0

    sent = 0
    now = time.time()

    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT user_id, last_activity, reentry_after_hours, reentry_sent "
            "FROM reentry_state WHERE reentry_sent = 0 AND last_activity > 0"
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.debug("check_reentry scan failed: %s", exc)
        return 0

    for user_id, last_activity, reentry_hours, reentry_sent in rows:
        try:
            # Skip non-telegram users
            if not user_id.startswith("tg:"):
                continue

            # Skip creator
            creator_uid = os.environ.get("VX_CREATOR_ID", "2030762343")
            if user_id.replace("tg:", "") == creator_uid:
                continue

            # Check silence duration
            silence_hours = (now - last_activity) / 3600
            if silence_hours < reentry_hours:
                continue

            # Build contextual reentry message
            chat_id = int(user_id.replace("tg:", ""))
            message = _build_reentry_message(user_id)
            if not message:
                continue

            # Send
            ok = tg_send_fn(chat_id, message)
            if ok:
                # Mark as sent — will NOT send again until user returns
                try:
                    conn2 = _conn()
                    conn2.execute(
                        "UPDATE reentry_state SET reentry_sent = 1, reentry_sent_at = ? "
                        "WHERE user_id = ?",
                        (now, user_id),
                    )
                    conn2.commit()
                    conn2.close()
                except Exception:
                    pass
                sent += 1
                logger.info(
                    "Reentry sent | user=%s | silence=%.1fh | threshold=%.1fh",
                    user_id[:20], silence_hours, reentry_hours,
                )

        except Exception as exc:
            logger.debug("check_reentry user %s failed: %s", user_id[:20], exc)

    return sent


def _build_reentry_message(user_id: str) -> Optional[str]:
    """Build a contextual reentry message based on user's last activity."""
    # Get user name
    name = ""
    try:
        _user_db = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "vault", "user_memory.db",
        )
        conn = sqlite3.connect(_user_db, timeout=2)
        row = conn.execute(
            "SELECT name FROM profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
        conn.close()
        if row and row[0]:
            name = row[0].split()[0]
    except Exception:
        pass

    # Check for pending leads (most actionable context)
    try:
        from core.lead_tracker import get_silent_leads
        silent = get_silent_leads(user_id)
        if silent:
            lead = silent[0]
            days = int(lead["days_silent"])
            if name:
                return f"{name}, {lead['name']} lleva {days} dias sin respuesta. Quieres que prepare un mensaje de seguimiento?"
            return f"{lead['name']} lleva {days} dias sin respuesta. Quieres que prepare un mensaje de seguimiento?"
    except Exception:
        pass

    # Generic contextual reentry
    if name:
        return f"{name}, hace rato no hablamos. Algo en lo que pueda ayudarte hoy?"
    return "Hace rato no hablamos. Algo en lo que pueda ayudarte hoy?"


# ---------------------------------------------------------------------------
# Admin controls (creator only)
# ---------------------------------------------------------------------------

def set_enabled(enabled: bool) -> None:
    """Enable/disable the reentry system. Creator only."""
    global _ENABLED
    _ENABLED = enabled
    logger.info("Continuity reentry: %s", "ENABLED" if enabled else "DISABLED")


def is_enabled() -> bool:
    return _ENABLED


def get_stats() -> dict:
    """Return reentry system stats."""
    try:
        conn = _conn()
        total = conn.execute("SELECT COUNT(*) FROM reentry_state").fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM reentry_state WHERE reentry_sent = 0 AND last_activity > 0"
        ).fetchone()[0]
        sent = conn.execute(
            "SELECT COUNT(*) FROM reentry_state WHERE reentry_sent = 1"
        ).fetchone()[0]
        conn.close()
        return {
            "enabled": _ENABLED,
            "total_users": total,
            "pending": pending,
            "sent": sent,
            "min_hours": MIN_SILENCE_HOURS,
            "max_hours": MAX_SILENCE_HOURS,
        }
    except Exception:
        return {"enabled": _ENABLED, "error": "db_unavailable"}
