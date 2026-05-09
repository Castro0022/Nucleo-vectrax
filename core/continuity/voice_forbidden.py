"""
core/continuity/voice_forbidden.py — Persistencia del flag
VOICE_MESSAGES_FORBIDDEN por chat.

Antes este flag estaba in-memory en `core/voice/telegram_dispatch.py`
(`_voice_forbidden_chats`). Cada restart re-intentaba sendVoice y
perdía un round-trip a Telegram (400) hasta volver a cachear.

Este módulo persiste el flag en `continuity.db::vctx_voice_forbidden`,
de modo que tras restart Baytracks ya sabe qué chats no aceptan voice
y va directo a sendAudio (legacy mode).

Tabla:
    vctx_voice_forbidden(chat_id INTEGER PRIMARY KEY, marked_at REAL)

API:
    mark_forbidden(chat_id) -> bool
    is_forbidden(chat_id) -> bool
    unmark(chat_id) -> bool   (ej. user volvió a permitir voice)
    list_forbidden() -> list[int]
    reset_for_tests()

Backend: SQLite WAL en `~/.vectrax/continuity.db`. Lock global para
escrituras. Lecturas baratas por PK.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from typing import List

logger = logging.getLogger("vectrax.continuity.voice_forbidden")


def _db_path() -> str:
    return os.environ.get(
        "VECTRAX_CONTINUITY_DB",
        os.path.join(
            os.path.expanduser("~"),
            ".vectrax", "continuity.db",
        ),
    )

_lock = threading.Lock()
_in_memory_cache: set = set()   # warm cache, populated en init
_cache_loaded = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vctx_voice_forbidden (
    chat_id    INTEGER PRIMARY KEY,
    marked_at  REAL NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    p = _db_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    c = sqlite3.connect(p, timeout=3, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    return c


def _ensure_loaded() -> None:
    """Carga el set en memoria desde la DB (una sola vez)."""
    global _cache_loaded
    if _cache_loaded:
        return
    try:
        with _lock:
            if _cache_loaded:
                return
            c = _conn()
            try:
                rows = c.execute(
                    "SELECT chat_id FROM vctx_voice_forbidden",
                ).fetchall()
                for (cid,) in rows:
                    _in_memory_cache.add(int(cid))
                _cache_loaded = True
                if rows:
                    logger.info(
                        "voice_forbidden: cargados %d chats desde DB",
                        len(rows),
                    )
            finally:
                c.close()
    except Exception as exc:
        logger.debug("voice_forbidden load failed: %s", exc)
        _cache_loaded = True  # evitar reintento infinito


def is_forbidden(chat_id: int) -> bool:
    """True si este chat no acepta voice messages."""
    if not chat_id:
        return False
    _ensure_loaded()
    return int(chat_id) in _in_memory_cache


def mark_forbidden(chat_id: int) -> bool:
    """Marca el chat como voice_forbidden. Idempotente."""
    if not chat_id:
        return False
    _ensure_loaded()
    cid = int(chat_id)
    if cid in _in_memory_cache:
        return True
    try:
        with _lock:
            c = _conn()
            try:
                c.execute(
                    "INSERT OR IGNORE INTO vctx_voice_forbidden "
                    "(chat_id, marked_at) VALUES (?, ?)",
                    (cid, time.time()),
                )
                c.commit()
            finally:
                c.close()
            _in_memory_cache.add(cid)
        logger.info("voice_forbidden: chat %s marcado (persistente)", cid)
        return True
    except Exception as exc:
        logger.debug("mark_forbidden failed: %s", exc)
        return False


def unmark(chat_id: int) -> bool:
    """Quita el chat del set (ej. user permite voice de nuevo).

    Devuelve True si afectó alguna fila.
    """
    if not chat_id:
        return False
    _ensure_loaded()
    cid = int(chat_id)
    try:
        with _lock:
            c = _conn()
            try:
                cur = c.execute(
                    "DELETE FROM vctx_voice_forbidden WHERE chat_id = ?",
                    (cid,),
                )
                c.commit()
                affected = cur.rowcount
            finally:
                c.close()
            _in_memory_cache.discard(cid)
        if affected:
            logger.info("voice_forbidden: chat %s removido", cid)
        return affected > 0
    except Exception as exc:
        logger.debug("unmark failed: %s", exc)
        return False


def list_forbidden() -> List[int]:
    """Devuelve la lista de chats marcados como voice_forbidden."""
    _ensure_loaded()
    return sorted(_in_memory_cache)


def reset_for_tests() -> None:
    """Test-only: clear DB + in-memory cache."""
    global _cache_loaded
    try:
        with _lock:
            c = _conn()
            try:
                c.execute("DELETE FROM vctx_voice_forbidden")
                c.commit()
            finally:
                c.close()
            _in_memory_cache.clear()
            _cache_loaded = False
    except Exception:
        pass
