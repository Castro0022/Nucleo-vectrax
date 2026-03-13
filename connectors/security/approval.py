"""
Vectrax UCE — Approval Gate.

No connection activates without explicit creator approval.  The gate
generates ConnectionProposal objects, persists them, and only allows
operations when the proposal status is 'approved'.
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

from connectors.contracts.schema import ConnectorContract
from connectors.core.errors import ApprovalRequired
from connectors.security.policy import ConnectionPolicy

logger = logging.getLogger("vectrax.uce.security.approval")

_DB_DIR = Path.home() / ".vectrax"
_DB_PATH = _DB_DIR / "vectrax.db"


# ------------------------------------------------------------------
# Data model
# ------------------------------------------------------------------

@dataclass
class ConnectionProposal:
    """Proposal that must be approved before a connector goes live."""
    id: str = field(default_factory=lambda: f"CP-{uuid.uuid4().hex[:8].upper()}")
    connector_name: str = ""
    connector_version: str = ""
    family: str = ""
    capabilities: List[str] = field(default_factory=list)
    permissions_required: List[str] = field(default_factory=list)
    auth_method: str = ""
    risks: List[str] = field(default_factory=list)
    risk_level: str = "low"
    description: str = ""
    status: str = "pending"          # pending | approved | rejected
    created_at: float = field(default_factory=time.time)
    decided_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "connector_name": self.connector_name,
            "connector_version": self.connector_version,
            "family": self.family,
            "capabilities": self.capabilities,
            "permissions_required": self.permissions_required,
            "auth_method": self.auth_method,
            "risks": self.risks,
            "risk_level": self.risk_level,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
        }


# ------------------------------------------------------------------
# Approval Gate
# ------------------------------------------------------------------

class ApprovalGate:
    """
    Generates, stores and manages connection proposals.

    Workflow:
      1. ``propose(contract)`` — creates a pending proposal
      2. Creator reviews via CLI / API
      3. ``approve(proposal_id)`` or ``reject(proposal_id)``
      4. ``is_approved(connector_name)`` — checks status
    """

    def __init__(self) -> None:
        self._ensure_table()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def propose(self, contract: ConnectorContract) -> ConnectionProposal:
        """Generate a new connection proposal from a contract."""
        risk = ConnectionPolicy.assess_risk(contract)
        proposal = ConnectionProposal(
            connector_name=contract.name,
            connector_version=contract.version,
            family=contract.family.value,
            capabilities=[c.value for c in contract.capabilities],
            permissions_required=contract.required_permissions,
            auth_method=contract.auth_method.value,
            risks=risk["reasons"],
            risk_level=risk["risk_level"],
            description=contract.description,
        )
        self._save(proposal)
        logger.info("Connection proposal created: %s for '%s'", proposal.id, contract.name)
        return proposal

    def approve(self, proposal_id: str) -> Optional[ConnectionProposal]:
        """Approve a pending proposal (creator-only action)."""
        return self._decide(proposal_id, "approved")

    def reject(self, proposal_id: str) -> Optional[ConnectionProposal]:
        """Reject a pending proposal (creator-only action)."""
        return self._decide(proposal_id, "rejected")

    def is_approved(self, connector_name: str) -> bool:
        """Check whether the connector has an approved proposal."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT status FROM connection_proposals "
                "WHERE connector_name=? ORDER BY created_at DESC LIMIT 1",
                (connector_name,),
            ).fetchone()
        return row is not None and row[0] == "approved"

    def require_approval(self, connector_name: str) -> None:
        """Raise ApprovalRequired if the connector is not approved."""
        if not self.is_approved(connector_name):
            raise ApprovalRequired(
                f"Connector '{connector_name}' requires creator approval. "
                "Run: vectrax connect propose <name>"
            )

    def get_proposal(self, proposal_id: str) -> Optional[ConnectionProposal]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM connection_proposals WHERE id=?", (proposal_id,)
            ).fetchone()
        return self._row_to_proposal(row) if row else None

    def list_proposals(self, status: Optional[str] = None) -> List[ConnectionProposal]:
        query = "SELECT * FROM connection_proposals"
        params: list = []
        if status:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_proposal(r) for r in rows]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _decide(self, proposal_id: str, decision: str) -> Optional[ConnectionProposal]:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE connection_proposals SET status=?, decided_at=? WHERE id=?",
                (decision, now, proposal_id),
            )
        proposal = self.get_proposal(proposal_id)
        if proposal:
            logger.info("Proposal %s: %s", proposal_id, decision)
        return proposal

    def _save(self, p: ConnectionProposal) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO connection_proposals
                   (id, connector_name, connector_version, family, capabilities,
                    permissions_required, auth_method, risks, risk_level,
                    description, status, created_at, decided_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    p.id, p.connector_name, p.connector_version, p.family,
                    json.dumps(p.capabilities), json.dumps(p.permissions_required),
                    p.auth_method, json.dumps(p.risks), p.risk_level,
                    p.description, p.status, p.created_at, p.decided_at,
                ),
            )

    @staticmethod
    def _conn() -> sqlite3.Connection:
        _DB_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS connection_proposals (
                    id TEXT PRIMARY KEY,
                    connector_name TEXT NOT NULL,
                    connector_version TEXT NOT NULL DEFAULT '0.1.0',
                    family TEXT NOT NULL DEFAULT 'custom',
                    capabilities TEXT NOT NULL DEFAULT '[]',
                    permissions_required TEXT NOT NULL DEFAULT '[]',
                    auth_method TEXT NOT NULL DEFAULT 'none',
                    risks TEXT NOT NULL DEFAULT '[]',
                    risk_level TEXT NOT NULL DEFAULT 'low',
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at REAL NOT NULL,
                    decided_at REAL
                )
            """)

    @staticmethod
    def _row_to_proposal(row: sqlite3.Row) -> ConnectionProposal:
        return ConnectionProposal(
            id=row["id"],
            connector_name=row["connector_name"],
            connector_version=row["connector_version"],
            family=row["family"],
            capabilities=json.loads(row["capabilities"]),
            permissions_required=json.loads(row["permissions_required"]),
            auth_method=row["auth_method"],
            risks=json.loads(row["risks"]),
            risk_level=row["risk_level"],
            description=row["description"],
            status=row["status"],
            created_at=row["created_at"],
            decided_at=row["decided_at"],
        )
