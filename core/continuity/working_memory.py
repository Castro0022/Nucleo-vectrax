"""
core/continuity/working_memory.py — Working memory por chat.

Reemplaza/complementa `vectrax.user_memory.interactions` para guardar
los últimos N turnos por chat (working memory inmediata) que el LLM
puede consumir como contexto reciente.

Tabla (definida en schema_continuity.sql):
    vctx_working_memory(
        chat_id    TEXT NOT NULL,
        turn_index INTEGER NOT NULL,
        role       TEXT NOT NULL ('user' | 'vectrax'),
        content    TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY (chat_id, turn_index)
    )

API pública:
    record_turn(chat_id, role, content) -> int (turn_index)
    get_recent_turns(chat_id, limit=10) -> list[dict]
    clear_for_chat(chat_id) -> int
    reset_for_tests()

Co-existe con `interactions` (tabla legacy en vectrax.db). No migra
nada — los callers nuevos pueden usar working_memory; los antiguos
siguen escribiendo a interactions sin conflicto.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List

logger = logging.getLogger("vectrax.continuity.working_memory")


def _db_path() -> str:
    return os.environ.get(
        "VECTRAX_CONTINUITY_DB",
        os.path.join(
            os.path.expanduser("~"),
            ".vectrax", "continuity.db",
        ),
    )

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    p = _db_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    c = sqlite3.connect(p, timeout=3, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    # Schema ya creado por idempotency.py al cargar el ledger; defensivo
    # crear si no existe.
    c.execute("""
        CREATE TABLE IF NOT EXISTS vctx_working_memory (
            chat_id    TEXT NOT NULL,
            turn_index INTEGER NOT NULL,
            role       TEXT NOT NULL,
            content    TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (chat_id, turn_index)
        )
    """)
    return c


def record_turn(chat_id: str, role: str, content: str) -> int:
    """Graba un turno (user o vectrax) en la working memory.

    `turn_index` se asigna automáticamente: max(turn_index) + 1 para
    este chat, o 0 si es el primer turno.

    Devuelve el turn_index asignado, o -1 si falló.
    """
    if not chat_id or not role or not content:
        return -1
    role = role.strip().lower()
    if role not in ("user", "vectrax", "system"):
        # Aceptamos solo roles conocidos para no mezclar basura.
        role = "user"
    try:
        with _lock:
            c = _conn()
            try:
                row = c.execute(
                    "SELECT COALESCE(MAX(turn_index), -1) "
                    "FROM vctx_working_memory WHERE chat_id = ?",
                    (str(chat_id),),
                ).fetchone()
                next_idx = int(row[0]) + 1 if row else 0
                c.execute(
                    "INSERT INTO vctx_working_memory "
                    "(chat_id, turn_index, role, content, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        str(chat_id), next_idx, role,
                        str(content)[:8192], int(time.time()),
                    ),
                )
                c.commit()
                return next_idx
            finally:
                c.close()
    except Exception as exc:
        logger.debug("record_turn failed: %s", exc)
        return -1


def get_recent_turns(
    chat_id: str, limit: int = 10,
) -> List[Dict[str, Any]]:
    """Devuelve los últimos `limit` turnos del chat, más reciente último.

    Cada dict: {turn_index, role, content, created_at}.
    """
    if not chat_id:
        return []
    try:
        with _lock:
            c = _conn()
            try:
                rows = c.execute(
                    "SELECT turn_index, role, content, created_at "
                    "FROM vctx_working_memory "
                    "WHERE chat_id = ? "
                    "ORDER BY turn_index DESC LIMIT ?",
                    (str(chat_id), int(limit)),
                ).fetchall()
            finally:
                c.close()
        # rows están en DESC; revertimos para entregar en orden cronológico.
        out = []
        for r in reversed(rows):
            out.append({
                "turn_index": int(r[0]),
                "role": r[1],
                "content": r[2],
                "created_at": int(r[3]),
            })
        return out
    except Exception as exc:
        logger.debug("get_recent_turns failed: %s", exc)
        return []


def clear_for_chat(chat_id: str) -> int:
    """Borra todos los turnos de un chat. Devuelve filas afectadas."""
    if not chat_id:
        return 0
    try:
        with _lock:
            c = _conn()
            try:
                cur = c.execute(
                    "DELETE FROM vctx_working_memory WHERE chat_id = ?",
                    (str(chat_id),),
                )
                c.commit()
                return cur.rowcount
            finally:
                c.close()
    except Exception:
        return 0


def reset_for_tests() -> None:
    """Test-only: limpia toda la tabla."""
    try:
        with _lock:
            c = _conn()
            try:
                c.execute("DELETE FROM vctx_working_memory")
                c.commit()
            finally:
                c.close()
    except Exception:
        pass


def format_for_prompt(chat_id: str, limit: int = 6) -> str:
    """Devuelve los últimos turnos como texto listo para inyectar.

    Ejemplo:
        [HILO RECIENTE]
        Mario: ¿cómo va el deploy?
        Vectrax: container Up healthy, sin errores.
        Mario: dale.
    """
    turns = get_recent_turns(chat_id, limit=limit)
    if not turns:
        return ""
    lines = ["[HILO RECIENTE]"]
    for t in turns:
        role = "Mario" if t["role"] == "user" else "Vectrax"
        snippet = t["content"][:140].replace("\n", " ")
        lines.append(f"{role}: {snippet}")
    return "\n".join(lines)
