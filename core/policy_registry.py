"""
Vectrax Policy Registry
=========================
Versioned policy storage (SQLite). Controls ONLY operational parameters —
structural invariants live in invariants.py and cannot be overridden.

Tunable parameters:
  - confidence_threshold  (float, floor enforced by invariants)
  - escalation_threshold  (float, 0-1)
  - routing_weights       (dict)
  - fallback_preferences  (list)
  - provider_priority     (dict)

Every mutation creates an audit entry.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.invariants import MIN_CONFIDENCE_FLOOR


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

VAULT_DIR = os.environ.get("VECTRAX_VAULT_DIR", os.path.join(os.path.expanduser("~/Vectrax"), "vault"))
REGISTRY_PATH = os.path.join(VAULT_DIR, "policy_registry.db")

# ---------------------------------------------------------------------------
# Default tunable params
# ---------------------------------------------------------------------------

DEFAULT_PARAMS: Dict[str, Any] = {
    "confidence_threshold": 0.60,
    "escalation_threshold": 0.80,
    "routing_weights": {
        "capability": 0.40,
        "cost": 0.20,
        "latency": 0.20,
        "priority": 0.20,
    },
    "fallback_preferences": ["ollama"],
    "provider_priority": {},
}


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS policy_versions (
    id          TEXT PRIMARY KEY,
    version     INTEGER NOT NULL,
    params      TEXT NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'candidate',
    created_at  TEXT NOT NULL,
    promoted_at TEXT DEFAULT NULL,
    audit_id    INTEGER DEFAULT NULL
);
"""

# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class PolicyVersion:
    id: str
    version: int
    params: Dict[str, Any]
    status: str  # "active" | "candidate" | "retired" | "rolled_back"
    created_at: str
    promoted_at: Optional[str] = None
    audit_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "params": self.params,
            "status": self.status,
            "created_at": self.created_at,
            "promoted_at": self.promoted_at,
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class PolicyRegistry:
    """Versioned policy store backed by SQLite."""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or REGISTRY_PATH

    # -- connection ---------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.execute(_CREATE_TABLE)
        conn.commit()
        return conn

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _row_to_pv(row) -> PolicyVersion:
        return PolicyVersion(
            id=row[0],
            version=row[1],
            params=json.loads(row[2]),
            status=row[3],
            created_at=row[4],
            promoted_at=row[5],
            audit_id=row[6],
        )

    def _next_version(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT MAX(version) FROM policy_versions"
        ).fetchone()
        return (row[0] or 0) + 1

    # -- CRUD ---------------------------------------------------------------

    def create_version(
        self, params: Optional[Dict[str, Any]] = None
    ) -> PolicyVersion:
        """Create a new candidate policy version."""
        merged = dict(DEFAULT_PARAMS)
        if params:
            merged.update(params)

        # Enforce confidence floor
        if merged.get("confidence_threshold", 1.0) < MIN_CONFIDENCE_FLOOR:
            merged["confidence_threshold"] = MIN_CONFIDENCE_FLOOR

        conn = self._conn()
        try:
            ver = self._next_version(conn)
            pid = str(uuid.uuid4())[:8]
            now = datetime.utcnow().isoformat() + "Z"
            conn.execute(
                "INSERT INTO policy_versions (id, version, params, status, created_at) "
                "VALUES (?, ?, ?, 'candidate', ?)",
                (pid, ver, json.dumps(merged), now),
            )
            conn.commit()
            return PolicyVersion(
                id=pid, version=ver, params=merged,
                status="candidate", created_at=now,
            )
        finally:
            conn.close()

    def get_active(self) -> Optional[PolicyVersion]:
        """Return the currently active policy, or None."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM policy_versions WHERE status = 'active' "
                "ORDER BY version DESC LIMIT 1"
            ).fetchone()
            return self._row_to_pv(row) if row else None
        finally:
            conn.close()

    def get_version(self, policy_id: str) -> Optional[PolicyVersion]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM policy_versions WHERE id = ?", (policy_id,)
            ).fetchone()
            return self._row_to_pv(row) if row else None
        finally:
            conn.close()

    def history(self, limit: int = 20) -> List[PolicyVersion]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM policy_versions ORDER BY version DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row_to_pv(r) for r in rows]
        finally:
            conn.close()

    # -- promote / rollback -------------------------------------------------

    def promote(self, policy_id: str, role: str = "owner") -> PolicyVersion:
        """
        Promote a candidate to active.
        Retires the previous active version.
        Requires owner role.
        """
        from core.roles import Permission, has_permission
        if not has_permission(role, Permission.CONFIG_WRITE):
            raise PermissionError(
                f"Role '{role}' lacks permission to promote policies"
            )

        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM policy_versions WHERE id = ?", (policy_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"Policy {policy_id} not found")

            pv = self._row_to_pv(row)
            if pv.status not in ("candidate", "active"):
                raise ValueError(
                    f"Policy {policy_id} status '{pv.status}' cannot be promoted"
                )

            now = datetime.utcnow().isoformat() + "Z"

            # Retire current active
            conn.execute(
                "UPDATE policy_versions SET status = 'retired' "
                "WHERE status = 'active'"
            )

            # Promote candidate
            audit_id = self._audit("policy_promote", policy_id, role)
            conn.execute(
                "UPDATE policy_versions SET status = 'active', "
                "promoted_at = ?, audit_id = ? WHERE id = ?",
                (now, audit_id, policy_id),
            )
            conn.commit()

            pv.status = "active"
            pv.promoted_at = now
            pv.audit_id = audit_id
            return pv
        finally:
            conn.close()

    def rollback(self, policy_id: str, role: str = "owner") -> Optional[PolicyVersion]:
        """
        Roll back a promoted policy.  Marks it rolled_back and restores
        the most recent retired version to active.
        """
        from core.roles import Permission, has_permission
        if not has_permission(role, Permission.CONFIG_WRITE):
            raise PermissionError(
                f"Role '{role}' lacks permission to rollback policies"
            )

        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM policy_versions WHERE id = ?", (policy_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"Policy {policy_id} not found")

            pv = self._row_to_pv(row)
            if pv.status != "active":
                raise ValueError(
                    f"Policy {policy_id} is not active — cannot rollback"
                )

            # Mark rolled_back
            conn.execute(
                "UPDATE policy_versions SET status = 'rolled_back' WHERE id = ?",
                (policy_id,),
            )

            # Restore most recent retired
            prev = conn.execute(
                "SELECT * FROM policy_versions WHERE status = 'retired' "
                "ORDER BY version DESC LIMIT 1"
            ).fetchone()

            restored = None
            if prev:
                conn.execute(
                    "UPDATE policy_versions SET status = 'active' WHERE id = ?",
                    (prev[0],),
                )
                restored = self._row_to_pv(prev)
                restored.status = "active"

            self._audit("policy_rollback", policy_id, role)
            conn.commit()
            return restored
        finally:
            conn.close()

    # -- audit helper -------------------------------------------------------

    @staticmethod
    def _audit(action: str, policy_id: str, role: str) -> int:
        try:
            from core.audit_ledger import record
            return record(
                action=action,
                actor="policy_registry",
                role=role,
                decision="approved",
                reason=f"Policy {policy_id}",
                metadata={"policy_id": policy_id},
            )
        except Exception:
            return 0


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_global_registry: Optional[PolicyRegistry] = None


def get_policy_registry() -> PolicyRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = PolicyRegistry()
    return _global_registry
