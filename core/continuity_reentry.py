"""
core/continuity_reentry.py — Máquina de estados de presencia proactiva.

Reglas finales:
  - Máximo 3 presence_nudges por usuario sin respuesta.
  - Nudge #1: ventana aleatoria 12-20h desde first_seen/última actividad.
  - Nudge #2: 3-5 días después de nudge #1.
  - Nudge #3: varias semanas después de nudge #2, máximo 1 nudge por mes.
  - Después de nudge #3 sin respuesta → DORMANT indefinido.
  - DORMANT se reactiva solo con mensaje entrante del usuario.
  - Cualquier respuesta del usuario resetea completo el contador de nudges.
  - Los 3 nudges respetan la ventana horaria 09:00-21:00.

Persistencia: SQLite en vault/continuity_reentry.db
"""
from __future__ import annotations

import logging
import os
import random
import sqlite3
import time
from datetime import datetime
from typing import Callable

logger = logging.getLogger("vectrax.continuity_reentry")

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "continuity_reentry.db",
)

_ENABLED = os.environ.get("CONTINUITY_REENTRY_ENABLED", "1") == "1"

NUDGE1_MIN_HOURS = 12
NUDGE1_MAX_HOURS = 20
NUDGE2_MIN_DAYS = 3
NUDGE2_MAX_DAYS = 5
NUDGE3_MIN_DAYS = 21
NUDGE3_MAX_DAYS = 30
MAX_NUDGES = 3

SEND_HOUR_START = 9
SEND_HOUR_END = 21

STATUS_ACTIVE = "active"
STATUS_DORMANT = "dormant"

# Backward-compatible names used by stats or older imports.
MIN_SILENCE_HOURS = NUDGE1_MIN_HOURS
MAX_SILENCE_HOURS = NUDGE1_MAX_HOURS

_CREATE = """
CREATE TABLE IF NOT EXISTS reentry_state (
    user_id             TEXT PRIMARY KEY,
    first_seen          REAL NOT NULL DEFAULT 0,
    last_activity       REAL NOT NULL DEFAULT 0,
    nudge_count         INTEGER NOT NULL DEFAULT 0,
    last_nudge_at       REAL NOT NULL DEFAULT 0,
    next_nudge_after    REAL NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'active',
    reentry_after_hours REAL NOT NULL DEFAULT 0,
    reentry_sent        INTEGER NOT NULL DEFAULT 0,
    reentry_sent_at     REAL NOT NULL DEFAULT 0
);
"""

_MIGRATION_COLUMNS = {
    "first_seen": "REAL NOT NULL DEFAULT 0",
    "nudge_count": "INTEGER NOT NULL DEFAULT 0",
    "last_nudge_at": "REAL NOT NULL DEFAULT 0",
    "next_nudge_after": "REAL NOT NULL DEFAULT 0",
    "status": "TEXT NOT NULL DEFAULT 'active'",
}


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=3)
    conn.execute(_CREATE)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(reentry_state)")}
    for name, ddl in _MIGRATION_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE reentry_state ADD COLUMN {name} {ddl}")
    conn.commit()
    return conn


def _in_send_window(now: float | None = None) -> bool:
    hour = datetime.fromtimestamp(now or time.time()).hour
    return SEND_HOUR_START <= hour < SEND_HOUR_END


def _nudge_delay_seconds(nudge_number: int) -> float:
    if nudge_number == 1:
        return random.uniform(NUDGE1_MIN_HOURS, NUDGE1_MAX_HOURS) * 3600
    if nudge_number == 2:
        return random.uniform(NUDGE2_MIN_DAYS, NUDGE2_MAX_DAYS) * 86400
    return random.uniform(NUDGE3_MIN_DAYS, NUDGE3_MAX_DAYS) * 86400


def record_activity(user_id: str) -> None:
    """Registra un mensaje entrante del usuario y resetea el flujo de nudges."""
    if not user_id:
        return

    now = time.time()
    next_nudge_after = now + _nudge_delay_seconds(1)

    try:
        conn = _conn()
        row = conn.execute(
            "SELECT first_seen FROM reentry_state WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        first_seen = row[0] if row and row[0] else now
        conn.execute(
            """
            INSERT INTO reentry_state (
                user_id, first_seen, last_activity, nudge_count,
                last_nudge_at, next_nudge_after, status,
                reentry_after_hours, reentry_sent, reentry_sent_at
            )
            VALUES (?, ?, ?, 0, 0, ?, 'active', ?, 0, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                last_activity = excluded.last_activity,
                nudge_count = 0,
                last_nudge_at = 0,
                next_nudge_after = excluded.next_nudge_after,
                status = 'active',
                reentry_sent = 0,
                reentry_sent_at = 0
            """,
            (
                user_id,
                first_seen,
                now,
                next_nudge_after,
                (NUDGE1_MIN_HOURS + NUDGE1_MAX_HOURS) / 2,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("record_activity failed: %s", exc)


def check_reentry(tg_send_fn: Callable[[int, str], bool]) -> int:
    """Envía el próximo nudge si corresponde; nunca envía un nudge #4."""
    if not _ENABLED:
        return 0

    now = time.time()
    if not _in_send_window(now):
        return 0

    sent = 0
    try:
        conn = _conn()
        rows = conn.execute(
            """
            SELECT user_id, nudge_count
            FROM reentry_state
            WHERE status = 'active'
              AND next_nudge_after > 0
              AND next_nudge_after <= ?
            """,
            (now,),
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.debug("check_reentry scan failed: %s", exc)
        return 0

    for user_id, nudge_count in rows:
        try:
            if not user_id.startswith("tg:"):
                continue

            creator_uid = os.environ.get("VX_CREATOR_ID", "2030762343")
            if (
                user_id.replace("tg:", "") == creator_uid
                and os.environ.get("REENTRY_INCLUDE_CREATOR", "1") != "1"
            ):
                continue

            nudge_number = int(nudge_count) + 1
            if nudge_number > MAX_NUDGES:
                _mark_dormant(user_id, now)
                continue

            chat_id = int(user_id.replace("tg:", ""))
            message = _build_reentry_message(user_id, nudge_number)
            if not message:
                continue

            if tg_send_fn(chat_id, message):
                _advance_state_after_send(user_id, nudge_number, now)
                sent += 1
                logger.info("presence_nudge #%d sent | user=%s", nudge_number, user_id[:20])
        except Exception as exc:
            logger.debug("check_reentry user %s failed: %s", user_id[:20], exc)

    return sent


def _advance_state_after_send(user_id: str, nudge_number: int, now: float) -> None:
    try:
        conn = _conn()
        if nudge_number >= MAX_NUDGES:
            conn.execute(
                """
                UPDATE reentry_state
                SET nudge_count = 3,
                    last_nudge_at = ?,
                    next_nudge_after = 0,
                    status = 'dormant',
                    reentry_sent = 1,
                    reentry_sent_at = ?
                WHERE user_id = ?
                """,
                (now, now, user_id),
            )
        else:
            next_after = now + _nudge_delay_seconds(nudge_number + 1)
            conn.execute(
                """
                UPDATE reentry_state
                SET nudge_count = ?,
                    last_nudge_at = ?,
                    next_nudge_after = ?,
                    status = 'active',
                    reentry_sent = 1,
                    reentry_sent_at = ?
                WHERE user_id = ?
                """,
                (nudge_number, now, next_after, now, user_id),
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("_advance_state_after_send failed: %s", exc)


def _mark_dormant(user_id: str, now: float) -> None:
    try:
        conn = _conn()
        conn.execute(
            """
            UPDATE reentry_state
            SET nudge_count = 3,
                last_nudge_at = ?,
                next_nudge_after = 0,
                status = 'dormant',
                reentry_sent = 1,
                reentry_sent_at = ?
            WHERE user_id = ?
            """,
            (now, now, user_id),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("_mark_dormant failed: %s", exc)


_TONE_DIRECTIVES = {
    1: (
        "Tono: apertura y bienvenida, sin presión. El usuario acaba de unirse pero lleva "
        "horas sin escribir. Genera un primer contacto cálido y breve que invite a la "
        "conversación sin exigir respuesta. Sin frases genéricas como 'en qué puedo ayudarte'."
    ),
    2: (
        "Tono: continuidad. Hace varios días que no hay contacto. Recuérdale que sigues "
        "disponible. Ejemplo de espíritu (no de frase literal): "
        "'Hace un tiempo que no hablamos. Sigo disponible si quieres retomar algo.' "
        "Usa el contexto real de la última conversación si lo tienes."
    ),
    3: (
        "Tono: despedida suave, última presencia. Es el mensaje final antes del silencio "
        "indefinido. No pidas nada. Ejemplo de espíritu (no literal): "
        "'Sigo aquí, en silencio. Si algún día necesitas ordenar una idea o recordar algo, "
        "puedes escribirme.' Sin presión, sin llamada a la acción."
    ),
}


def _build_reentry_message(user_id: str, nudge_number: int = 1) -> str | None:
    """Genera un nudge de presencia vía LLM con tono específico por número de nudge."""
    user_db = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "vault", "user_memory.db",
    )

    name, lang, last_input, last_output = "", "es", "", ""
    try:
        conn = sqlite3.connect(user_db, timeout=2)
        row = conn.execute(
            "SELECT name, language FROM profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row:
            name = (row[0] or "").split()[0] if row[0] else ""
            lang = row[1] or "es"
        row2 = conn.execute(
            "SELECT user_input, bot_output FROM interactions "
            "WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if row2:
            last_input = (row2[0] or "")[:200]
            last_output = (row2[1] or "")[:200]
        conn.close()
    except Exception:
        pass

    tone = _TONE_DIRECTIVES.get(nudge_number, _TONE_DIRECTIVES[1])
    context_parts = []
    if name:
        context_parts.append(f"Nombre: {name}")
    if last_input:
        context_parts.append(f"Último mensaje del usuario: {last_input}")
    if last_output:
        context_parts.append(f"Última respuesta de Vectrax: {last_output}")

    context_block = "\n".join(context_parts) if context_parts else "(sin contexto previo)"

    prompt = (
        f"Eres Vectrax (nudge #{nudge_number} de 3). {tone} "
        f"Máximo 2 frases. Idioma: {lang}.\n\n"
        f"Contexto:\n{context_block}\n\n"
        "Mensaje de presencia:"
    )

    # Primary: intelligence bridge
    try:
        from vectrax.intelligence_bridge import is_ready, route_single
        if is_ready():
            result = route_single(prompt)
            if result.get("success") and result.get("content"):
                msg = result["content"].strip().strip('"').strip()
                if len(msg) > 5:
                    logger.info(
                        "reentry_nudge #%d LLM generated | user=%s | len=%d",
                        nudge_number, user_id[:20], len(msg),
                    )
                    return msg
    except Exception as exc:
        logger.debug("Nudge LLM (bridge) failed: %s", exc)

    # Fallback: OpenAI direct
    try:
        import httpx
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if api_key:
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 100,
                    "temperature": 0.8,
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            msg = resp.json()["choices"][0]["message"]["content"].strip().strip('"').strip()
            if len(msg) > 5:
                logger.info(
                    "reentry_nudge #%d OpenAI generated | user=%s | len=%d",
                    nudge_number, user_id[:20], len(msg),
                )
                return msg
    except Exception as exc:
        logger.debug("Nudge LLM (OpenAI) failed: %s", exc)

    logger.debug("reentry_nudge skipped for %s: no LLM available", user_id[:20])
    return None


def set_enabled(enabled: bool) -> None:
    global _ENABLED
    _ENABLED = enabled
    logger.info("Continuity reentry: %s", "ENABLED" if enabled else "DISABLED")


def is_enabled() -> bool:
    return _ENABLED


def get_stats() -> dict:
    try:
        conn = _conn()
        total = conn.execute("SELECT COUNT(*) FROM reentry_state").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM reentry_state WHERE status = 'active'"
        ).fetchone()[0]
        dormant = conn.execute(
            "SELECT COUNT(*) FROM reentry_state WHERE status = 'dormant'"
        ).fetchone()[0]
        by_nudge = {}
        for n in range(0, MAX_NUDGES + 1):
            by_nudge[str(n)] = conn.execute(
                "SELECT COUNT(*) FROM reentry_state WHERE nudge_count = ?",
                (n,),
            ).fetchone()[0]
        conn.close()
        return {
            "enabled": _ENABLED,
            "total_users": total,
            "active": active,
            "dormant": dormant,
            "by_nudge": by_nudge,
            "nudge1_window_hours": [NUDGE1_MIN_HOURS, NUDGE1_MAX_HOURS],
            "nudge2_window_days": [NUDGE2_MIN_DAYS, NUDGE2_MAX_DAYS],
            "nudge3_window_days": [NUDGE3_MIN_DAYS, NUDGE3_MAX_DAYS],
            "send_window_hours": [SEND_HOUR_START, SEND_HOUR_END],
        }
    except Exception:
        return {"enabled": _ENABLED, "error": "db_unavailable"}
