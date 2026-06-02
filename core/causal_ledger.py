"""
Vectrax Causal Ledger
=======================
Append-only JSONL ledger for action lifecycle events.

Event types:
  ACTION_PLANNED     — ActionSandbox produced a dry-run plan.
  ACTION_EXECUTED    — ActionExecutor completed successfully.
  ACTION_FAILED      — ActionExecutor encountered an unexpected error.
  ACTION_ROLLED_BACK — ActionExecutor rolled back after test failure.

File location: vault/causal_ledger.jsonl
Each line is a self-contained JSON object for streaming reads.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VAULT_DIR = os.environ.get("VECTRAX_VAULT_DIR", os.path.join(os.path.expanduser("~/Vectrax"), "vault"))
LEDGER_PATH = os.path.join(VAULT_DIR, "causal_ledger.jsonl")


class CausalEventType(str, Enum):
    ACTION_PLANNED = "ACTION_PLANNED"
    ACTION_EXECUTED = "ACTION_EXECUTED"
    ACTION_FAILED = "ACTION_FAILED"
    ACTION_ROLLED_BACK = "ACTION_ROLLED_BACK"


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def _ensure_vault() -> None:
    os.makedirs(VAULT_DIR, exist_ok=True)


def append(
    event_type: CausalEventType,
    plan_id: str,
    *,
    action_type: str = "",
    routing_decision: str = "",
    risk_score: Optional[float] = None,
    polarity_state: str = "",
    tension: Optional[float] = None,
    precedent_confidence: Optional[float] = None,
    details: Optional[Dict[str, Any]] = None,
    diff_hash: str = "",
) -> Dict[str, Any]:
    """
    Append a causal event to the JSONL ledger.
    Returns the written record.
    """
    _ensure_vault()

    record: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type.value,
        "plan_id": plan_id,
        "action_type": action_type,
        "routing_decision": routing_decision,
        "risk_score": risk_score,
        "polarity_state": polarity_state,
        "tension": tension,
        "precedent_confidence": precedent_confidence,
        "details": details or {},
        "diff_hash": diff_hash,
    }

    with open(LEDGER_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def read_all() -> List[Dict[str, Any]]:
    """Read all events from the ledger (oldest first)."""
    if not os.path.exists(LEDGER_PATH):
        return []

    events: List[Dict[str, Any]] = []
    with open(LEDGER_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return events


def read_last(n: int = 20) -> List[Dict[str, Any]]:
    """Read the last *n* events (most recent first)."""
    all_events = read_all()
    return list(reversed(all_events[-n:]))


def read_by_plan(plan_id: str) -> List[Dict[str, Any]]:
    """Read all events for a specific plan_id (oldest first)."""
    return [e for e in read_all() if e.get("plan_id") == plan_id]


# ---------------------------------------------------------------------------
# Precedent confidence
# ---------------------------------------------------------------------------

def query_precedent(action_type: str) -> float:
    """
    Compute precedent confidence for an action_type.

    Returns the ratio of ACTION_EXECUTED events over total terminal events
    (EXECUTED + FAILED + ROLLED_BACK) for the given action_type.

    If no history exists, returns 0.0 (no precedent = no confidence).
    """
    events = read_all()

    executed = 0
    failed = 0
    rolled_back = 0

    for e in events:
        if e.get("action_type") != action_type:
            continue
        et = e.get("event_type", "")
        if et == CausalEventType.ACTION_EXECUTED.value:
            executed += 1
        elif et == CausalEventType.ACTION_FAILED.value:
            failed += 1
        elif et == CausalEventType.ACTION_ROLLED_BACK.value:
            rolled_back += 1

    total = executed + failed + rolled_back
    if total == 0:
        return 0.0

    return executed / total


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def compute_diff_hash(content: str) -> str:
    """SHA-256 hash (truncated) for integrity verification."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def count() -> int:
    """Return total events in the ledger."""
    return len(read_all())
