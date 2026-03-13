"""
Vectrax Ascension Gate
=======================
Human-governed application of learned patterns.

Candidates are QUARANTINED by default.
Only APPROVED patterns may influence proposals.

CLI integration:
  vx learn approve <id>
  vx learn ban <id>
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.learn import VAULT_DIR, CANDIDATES_PATH, CANDIDATE_STATES


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PatternCandidate:
    summary: str
    intent: str
    file_path: str = ""
    symbols: List[str] = field(default_factory=list)
    confidence: float = 0.0
    impact: str = "low"
    content_hash: str = ""
    cc_score: float = 0.0           # Cognitive Consistency
    domain: str = "unknown"         # Domain classification
    constitution_mode: str = "OBSERVE"  # Constitution-derived mode
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Auto-populated
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    state: str = "QUARANTINED"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    decided_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

class AscensionGate:
    """Manages pattern candidates: quarantine, approve, ban."""

    def __init__(self, path: str = CANDIDATES_PATH):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    # -- persistence --------------------------------------------------------

    def _load_all(self) -> List[Dict[str, Any]]:
        if not os.path.isfile(self.path):
            return []
        records = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records

    def _save_all(self, records: List[Dict[str, Any]]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # -- public API ---------------------------------------------------------

    def add(self, candidate: PatternCandidate) -> str:
        """Add a new candidate (always starts QUARANTINED). Returns id."""
        candidate.state = "QUARANTINED"
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(candidate.to_dict(), ensure_ascii=False) + "\n")
        return candidate.id

    def approve(self, candidate_id: str) -> bool:
        """Approve a quarantined candidate. Returns True if found."""
        return self._transition(candidate_id, "APPROVED")

    def ban(self, candidate_id: str) -> bool:
        """Ban a candidate. Returns True if found."""
        return self._transition(candidate_id, "BANNED")

    def _transition(self, candidate_id: str, new_state: str) -> bool:
        records = self._load_all()
        found = False
        for rec in records:
            if rec.get("id") == candidate_id:
                rec["state"] = new_state
                rec["decided_at"] = datetime.now(timezone.utc).isoformat()
                found = True
                break
        if found:
            self._save_all(records)
            # Log to episodic ledger (best-effort)
            try:
                from core.learn.episodic import get_ledger, EpisodicEvent
                get_ledger().append(EpisodicEvent(
                    event_type="ASCENSION_STATE_CHANGED",
                    summary=f"Candidate {candidate_id} → {new_state}",
                    metadata={"candidate_id": candidate_id, "new_state": new_state},
                ))
            except Exception:
                pass
        return found

    def get(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        """Get a single candidate by id."""
        for rec in self._load_all():
            if rec.get("id") == candidate_id:
                return rec
        return None

    def list_by_state(self, state: str = "QUARANTINED") -> List[Dict[str, Any]]:
        """List candidates filtered by state."""
        return [r for r in self._load_all() if r.get("state") == state]

    def list_all(self) -> List[Dict[str, Any]]:
        return self._load_all()

    def count_by_state(self) -> Dict[str, int]:
        """Count candidates grouped by state."""
        counts: Dict[str, int] = {s: 0 for s in CANDIDATE_STATES}
        for rec in self._load_all():
            st = rec.get("state", "QUARANTINED")
            counts[st] = counts.get(st, 0) + 1
        return counts

    def approved_patterns(self) -> List[Dict[str, Any]]:
        """Return only APPROVED candidates (safe to influence proposals)."""
        return self.list_by_state("APPROVED")

    def update_cc(self, candidate_id: str, cc_score: float, mode: str = "") -> bool:
        """Update CC score and constitution mode on an existing candidate."""
        records = self._load_all()
        found = False
        for rec in records:
            if rec.get("id") == candidate_id:
                rec["cc_score"] = round(cc_score, 4)
                if mode:
                    rec["constitution_mode"] = mode
                found = True
                break
        if found:
            self._save_all(records)
        return found


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_gate: Optional[AscensionGate] = None


def get_gate() -> AscensionGate:
    global _gate
    if _gate is None:
        _gate = AscensionGate()
    return _gate
