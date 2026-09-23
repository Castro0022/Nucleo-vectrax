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


#: Ruta de PRODUCCIÓN cuando no hay override. Es exactamente la que este
#: módulo usaba antes; no se cambia.
PRODUCTION_VAULT_DIR = os.path.join(os.path.expanduser("~"), "Vectrax", "vault")

LEDGER_FILENAME = "audit_ledger.db"


def vault_dir() -> str:
    """Directorio del vault, resuelto EN CADA LLAMADA.

    Antes esto era `VAULT_DIR = os.environ.get(...)` a nivel de módulo: se
    congelaba en el primer import y `VECTRAX_VAULT_DIR` dejaba de tener efecto
    después. En las pruebas eso significaba que TODAS escribían en el vault que
    estuviera activo cuando se importó el módulo por primera vez — el mismo
    defecto de ruta congelada que PR #124 corrigió en `learned_rules`.

    Importa más ahora que antes: las rutas constitucionales de este PR asientan
    aquí sus bloqueos e indisponibilidades.
    """
    return os.environ.get("VECTRAX_VAULT_DIR") or PRODUCTION_VAULT_DIR


def ledger_path(db_path: Optional[str] = None) -> str:
    """Ruta del ledger. `db_path` explícito gana (para pruebas)."""
    return db_path or os.path.join(vault_dir(), LEDGER_FILENAME)

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


def _get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Conexión de ESCRITURA: crea el directorio y la tabla si hacen falta."""
    path = ledger_path(db_path)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(_CREATE_TABLE)
    conn.commit()
    return conn


def _open_for_read(db_path: Optional[str] = None) -> Optional[sqlite3.Connection]:
    """Conexión de LECTURA. `None` si el ledger todavía no existe.

    Un lector nunca crea el almacén: consultar el ledger no puede tener como
    efecto secundario sembrar un `audit_ledger.db` vacío allí donde apunte
    `VECTRAX_VAULT_DIR` en ese instante.
    """
    path = ledger_path(db_path)
    if not os.path.exists(path):
        return None
    return _get_conn(path)


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
    db_path: Optional[str] = None,
) -> int:
    """
    Append an entry to the audit ledger.
    Returns the row ID.
    """
    import json

    conn = _get_conn(db_path)
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
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Query the audit ledger.
    Returns list of dicts (most recent first). Lista vacía si aún no existe:
    leer no crea el almacén.
    """
    conn = _open_for_read(db_path)
    if conn is None:
        return []
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


def count(db_path: Optional[str] = None) -> int:
    """Total de entradas. 0 si el ledger aún no existe — leer no lo crea."""
    conn = _open_for_read(db_path)
    if conn is None:
        return 0
    try:
        return conn.execute("SELECT COUNT(*) FROM audit_ledger").fetchone()[0]
    finally:
        conn.close()
