"""
Vectrax Audit Ledger
======================
Append-only SQLite ledger for auditing all significant actions.
Stores: timestamp, actor, role, action, diff_hash, decision, reason.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional


VAULT_DIR = os.environ.get(
    "VECTRAX_VAULT_DIR",
    os.path.join(os.path.expanduser("~"), "Vectrax", "vault"),
)
LEDGER_PATH = os.path.join(VAULT_DIR, "audit_ledger.db")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS audit_ledger (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT 'system',
    role        TEXT NOT NULL DEFAULT 'owner',
    action      TEXT NOT NULL,
    diff_hash   TEXT DEFAULT '',
    decision    TEXT NOT NULL DEFAULT 'pending',
    reason      TEXT DEFAULT '',
    metadata    TEXT DEFAULT '{}'
);
"""


def _get_conn() -> sqlite3.Connection:
    os.makedirs(VAULT_DIR, exist_ok=True)
    conn = sqlite3.connect(LEDGER_PATH)
    conn.execute(_CREATE_TABLE)
    conn.commit()
    return conn


def compute_diff_hash(diff_text: str) -> str:
    """SHA-256 hash of a diff for integrity verification."""
    return hashlib.sha256(diff_text.encode("utf-8")).hexdigest()[:16]


def record(
    action: str,
    *,
    actor: str = "system",
    role: str = "owner",
    diff_hash: str = "",
    decision: str = "approved",
    reason: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Append an entry to the audit ledger.
    Returns the row ID.
    """
    import json

    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO audit_ledger (timestamp, actor, role, action, diff_hash, decision, reason, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.utcnow().isoformat() + "Z",
                actor,
                role,
                action,
                diff_hash,
                decision,
                reason,
                json.dumps(metadata or {}),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def query(
    limit: int = 100,
    action_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Query the audit ledger.
    Returns list of dicts (most recent first).
    """
    conn = _get_conn()
    try:
        if action_filter:
            rows = conn.execute(
                "SELECT * FROM audit_ledger WHERE action = ? ORDER BY id DESC LIMIT ?",
                (action_filter, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_ledger ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

        cols = ["id", "timestamp", "actor", "role", "action", "diff_hash", "decision", "reason", "metadata"]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def count() -> int:
    """Return total entries in the ledger."""
    conn = _get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM audit_ledger").fetchone()[0]
    finally:
        conn.close()
