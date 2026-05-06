"""
core/sovereignty/consent.py — Registro persistente de opt-ins por usuario.

Cada usuario controla qué tipos de mensajes recibe. Default: ningún
opt-in activo. Usuario debe hacer grant_consent() explícitamente para
recibir reminders, proactive, etc.

Backend: SQLite. Thread-safe.
Diseñado para millones de users (índice por user_id).
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import List, Optional

from core.sovereignty.policy import ConsentType


_DB_PATH = os.environ.get(
    "VECTRAX_SOVEREIGNTY_DB",
    os.path.join(
        os.path.expanduser("~"),
        ".vectrax", "sovereignty.db",
    ),
)

_CREATE = """
CREATE TABLE IF NOT EXISTS user_consent (
    user_id      TEXT NOT NULL,
    consent_type TEXT NOT NULL,
    granted_at   REAL NOT NULL,
    revoked_at   REAL,
    source       TEXT NOT NULL DEFAULT 'user',
    PRIMARY KEY (user_id, consent_type)
);
CREATE INDEX IF NOT EXISTS idx_consent_user ON user_consent(user_id);
"""

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    c = sqlite3.connect(_DB_PATH, timeout=3)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_CREATE)
    return c


def grant_consent(
    user_id: str,
    consent_type: ConsentType,
    source: str = "user",
) -> bool:
    """Activa un opt-in para el user.

    Args:
      user_id: identificador del user (ej: 'tg:2030762343').
      consent_type: ConsentType a otorgar.
      source: 'user' (el usuario lo activó) | 'admin' | 'default'.

    Returns:
      True si se grabó. False si falló silenciosamente.
    """
    if not user_id or not consent_type:
        return False
    try:
        with _lock:
            c = _conn()
            c.execute(
                "INSERT INTO user_consent (user_id, consent_type, granted_at, revoked_at, source) "
                "VALUES (?, ?, ?, NULL, ?) "
                "ON CONFLICT(user_id, consent_type) DO UPDATE SET "
                "  granted_at=excluded.granted_at, revoked_at=NULL, source=excluded.source",
                (user_id, consent_type.value, time.time(), source),
            )
            c.commit()
            c.close()
        return True
    except Exception:
        return False


def revoke_consent(user_id: str, consent_type: ConsentType) -> bool:
    """Desactiva un opt-in. Devuelve True si afectó alguna fila."""
    if not user_id or not consent_type:
        return False
    try:
        with _lock:
            c = _conn()
            cur = c.execute(
                "UPDATE user_consent SET revoked_at = ? "
                "WHERE user_id = ? AND consent_type = ? AND revoked_at IS NULL",
                (time.time(), user_id, consent_type.value),
            )
            c.commit()
            affected = cur.rowcount
            c.close()
        return affected > 0
    except Exception:
        return False


def has_consent(user_id: str, consent_type: ConsentType) -> bool:
    """True si el user tiene este consent activo (granted, no revoked)."""
    if not user_id or not consent_type:
        return False
    try:
        with _lock:
            c = _conn()
            row = c.execute(
                "SELECT granted_at, revoked_at FROM user_consent "
                "WHERE user_id = ? AND consent_type = ?",
                (user_id, consent_type.value),
            ).fetchone()
            c.close()
        if row is None:
            return False
        granted_at, revoked_at = row
        return granted_at is not None and revoked_at is None
    except Exception:
        return False


def list_consents(user_id: str) -> List[ConsentType]:
    """Devuelve la lista de ConsentType activos para el user."""
    if not user_id:
        return []
    try:
        with _lock:
            c = _conn()
            rows = c.execute(
                "SELECT consent_type FROM user_consent "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (user_id,),
            ).fetchall()
            c.close()
        out = []
        for (ct,) in rows:
            try:
                out.append(ConsentType(ct))
            except ValueError:
                continue
        return out
    except Exception:
        return []


def reset_for_tests() -> None:
    """Test-only: clear the DB."""
    try:
        with _lock:
            c = _conn()
            c.execute("DELETE FROM user_consent")
            c.commit()
            c.close()
    except Exception:
        pass
