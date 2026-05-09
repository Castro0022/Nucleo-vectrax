"""
core/continuity/idempotency.py — SQLite-backed Telegram update_id ledger.

Aplica el schema_continuity.sql del paquete original (tabla
`vctx_processed_updates`) y expone una API thread-safe para idempotencia.

Flujo en el gateway:

    for u in updates:
        uid = u["update_id"]
        if is_processed(uid):
            self._offset = uid + 1
            continue                       # ya procesado, ignorar
        mark_processed(uid, chat_id=...)
        self._offset = uid + 1
        self._pool.submit(self._handle, u)

Si el supervisor reinicia el gateway entre el `mark_processed` y el
`_save_offset`, la siguiente vez que arrancamos:
  - Telegram redelivera el update con su mismo update_id.
  - is_processed() devuelve True → skip.
  - Sin doble respuesta, sin doble audio.

Backend: SQLite en `~/.vectrax/continuity.db` (o env VECTRAX_CONTINUITY_DB).
Lock global para escrituras; lecturas concurrentes OK con WAL.

Pruning: prune_older_than(seconds) elimina entradas viejas para
evitar crecimiento infinito (default 7 días, configurable).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from typing import Optional

logger = logging.getLogger("vectrax.continuity.idempotency")


def _resolve_db_path() -> str:
    """Re-evalúa VECTRAX_CONTINUITY_DB en cada call. Permite a tests
    cambiar la ruta sin reiniciar el módulo."""
    return os.environ.get(
        "VECTRAX_CONTINUITY_DB",
        os.path.join(
            os.path.expanduser("~"),
            ".vectrax", "continuity.db",
        ),
    )


# Constante legacy: algunos imports leen este nombre. Apunta al valor
# del env al momento del import, pero los métodos usan _resolve_db_path()
# para no quedar pegados a esa snapshot.
_DEFAULT_DB_PATH = _resolve_db_path()

_DEFAULT_PRUNE_AFTER_DAYS = 7

# Schema replicado del archivo original schema_continuity.sql.
# Idempotente: todos los CREATE usan IF NOT EXISTS.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS vctx_identity (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS vctx_processed_updates (
    update_id   INTEGER PRIMARY KEY,
    received_at INTEGER NOT NULL,
    chat_id     TEXT
);

CREATE INDEX IF NOT EXISTS idx_vctx_processed_recv
    ON vctx_processed_updates(received_at);

CREATE INDEX IF NOT EXISTS idx_vctx_processed_chat
    ON vctx_processed_updates(chat_id, received_at DESC);

CREATE TABLE IF NOT EXISTS vctx_pending_actions (
    id          TEXT PRIMARY KEY,
    chat_id     TEXT NOT NULL,
    action_type TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    consumed_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_vctx_pending_chat
    ON vctx_pending_actions(chat_id, expires_at, consumed_at);

CREATE TABLE IF NOT EXISTS vctx_working_memory (
    chat_id    TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (chat_id, turn_index)
);

CREATE TABLE IF NOT EXISTS vctx_conversation_snapshots (
    id         TEXT PRIMARY KEY,
    chat_id    TEXT NOT NULL,
    summary    TEXT NOT NULL,
    keywords   TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vctx_snapshots_chat
    ON vctx_conversation_snapshots(chat_id, created_at);
"""


# ===========================================================================
# Ledger
# ===========================================================================

class SQLiteUpdateLedger:
    """Ledger persistente de update_ids procesados.

    Thread-safe (lock global). Lecturas concurrentes habilitadas por WAL.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or _resolve_db_path()
        self._lock = threading.Lock()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        c = sqlite3.connect(self.db_path, timeout=3,
                            check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def _init_schema(self) -> None:
        with self._lock:
            c = self._conn()
            try:
                c.executescript(_SCHEMA)
                c.commit()
            finally:
                c.close()

    # -------------------------------------------------------- queries
    def is_processed(self, update_id: int) -> bool:
        """True si update_id ya está en el ledger."""
        if not update_id:
            return False
        try:
            with self._lock:
                c = self._conn()
                try:
                    cur = c.execute(
                        "SELECT 1 FROM vctx_processed_updates "
                        "WHERE update_id = ?",
                        (int(update_id),),
                    )
                    return cur.fetchone() is not None
                finally:
                    c.close()
        except Exception as exc:
            logger.debug("is_processed failed: %s", exc)
            return False

    def mark_processed(
        self,
        update_id: int,
        chat_id: str = "",
    ) -> bool:
        """Inserta el update_id en el ledger. Idempotente (INSERT OR IGNORE).

        Devuelve True si la fila se creó/ya existía. False si error.
        """
        if not update_id:
            return False
        try:
            with self._lock:
                c = self._conn()
                try:
                    c.execute(
                        "INSERT OR IGNORE INTO vctx_processed_updates "
                        "(update_id, received_at, chat_id) "
                        "VALUES (?, ?, ?)",
                        (
                            int(update_id),
                            int(time.time()),
                            str(chat_id) if chat_id else None,
                        ),
                    )
                    c.commit()
                    return True
                finally:
                    c.close()
        except Exception as exc:
            logger.debug("mark_processed failed: %s", exc)
            return False

    def prune_older_than(self, seconds: Optional[int] = None) -> int:
        """Borra entradas viejas. Devuelve cuántas filas afectó."""
        secs = int(
            seconds if seconds is not None
            else _DEFAULT_PRUNE_AFTER_DAYS * 86400
        )
        cutoff = int(time.time()) - secs
        try:
            with self._lock:
                c = self._conn()
                try:
                    cur = c.execute(
                        "DELETE FROM vctx_processed_updates "
                        "WHERE received_at < ?",
                        (cutoff,),
                    )
                    c.commit()
                    return cur.rowcount
                finally:
                    c.close()
        except Exception as exc:
            logger.debug("prune failed: %s", exc)
            return 0

    def count(self) -> int:
        try:
            with self._lock:
                c = self._conn()
                try:
                    cur = c.execute(
                        "SELECT COUNT(*) FROM vctx_processed_updates",
                    )
                    row = cur.fetchone()
                    return int(row[0]) if row else 0
                finally:
                    c.close()
        except Exception:
            return 0

    def reset(self) -> None:
        """Test-only: clear all rows."""
        try:
            with self._lock:
                c = self._conn()
                try:
                    c.execute("DELETE FROM vctx_processed_updates")
                    c.commit()
                finally:
                    c.close()
        except Exception:
            pass


# ===========================================================================
# Singleton + funciones top-level (uso típico)
# ===========================================================================

_singleton_lock = threading.Lock()
_singleton: Optional[SQLiteUpdateLedger] = None


def _get_ledger() -> SQLiteUpdateLedger:
    global _singleton
    with _singleton_lock:
        current_path = _resolve_db_path()
        # Re-instantiar si el env var cambió (tests con tmpdirs distintos).
        if _singleton is None or _singleton.db_path != current_path:
            _singleton = SQLiteUpdateLedger(db_path=current_path)
        return _singleton


def is_processed(update_id: int) -> bool:
    return _get_ledger().is_processed(update_id)


def mark_processed(update_id: int, chat_id: str = "") -> bool:
    return _get_ledger().mark_processed(update_id, chat_id=chat_id)


def prune_older_than(seconds: Optional[int] = None) -> int:
    return _get_ledger().prune_older_than(seconds)


def reset_for_tests() -> None:
    """Test-only: re-resolve singleton + clear all rows."""
    global _singleton
    with _singleton_lock:
        _singleton = None
    _get_ledger().reset()
