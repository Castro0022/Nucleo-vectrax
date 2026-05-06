"""
Vectrax Message Queue — SQLite-based transport
=================================================
Cola de mensajes persistente entre el gateway (ligero) y los workers
(pesados). Elimina el acoplamiento directo que causa los bloqueos.

Flujo:
  1. Gateway recibe mensaje → enqueue()
  2. Worker lee mensaje → dequeue()
  3. Worker procesa → mark_done(id, response)
  4. Gateway lee respuesta → poll_response(id)
  5. Gateway envía respuesta al usuario

La cola usa SQLite WAL mode para permitir lecturas/escrituras
concurrentes entre procesos sin bloqueos.

Creado: 2026-03-28
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

_QUEUE_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "vault", "message_queue.db",
)

_CREATE = """
CREATE TABLE IF NOT EXISTS queue (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    chat_id     INTEGER NOT NULL,
    content     TEXT NOT NULL,
    channel     TEXT NOT NULL DEFAULT 'telegram',
    status      TEXT NOT NULL DEFAULT 'pending',
    response    TEXT DEFAULT '',
    created_at  REAL NOT NULL,
    started_at  REAL DEFAULT 0,
    done_at     REAL DEFAULT 0,
    error       TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status);
"""

# Max age before a "processing" message is considered stuck (seconds).
#
# CRITICAL: con valor 30s habia un bug grave (descubierto 2026-05-06):
#   - Voice + LLM + memoria + TTS toma legitimamente 30-60s.
#   - El dequeue() resetea cualquier msg processing > STUCK_TIMEOUT a
#     pending. Si Worker A toma 35s, Worker B lo retoma a los 30s y
#     ambos procesan el MISMO mensaje en paralelo. Cada uno llama
#     _tg_send + dispatch_audio_async => el usuario recibe 2 textos +
#     2 audios para un solo voice. Sintoma reportado: "habla del
#     anterior despues del audio ultimo".
#
# 180s es margen suficiente para vision + memoria + LLM + TTS (peor
# caso real observado: ~16s). Mantiene la recuperacion de workers
# que crashearon (msg queda en processing >180s sin avanzar => reset).
STUCK_TIMEOUT = 180
# Max age before a completed message is cleaned up (seconds)
CLEANUP_AGE = 300


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_QUEUE_DB), exist_ok=True)
    conn = sqlite3.connect(_QUEUE_DB, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.executescript(_CREATE)
    return conn


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class QueueMessage:
    id: str
    user_id: str
    chat_id: int
    content: str
    channel: str = "telegram"
    status: str = "pending"      # pending → processing → done | error
    response: str = ""
    created_at: float = 0.0
    started_at: float = 0.0
    done_at: float = 0.0
    error: str = ""


# ---------------------------------------------------------------------------
# Gateway side (write)
# ---------------------------------------------------------------------------

def enqueue(
    user_id: str,
    chat_id: int,
    content: str,
    channel: str = "telegram",
) -> str:
    """Add a message to the queue. Returns message ID."""
    msg_id = uuid.uuid4().hex[:12]
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO queue (id, user_id, chat_id, content, channel, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (msg_id, user_id, chat_id, content, channel, time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return msg_id


def poll_response(msg_id: str, timeout: float = 25.0) -> Optional[str]:
    """
    Wait for a response to appear for the given message.
    Polls SQLite every 0.3s. Returns response text or None on timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT status, response, error FROM queue WHERE id = ?",
                (msg_id,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None

        status, response, error = row
        if status == "done":
            return response or ""
        if status == "error":
            return f"Error: {error}" if error else "Error procesando mensaje."

        time.sleep(0.3)

    return None  # timeout


# ---------------------------------------------------------------------------
# Worker side (read + write)
# ---------------------------------------------------------------------------

def dequeue() -> Optional[QueueMessage]:
    """
    Atomically claim the oldest pending message.
    Returns QueueMessage or None if queue is empty.
    """
    conn = _conn()
    try:
        # Reset stuck messages first
        conn.execute(
            "UPDATE queue SET status = 'pending', started_at = 0 "
            "WHERE status = 'processing' AND started_at < ?",
            (time.time() - STUCK_TIMEOUT,),
        )

        row = conn.execute(
            "SELECT id, user_id, chat_id, content, channel, created_at "
            "FROM queue WHERE status = 'pending' "
            "ORDER BY created_at ASC LIMIT 1",
        ).fetchone()

        if row is None:
            conn.commit()
            return None

        msg_id = row[0]
        now = time.time()

        conn.execute(
            "UPDATE queue SET status = 'processing', started_at = ? WHERE id = ?",
            (now, msg_id),
        )
        conn.commit()

        return QueueMessage(
            id=msg_id,
            user_id=row[1],
            chat_id=row[2],
            content=row[3],
            channel=row[4],
            status="processing",
            created_at=row[5],
            started_at=now,
        )
    finally:
        conn.close()


def mark_done(msg_id: str, response: str) -> None:
    """Mark a message as done with its response."""
    conn = _conn()
    try:
        conn.execute(
            "UPDATE queue SET status = 'done', response = ?, done_at = ? WHERE id = ?",
            (response, time.time(), msg_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_error(msg_id: str, error: str) -> None:
    """Mark a message as failed."""
    conn = _conn()
    try:
        conn.execute(
            "UPDATE queue SET status = 'error', error = ?, done_at = ? WHERE id = ?",
            (error[:500], time.time(), msg_id),
        )
        conn.commit()
    finally:
        conn.close()


def cleanup() -> int:
    """Remove old completed/error messages. Returns count removed."""
    conn = _conn()
    try:
        cutoff = time.time() - CLEANUP_AGE
        cur = conn.execute(
            "DELETE FROM queue WHERE status IN ('done', 'error') AND done_at < ?",
            (cutoff,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def queue_stats() -> Dict[str, int]:
    """Queue statistics."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM queue GROUP BY status",
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    finally:
        conn.close()
