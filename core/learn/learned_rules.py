"""
Vectrax Learned Rules Store
==============================
Persists rules that emerge from confirmed hypotheses.

A learned rule represents a validated behavioral pattern that the system
can apply to future interactions.  Rules are stored in
``vault/learned_rules.jsonl`` and must pass Governor validation before
activation.

Privacy: rules store only abstract pattern descriptors — never raw
user data, credentials, or PII.

Creado: 2026-03-19
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.learn import VAULT_DIR

logger = logging.getLogger("vectrax.learn.learned_rules")

RULES_PATH = os.path.join(VAULT_DIR, "learned_rules.jsonl")

# ---------------------------------------------------------------------------
# Rule states
# ---------------------------------------------------------------------------

RULE_STATES = ("pending", "active", "inactive", "rejected")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class LearnedRule:
    """A behavioural rule derived from a confirmed hypothesis."""

    id: str = field(default_factory=lambda: f"RULE-{uuid.uuid4().hex[:8].upper()}")
    description: str = ""
    source_hypothesis_id: str = ""
    confidence: float = 0.0
    pattern: str = ""            # abstract pattern descriptor
    action: str = ""             # what the system should do when pattern matches
    category: str = ""           # e.g. "language", "routing", "response_style"
    state: str = "pending"       # pending → active | rejected
    governor_mode_at_creation: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    activated_at: Optional[str] = None
    deactivated_at: Optional[str] = None
    applications: int = 0        # how many times the rule has been applied
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class LearnedRulesStore:
    """
    JSONL-backed store for learned rules.

    Rules flow:
      1. Created as 'pending' from a confirmed hypothesis
      2. Governor validates → 'active' or 'rejected'
      3. Active rules influence system behaviour
      4. Can be deactivated if they prove harmful
    """

    def __init__(self, path: str = RULES_PATH) -> None:
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    # -- Persistence --------------------------------------------------------

    def _load_all(self) -> List[Dict[str, Any]]:
        if not os.path.isfile(self.path):
            return []
        records: List[Dict[str, Any]] = []
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

    # -- Public API ---------------------------------------------------------

    def add_rule(self, rule: LearnedRule) -> str:
        """Add a new learned rule (starts as 'pending'). Returns rule id."""
        rule.state = "pending"
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rule.to_dict(), ensure_ascii=False) + "\n")
        logger.info(
            "Learned rule created: %s | pattern='%s' | confidence=%.2f",
            rule.id, rule.pattern[:60], rule.confidence,
        )
        return rule.id

    def activate_rule(self, rule_id: str) -> bool:
        """Activate a pending rule (Governor approved)."""
        return self._transition(rule_id, "active")

    def reject_rule(self, rule_id: str) -> bool:
        """Reject a pending rule."""
        return self._transition(rule_id, "rejected")

    def deactivate_rule(self, rule_id: str) -> bool:
        """Deactivate an active rule."""
        return self._transition(rule_id, "inactive")

    def record_application(self, rule_id: str) -> bool:
        """Increment the application counter for a rule."""
        records = self._load_all()
        found = False
        for rec in records:
            if rec.get("id") == rule_id:
                rec["applications"] = rec.get("applications", 0) + 1
                found = True
                break
        if found:
            self._save_all(records)
        return found

    def _transition(self, rule_id: str, new_state: str) -> bool:
        records = self._load_all()
        found = False
        now = datetime.now(timezone.utc).isoformat()
        for rec in records:
            if rec.get("id") == rule_id:
                rec["state"] = new_state
                if new_state == "active":
                    rec["activated_at"] = now
                elif new_state == "inactive":
                    rec["deactivated_at"] = now
                found = True
                break
        if found:
            self._save_all(records)
            logger.info("Rule %s → %s", rule_id, new_state)
        return found

    # -- Queries ------------------------------------------------------------

    def get(self, rule_id: str) -> Optional[Dict[str, Any]]:
        for rec in self._load_all():
            if rec.get("id") == rule_id:
                return rec
        return None

    def get_active_rules(self) -> List[Dict[str, Any]]:
        """Return only active rules."""
        return [r for r in self._load_all() if r.get("state") == "active"]

    def get_pending_rules(self) -> List[Dict[str, Any]]:
        return [r for r in self._load_all() if r.get("state") == "pending"]

    def get_by_category(self, category: str) -> List[Dict[str, Any]]:
        return [
            r for r in self._load_all()
            if r.get("category") == category and r.get("state") == "active"
        ]

    def list_all(self) -> List[Dict[str, Any]]:
        return self._load_all()

    def stats(self) -> Dict[str, Any]:
        all_rules = self._load_all()
        by_state: Dict[str, int] = {}
        for r in all_rules:
            st = r.get("state", "pending")
            by_state[st] = by_state.get(st, 0) + 1
        return {
            "total": len(all_rules),
            "by_state": by_state,
            "total_applications": sum(r.get("applications", 0) for r in all_rules),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_store: Optional[LearnedRulesStore] = None


def get_rules_store() -> LearnedRulesStore:
    global _store
    if _store is None:
        _store = LearnedRulesStore()
    return _store
