"""
Vectrax UCE — Connection Tracker.

Logs every connector operation to the ``connection_logs`` table.
Stores only abstract operational metrics — never raw content, personal
data, or credentials (privacy-preserving by design).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.uce.learning.tracker")

_DB_DIR = Path.home() / ".vectrax"
_DB_PATH = _DB_DIR / "vectrax.db"


@dataclass
class ConnectionLog:
    """A single operation log entry."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    connector_name: str = ""
    operation: str = ""          # auth / read / write / observe / healthcheck
    status: str = ""             # success / failure / timeout
    latency_ms: float = 0.0
    error_detail: Optional[str] = None
    permissions_used: List[str] = field(default_factory=list)
    input_summary: str = ""      # abstract description, never raw data
    output_summary: str = ""     # abstract description, never raw data
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "connector_name": self.connector_name,
            "operation": self.operation,
            "status": self.status,
            "latency_ms": round(self.latency_ms, 2),
            "error_detail": self.error_detail,
            "permissions_used": self.permissions_used,
            "input_summary": self.input_summary,
            "output_summary": self.output_summary,
            "timestamp": self.timestamp,
        }


class ConnectionTracker:
    """
    Tracks connector operations for learning and pattern detection.

    Privacy rules:
      - Only abstract intent categories and outcomes are stored
      - No raw content, personal data, credentials, or file contents
      - All entries are statistical and non-reconstructable
    """

    def __init__(self) -> None:
        self._ensure_table()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log(self, entry: ConnectionLog) -> None:
        """Persist a log entry."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO connection_logs
                   (id, connector_name, operation, status, latency_ms,
                    error_detail, permissions_used, input_summary,
                    output_summary, timestamp)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    entry.id, entry.connector_name, entry.operation,
                    entry.status, entry.latency_ms, entry.error_detail,
                    json.dumps(entry.permissions_used),
                    entry.input_summary, entry.output_summary,
                    entry.timestamp,
                ),
            )

    def track_operation(
        self,
        connector_name: str,
        operation: str,
        success: bool,
        latency_ms: float,
        error: str | None = None,
        permissions: List[str] | None = None,
        input_hint: str = "",
        output_hint: str = "",
    ) -> ConnectionLog:
        """Convenience method: create and persist a log entry."""
        entry = ConnectionLog(
            connector_name=connector_name,
            operation=operation,
            status="success" if success else "failure",
            latency_ms=latency_ms,
            error_detail=error,
            permissions_used=permissions or [],
            input_summary=input_hint[:200],   # truncate to avoid storing data
            output_summary=output_hint[:200],
        )
        self.log(entry)
        return entry

    def get_logs(
        self,
        connector_name: str | None = None,
        operation: str | None = None,
        limit: int = 50,
    ) -> List[ConnectionLog]:
        """Retrieve recent logs with optional filters."""
        clauses, params = [], []
        if connector_name:
            clauses.append("connector_name=?")
            params.append(connector_name)
        if operation:
            clauses.append("operation=?")
            params.append(operation)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM connection_logs {where} "
                "ORDER BY timestamp DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._row_to_log(r) for r in rows]

    def get_stats(self, connector_name: str) -> Dict[str, Any]:
        """Return aggregate stats for a connector."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT
                     COUNT(*) as total,
                     SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as successes,
                     SUM(CASE WHEN status='failure' THEN 1 ELSE 0 END) as failures,
                     AVG(latency_ms) as avg_latency,
                     MAX(latency_ms) as max_latency
                   FROM connection_logs WHERE connector_name=?""",
                (connector_name,),
            ).fetchone()
        if not row or row["total"] == 0:
            return {"total": 0, "success_rate": 0.0, "avg_latency_ms": 0.0}
        return {
            "total": row["total"],
            "successes": row["successes"],
            "failures": row["failures"],
            "success_rate": round(row["successes"] / row["total"], 4),
            "avg_latency_ms": round(row["avg_latency"] or 0, 2),
            "max_latency_ms": round(row["max_latency"] or 0, 2),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _conn() -> sqlite3.Connection:
        _DB_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS connection_logs (
                    id TEXT PRIMARY KEY,
                    connector_name TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    latency_ms REAL,
                    error_detail TEXT,
                    permissions_used TEXT,
                    input_summary TEXT,
                    output_summary TEXT,
                    timestamp REAL NOT NULL
                )
            """)

    @staticmethod
    def _row_to_log(row: sqlite3.Row) -> ConnectionLog:
        return ConnectionLog(
            id=row["id"],
            connector_name=row["connector_name"],
            operation=row["operation"],
            status=row["status"],
            latency_ms=row["latency_ms"] or 0.0,
            error_detail=row["error_detail"],
            permissions_used=json.loads(row["permissions_used"] or "[]"),
            input_summary=row["input_summary"] or "",
            output_summary=row["output_summary"] or "",
            timestamp=row["timestamp"],
        )
