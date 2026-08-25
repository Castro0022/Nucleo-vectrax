"""
core/self_observation/observation_ledger.py — Memoria persistente de observaciones.

Vectrax observa su universo continuamente. Este módulo persiste cada
observación como un registro consultable: qué vio, cuándo, en qué dominio,
qué estrella fue afectada, y cuál fue la evidencia.

Tabla: autonomous_observations
  id          — autoincrement
  timestamp   — ISO 8601
  domain      — gravity | market | operator | convergence | health | user
  obs_type    — new_star | star_growth | convergence_detected | error_spike |
                worker_state | signal | market_shift | user_activity | ...
  star_id     — fingerprint o user_id de la estrella afectada (nullable)
  summary     — descripción breve legible
  evidence    — datos estructurados (JSON string)
  severity    — info | warning | critical

Retención: últimas 5000 observaciones (auto-prune).

API pública:
    init_ledger()
    record(domain, obs_type, summary, star_id=None, evidence=None, severity="info")
    get_recent(limit=10) -> list[dict]
    get_by_domain(domain, limit=20) -> list[dict]
    count() -> int
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.observation_ledger")

_MAX_ROWS = 5000

# DB path: use vault directory (persistent across restarts)
_VAULT = os.environ.get(
    "VECTRAX_VAULT_DIR",
    os.path.join(os.path.expanduser("~"), "Vectrax", "vault"),
)
_DB_PATH = os.path.join(_VAULT, "observation_ledger.db")


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    c = sqlite3.connect(_DB_PATH, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_ledger() -> None:
    """Create the observations table if it doesn't exist."""
    c = _conn()
    c.execute("""
        CREATE TABLE IF NOT EXISTS autonomous_observations (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT    NOT NULL,
            domain    TEXT    NOT NULL,
            obs_type  TEXT    NOT NULL,
            star_id   TEXT,
            summary   TEXT    NOT NULL,
            evidence  TEXT,
            severity  TEXT    NOT NULL DEFAULT 'info'
        )
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_obs_ts ON autonomous_observations(timestamp DESC)
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_obs_domain ON autonomous_observations(domain)
    """)
    c.commit()
    c.close()
    logger.debug("observation_ledger initialized at %s", _DB_PATH)


def _resolve_ts(timestamp: Optional[str]) -> str:
    """Return ``timestamp`` normalized to a UTC-aware ISO-8601 string, or
    the real wall-clock "now" if it is missing/unparsable.

    Mirrors the normalization done at Gravity's replay boundary
    (core.learn.gravity_engine._parse_iso_strict): naive input is assumed
    UTC, aware input is converted to UTC, invalid input never raises.
    """
    if not timestamp:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        dt = datetime.fromisoformat(timestamp)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat(timespec="seconds")


def record(
    domain: str,
    obs_type: str,
    summary: str,
    *,
    star_id: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
    severity: str = "info",
    timestamp: Optional[str] = None,
) -> int:
    """Record one autonomous observation. Returns the row id.

    ``timestamp`` (optional ISO-8601 string) lets a caller record an
    observation under a historical event time — e.g. domain_ingester
    replaying a past event via ``event_timestamp`` — instead of the real
    ingestion time. When omitted, behaviour is unchanged: the real
    wall-clock UTC "now" is used, exactly as before.
    """
    ts = _resolve_ts(timestamp)
    ev_json = json.dumps(evidence, ensure_ascii=False) if evidence else None
    try:
        c = _conn()
        cur = c.execute(
            "INSERT INTO autonomous_observations "
            "(timestamp, domain, obs_type, star_id, summary, evidence, severity) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, domain, obs_type, star_id, summary, ev_json, severity),
        )
        row_id = cur.lastrowid
        # Auto-prune old rows
        c.execute(
            "DELETE FROM autonomous_observations WHERE id NOT IN "
            "(SELECT id FROM autonomous_observations ORDER BY id DESC LIMIT ?)",
            (_MAX_ROWS,),
        )
        c.commit()
        c.close()
        return row_id
    except Exception as exc:
        logger.error("observation_ledger.record failed: %s", exc)
        return -1


def get_recent(limit: int = 10) -> List[Dict[str, Any]]:
    """Return the most recent observations."""
    try:
        c = _conn()
        rows = c.execute(
            "SELECT * FROM autonomous_observations ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        c.close()
        return [_row_to_dict(r) for r in rows]
    except Exception as exc:
        logger.error("observation_ledger.get_recent failed: %s", exc)
        return []


def get_by_domain(domain: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Return recent observations filtered by domain."""
    try:
        c = _conn()
        rows = c.execute(
            "SELECT * FROM autonomous_observations WHERE domain = ? "
            "ORDER BY id DESC LIMIT ?",
            (domain, limit),
        ).fetchall()
        c.close()
        return [_row_to_dict(r) for r in rows]
    except Exception as exc:
        logger.error("observation_ledger.get_by_domain failed: %s", exc)
        return []


def count() -> int:
    """Total observations recorded."""
    try:
        c = _conn()
        n = c.execute("SELECT COUNT(*) FROM autonomous_observations").fetchone()[0]
        c.close()
        return n
    except Exception:
        return 0


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    if d.get("evidence"):
        try:
            d["evidence"] = json.loads(d["evidence"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d
