"""
Vectrax Episodic Memory
========================
Append-only JSONL ledger at ``vault/episodic_ledger.jsonl``.

Event types: FILE_OBSERVED, STRUCTURE_EXTRACTED, INTENT_INFERRED,
PATTERN_CANDIDATE_CREATED, SEMANTIC_INDEX_UPSERTED,
ASCENSION_STATE_CHANGED, INGESTION_SUMMARY.

Each entry stores hashes and summaries — never raw secrets or PII.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from core.learn import VAULT_DIR, EPISODIC_LEDGER_PATH, EVENT_TYPES


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class EpisodicEvent:
    event_type: str
    summary: str
    content_hash: str = ""
    intent_fingerprint: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Auto-populated on append
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

class EpisodicLedger:
    """Append-only JSONL episodic ledger."""

    def __init__(self, path: str = EPISODIC_LEDGER_PATH):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def append(self, event: EpisodicEvent) -> str:
        """Append an event and return its id."""
        if event.event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown event_type: {event.event_type}")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        return event.id

    def read_all(self) -> List[Dict[str, Any]]:
        """Read every event from the ledger."""
        if not os.path.isfile(self.path):
            return []
        events = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return events

    def read_recent(self, n: int = 50) -> List[Dict[str, Any]]:
        """Return the last *n* events (most recent last)."""
        all_events = self.read_all()
        return all_events[-n:]

    def count(self) -> int:
        if not os.path.isfile(self.path):
            return 0
        with open(self.path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    def count_by_type(self) -> Dict[str, int]:
        """Count events grouped by event_type."""
        counts: Dict[str, int] = {}
        for ev in self.read_all():
            et = ev.get("event_type", "UNKNOWN")
            counts[et] = counts.get(et, 0) + 1
        return counts

    def since(self, hours: float = 24.0) -> List[Dict[str, Any]]:
        """Return events from the last *hours*."""
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        cutoff_iso = cutoff.isoformat()
        return [
            ev for ev in self.read_all()
            if ev.get("timestamp", "") >= cutoff_iso
        ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def content_hash(text: str) -> str:
    """SHA-256 of text, truncated to 16 hex chars."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_ledger: Optional[EpisodicLedger] = None


def get_ledger() -> EpisodicLedger:
    global _ledger
    if _ledger is None:
        _ledger = EpisodicLedger()
    return _ledger
