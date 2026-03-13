"""
Human Review Gate — persists cognitive proposals for creator approval.

No cognitive proposal can be executed without passing through this gate.
Proposals are stored in SQLite (cognitive_proposals table).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from cognition.errors import ApprovalRequired
from cognition.types import CognitiveProposal, ProposalStatus

logger = logging.getLogger("vectrax.cognition.reasoning.gate")

_DB_DIR = Path.home() / ".vectrax"
_DB_PATH = _DB_DIR / "vectrax.db"


class HumanReviewGate:
    """
    Manages cognitive proposals lifecycle:
      1. submit() — persist a draft/pending proposal
      2. Creator reviews via CLI
      3. approve() / reject()
      4. is_approved() — check before execution
    """

    def __init__(self) -> None:
        self._ensure_table()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit(self, proposal: CognitiveProposal) -> CognitiveProposal:
        """Submit a proposal for creator review."""
        proposal.status = ProposalStatus.PENDING
        self._save(proposal)
        logger.info("Cognitive proposal submitted: %s", proposal.id)
        return proposal

    def approve(self, proposal_id: str) -> Optional[CognitiveProposal]:
        """Approve a pending proposal (creator-only)."""
        return self._decide(proposal_id, ProposalStatus.APPROVED)

    def reject(self, proposal_id: str) -> Optional[CognitiveProposal]:
        """Reject a pending proposal (creator-only)."""
        return self._decide(proposal_id, ProposalStatus.REJECTED)

    def is_approved(self, proposal_id: str) -> bool:
        """Check if a proposal has been approved."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT status FROM cognitive_proposals WHERE id=?",
                (proposal_id,),
            ).fetchone()
        return row is not None and row[0] == ProposalStatus.APPROVED.value

    def require_approval(self, proposal_id: str) -> None:
        """Raise ApprovalRequired if not approved."""
        if not self.is_approved(proposal_id):
            raise ApprovalRequired(
                f"Cognitive proposal '{proposal_id}' requires creator approval. "
                "Run: vectrax cognition approve <id>"
            )

    def get(self, proposal_id: str) -> Optional[CognitiveProposal]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM cognitive_proposals WHERE id=?",
                (proposal_id,),
            ).fetchone()
        return self._row_to_proposal(row) if row else None

    def list_proposals(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[CognitiveProposal]:
        query = "SELECT * FROM cognitive_proposals"
        params: list = []
        if status:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_proposal(r) for r in rows]

    def mark_executed(
        self,
        proposal_id: str,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mark an approved proposal as executed."""
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE cognitive_proposals SET status=?, decided_at=?, "
                "execution_result=? WHERE id=?",
                (
                    ProposalStatus.EXECUTED.value,
                    now,
                    json.dumps(result or {}),
                    proposal_id,
                ),
            )

    def mark_failed(
        self,
        proposal_id: str,
        error: str = "",
    ) -> None:
        """Mark a proposal as failed."""
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE cognitive_proposals SET status=?, decided_at=?, "
                "execution_result=? WHERE id=?",
                (
                    ProposalStatus.FAILED.value,
                    now,
                    json.dumps({"error": error}),
                    proposal_id,
                ),
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _decide(
        self,
        proposal_id: str,
        decision: ProposalStatus,
    ) -> Optional[CognitiveProposal]:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE cognitive_proposals SET status=?, decided_at=? WHERE id=?",
                (decision.value, now, proposal_id),
            )
        proposal = self.get(proposal_id)
        if proposal:
            logger.info("Cognitive proposal %s: %s", proposal_id, decision.value)
        return proposal

    def _save(self, p: CognitiveProposal) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO cognitive_proposals
                   (id, timestamp, status, description, context_id,
                    reasoning, actions, decided_at, execution_result)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    p.id,
                    p.timestamp,
                    p.status.value,
                    p.description,
                    p.context_id,
                    json.dumps(p.reasoning.to_dict() if p.reasoning else None),
                    json.dumps(p.actions),
                    p.decided_at,
                    json.dumps(p.execution_result) if p.execution_result else None,
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
                CREATE TABLE IF NOT EXISTS cognitive_proposals (
                    id TEXT PRIMARY KEY,
                    timestamp REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    description TEXT NOT NULL DEFAULT '',
                    context_id TEXT DEFAULT '',
                    reasoning TEXT DEFAULT '{}',
                    actions TEXT DEFAULT '[]',
                    decided_at REAL,
                    execution_result TEXT
                )
            """)

    @staticmethod
    def _row_to_proposal(row) -> CognitiveProposal:
        reasoning_data = json.loads(row["reasoning"]) if row["reasoning"] else None
        # Reconstruct ReasoningResult if data present
        reasoning = None
        if reasoning_data:
            from cognition.types import ReasoningResult, ImpactAssessment, Scenario
            impact_data = reasoning_data.get("impact")
            impact = ImpactAssessment(**impact_data) if impact_data else None
            scenarios = [Scenario(**s) for s in reasoning_data.get("scenarios", [])]
            reasoning = ReasoningResult(
                risk_score=reasoning_data.get("risk_score", 0),
                risk_level=reasoning_data.get("risk_level", "LOW"),
                impact=impact,
                scenarios=scenarios,
                contradictions=reasoning_data.get("contradictions", []),
                constitutional_pass=reasoning_data.get("constitutional_pass", True),
                constitutional_reasons=reasoning_data.get("constitutional_reasons", []),
                recommendation=reasoning_data.get("recommendation", ""),
                details=reasoning_data.get("details", ""),
            )

        return CognitiveProposal(
            id=row["id"],
            timestamp=row["timestamp"],
            status=ProposalStatus(row["status"]),
            description=row["description"],
            context_id=row["context_id"] or "",
            reasoning=reasoning,
            actions=json.loads(row["actions"]) if row["actions"] else [],
            decided_at=row["decided_at"],
            execution_result=json.loads(row["execution_result"]) if row["execution_result"] else None,
        )
