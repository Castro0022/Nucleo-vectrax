"""
connectors/cybersecurity/seen_ledger.py — Seen-ledger por cve_id (idempotencia + checkpoints).

Pieza NUEVA (no existía en real_estate). SQLite WAL, fuente de verdad por-CVE y
store de checkpoints del backfill/incremental. Garantiza que una CVE contribuye
UNA sola vez a masa/outcome y que re-ejecutar el backfill añade 0.

Path: `$VECTRAX_VAULT_DIR/cyber_seen.db` (override con `CYBER_SEEN_DB`).

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Optional, Tuple

_lock = threading.Lock()

_COLUMNS = (
    "cve_id", "published_ts", "in_kev", "kev_date_ts", "days_to_kev",
    "product_family", "vulnerability_class", "attack_vector", "severity_tier",
    "outcome", "timing", "content_hash", "state", "materialized_levels",
    "first_seen_ts", "last_update_ts",
)

_CREATE = """
CREATE TABLE IF NOT EXISTS cve (
    cve_id TEXT PRIMARY KEY,
    published_ts REAL DEFAULT 0,
    in_kev INTEGER DEFAULT 0,
    kev_date_ts REAL DEFAULT 0,
    days_to_kev INTEGER,
    product_family TEXT,
    vulnerability_class TEXT,
    attack_vector TEXT,
    severity_tier TEXT,
    outcome TEXT,
    timing TEXT,
    content_hash TEXT,
    state TEXT,
    materialized_levels TEXT DEFAULT '',
    first_seen_ts REAL,
    last_update_ts REAL
);
CREATE TABLE IF NOT EXISTS checkpoint (key TEXT PRIMARY KEY, value TEXT);
"""

_HASH_FIELDS = (
    "in_kev", "kev_date_ts", "days_to_kev", "product_family",
    "vulnerability_class", "attack_vector", "severity_tier", "outcome", "timing",
)


def _vault_dir() -> str:
    return os.environ.get(
        "VECTRAX_VAULT_DIR",
        os.path.join(os.path.expanduser("~"), "Vectrax", "vault"),
    )


def _db_path() -> str:
    return os.environ.get("CYBER_SEEN_DB") or os.path.join(_vault_dir(), "cyber_seen.db")


def _conn() -> sqlite3.Connection:
    path = _db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_CREATE)
    return conn


def content_hash(rec: Dict[str, Any]) -> str:
    payload = {k: rec.get(k) for k in _HASH_FIELDS}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def upsert(rec: Dict[str, Any]) -> Tuple[bool, bool]:
    """Inserta/actualiza una CVE. Devuelve (is_new, is_changed).
    Preserva first_seen_ts y materialized_levels en updates."""
    cve_id = str(rec.get("cve_id") or "").strip().upper()
    if not cve_id:
        raise ValueError("upsert requiere cve_id")
    h = rec.get("content_hash") or content_hash(rec)
    now = time.time()
    with _lock:
        conn = _conn()
        try:
            row = conn.execute("SELECT content_hash FROM cve WHERE cve_id=?", (cve_id,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO cve (cve_id,published_ts,in_kev,kev_date_ts,days_to_kev,"
                    "product_family,vulnerability_class,attack_vector,severity_tier,"
                    "outcome,timing,content_hash,state,materialized_levels,"
                    "first_seen_ts,last_update_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cve_id, float(rec.get("published_ts") or 0), int(bool(rec.get("in_kev"))),
                     float(rec.get("kev_date_ts") or 0), rec.get("days_to_kev"),
                     rec.get("product_family"), rec.get("vulnerability_class"),
                     rec.get("attack_vector"), rec.get("severity_tier"),
                     rec.get("outcome"), rec.get("timing"), h, rec.get("state"),
                     "", now, now),
                )
                conn.commit()
                return True, True
            changed = (row[0] != h)
            conn.execute(
                "UPDATE cve SET published_ts=?, in_kev=?, kev_date_ts=?, days_to_kev=?, "
                "product_family=?, vulnerability_class=?, attack_vector=?, severity_tier=?, "
                "outcome=?, timing=?, content_hash=?, state=?, last_update_ts=? WHERE cve_id=?",
                (float(rec.get("published_ts") or 0), int(bool(rec.get("in_kev"))),
                 float(rec.get("kev_date_ts") or 0), rec.get("days_to_kev"),
                 rec.get("product_family"), rec.get("vulnerability_class"),
                 rec.get("attack_vector"), rec.get("severity_tier"),
                 rec.get("outcome"), rec.get("timing"), h, rec.get("state"), now, cve_id),
            )
            conn.commit()
            return False, changed
        finally:
            conn.close()


def get(cve_id: str) -> Optional[Dict[str, Any]]:
    cve_id = str(cve_id or "").strip().upper()
    with _lock:
        conn = _conn()
        try:
            row = conn.execute(
                f"SELECT {','.join(_COLUMNS)} FROM cve WHERE cve_id=?", (cve_id,)
            ).fetchone()
        finally:
            conn.close()
    return dict(zip(_COLUMNS, row)) if row else None


def mark_materialized(cve_id: str, level: str) -> bool:
    """Marca (cve_id, level) como materializado. Devuelve True si es NUEVO (no
    estaba), para gatear el incremento de masa una sola vez."""
    cve_id = str(cve_id or "").strip().upper()
    now = time.time()
    with _lock:
        conn = _conn()
        try:
            row = conn.execute("SELECT materialized_levels FROM cve WHERE cve_id=?", (cve_id,)).fetchone()
            levels = (set((row[0] or "").split(",")) - {""}) if row else set()
            if level in levels:
                return False
            levels.add(level)
            joined = ",".join(sorted(levels))
            if row is None:
                conn.execute(
                    "INSERT INTO cve (cve_id, materialized_levels, first_seen_ts, last_update_ts) "
                    "VALUES (?,?,?,?)", (cve_id, joined, now, now),
                )
            else:
                conn.execute(
                    "UPDATE cve SET materialized_levels=?, last_update_ts=? WHERE cve_id=?",
                    (joined, now, cve_id),
                )
            conn.commit()
            return True
        finally:
            conn.close()


def is_materialized(cve_id: str, level: str) -> bool:
    rec = get(cve_id)
    if not rec:
        return False
    return level in (set((rec.get("materialized_levels") or "").split(",")) - {""})


def get_checkpoint(key: str, default: Optional[str] = None) -> Optional[str]:
    with _lock:
        conn = _conn()
        try:
            row = conn.execute("SELECT value FROM checkpoint WHERE key=?", (key,)).fetchone()
        finally:
            conn.close()
    return row[0] if row else default


def set_checkpoint(key: str, value: str) -> None:
    with _lock:
        conn = _conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO checkpoint (key,value) VALUES (?,?)",
                (key, str(value)),
            )
            conn.commit()
        finally:
            conn.close()


def counts() -> Dict[str, Any]:
    with _lock:
        conn = _conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM cve").fetchone()[0]
            by_outcome = dict(
                conn.execute("SELECT outcome, COUNT(*) FROM cve GROUP BY outcome").fetchall()
            )
            in_kev = conn.execute("SELECT COUNT(*) FROM cve WHERE in_kev=1").fetchone()[0]
        finally:
            conn.close()
    return {"total": total, "by_outcome": by_outcome, "in_kev": in_kev}


def clear() -> None:
    """Solo tests / reinicio controlado."""
    with _lock:
        conn = _conn()
        try:
            conn.execute("DELETE FROM cve")
            conn.execute("DELETE FROM checkpoint")
            conn.commit()
        finally:
            conn.close()
