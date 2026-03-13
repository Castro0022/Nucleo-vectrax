"""
Vectrax UCE — SQLite Database Connector.

Read and write to any local SQLite database file.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from connectors.base import ConnectorSpec
from connectors.contracts.families import ConnectorFamily
from connectors.contracts.schema import ConnectorContract
from connectors.core.interface import UniversalConnector
from connectors.core.types import AuthMethod, ConnectorCapability, ConnectorStatus

SQLITE_CONTRACT = ConnectorContract(
    name="sqlite_db",
    family=ConnectorFamily.DATABASE,
    version="0.1.0",
    capabilities=[
        ConnectorCapability.AUTHENTICATE,
        ConnectorCapability.READ,
        ConnectorCapability.WRITE,
    ],
    required_permissions=["read", "write"],
    auth_method=AuthMethod.NONE,
    input_schema={
        "read": {"query": "str (SQL SELECT)", "params": "list (optional)"},
        "write": {"query": "str (SQL INSERT/UPDATE/DELETE)", "params": "list (optional)"},
    },
    output_schema={"rows": "list[dict]", "affected": "int"},
    risks=["Database modification", "SQL injection if params not used"],
    description="SQLite connector — read/write to any local .db file.",
    author="vectrax",
)


class SqliteConnector(UniversalConnector):
    """Connector for SQLite databases."""

    def __init__(self, db_path: str | None = None) -> None:
        spec = ConnectorSpec(
            name="sqlite_db",
            scopes=["db.read", "db.write"],
            permissions=["read", "write"],
            version="0.1.0",
            description=SQLITE_CONTRACT.description,
            author="vectrax",
        )
        super().__init__(spec)
        self._db_path: Optional[Path] = Path(db_path) if db_path else None

    def authenticate(self, credentials: Dict[str, Any] | None = None) -> bool:
        if credentials and "db_path" in credentials:
            self._db_path = Path(credentials["db_path"])
        if self._db_path is None or not self._db_path.exists():
            self._set_status(ConnectorStatus.ERROR, "Database file not found")
            return False
        self._set_status(ConnectorStatus.CONNECTED)
        return True

    def read(self, query: Dict[str, Any]) -> Dict[str, Any]:
        sql = query.get("query", "")
        params = query.get("params", [])
        if not sql:
            return {"error": "No query provided", "success": False}

        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
            result = [dict(r) for r in rows]
            conn.close()
            return {"rows": result, "count": len(result), "success": True}
        except Exception as exc:
            return {"error": str(exc), "success": False}

    def write(self, data: Dict[str, Any]) -> Dict[str, Any]:
        sql = data.get("query", "")
        params = data.get("params", [])
        if not sql:
            return {"error": "No query provided", "success": False}

        try:
            conn = sqlite3.connect(str(self._db_path))
            cursor = conn.execute(sql, params)
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return {"affected": affected, "success": True}
        except Exception as exc:
            return {"error": str(exc), "success": False}

    def healthcheck(self) -> bool:
        if not self._db_path or not self._db_path.exists():
            return False
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute("SELECT 1")
            conn.close()
            return True
        except Exception:
            return False

    def test(self) -> Dict[str, Any]:
        ok = self.healthcheck()
        return {
            "passed": ok,
            "details": f"SQLite {'connected' if ok else 'unreachable'}: {self._db_path}",
        }

    def list_resources(self) -> List[Dict[str, Any]]:
        if not self._db_path or not self._db_path.exists():
            return []
        try:
            conn = sqlite3.connect(str(self._db_path))
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            conn.close()
            return [
                {"id": f"table-{t[0]}", "name": t[0], "type": "table"}
                for t in tables
            ]
        except Exception:
            return []
