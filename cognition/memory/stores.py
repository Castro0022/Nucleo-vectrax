"""
Memory Stores — five specialised persistent stores.

Each store persists data as JSONL in vault/ and maintains an in-memory
cache.  Only abstract metadata is stored — no raw content, PII, or
credentials.

Stores:
  - DecisionStore   — approved / rejected / pending decisions
  - PatternStore    — wraps GravityIndex for cognitive patterns
  - SignalStore     — recent classified signals (circular)
  - ErrorStore      — errors + mitigations attempted
  - PrincipleStore  — active principles and restrictions
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.cognition.memory.stores")

VAULT_DIR = os.environ.get(
    "VECTRAX_VAULT_DIR",
    os.path.join(os.path.expanduser("~"), "Vectrax", "vault"),
)

MAX_SIGNAL_HISTORY = 500
MAX_ERROR_HISTORY = 200
MAX_DECISION_HISTORY = 500


def _ensure_vault() -> None:
    os.makedirs(VAULT_DIR, exist_ok=True)


def _append_jsonl(path: str, record: dict) -> None:
    _ensure_vault()
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.debug("JSONL write failed %s: %s", path, exc)


def _read_jsonl(path: str, limit: int = 0) -> List[dict]:
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        records = []
        for line in lines:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if limit > 0:
            records = records[-limit:]
        return records
    except OSError:
        return []


# ---------------------------------------------------------------------------
# Decision Store
# ---------------------------------------------------------------------------

DECISIONS_PATH = os.path.join(VAULT_DIR, "cognition_decisions.jsonl")


class DecisionStore:
    """Append-only log of cognitive decisions."""

    def record(
        self,
        decision_id: str,
        description: str,
        status: str,
        risk_score: float = 0.0,
        context_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        rec = {
            "id": decision_id,
            "description": description[:200],
            "status": status,
            "risk_score": round(risk_score, 4),
            "context_id": context_id,
            "ts": time.time(),
            "metadata": metadata or {},
        }
        _append_jsonl(DECISIONS_PATH, rec)
        return rec

    def recent(self, n: int = 20) -> List[Dict[str, Any]]:
        return list(reversed(_read_jsonl(DECISIONS_PATH, limit=n)))

    def by_status(self, status: str) -> List[Dict[str, Any]]:
        return [d for d in _read_jsonl(DECISIONS_PATH) if d.get("status") == status]

    def search(self, term: str) -> List[Dict[str, Any]]:
        term_lower = term.lower()
        return [
            d for d in _read_jsonl(DECISIONS_PATH)
            if term_lower in d.get("description", "").lower()
        ]


# ---------------------------------------------------------------------------
# Pattern Store  (wraps existing GravityIndex)
# ---------------------------------------------------------------------------

class PatternStore:
    """Facade over GravityIndex for cognitive pattern queries."""

    def __init__(self) -> None:
        self._index = None

    def _get_index(self):
        if self._index is None:
            try:
                from core.learn.gravity_engine import GravityIndex
                self._index = GravityIndex()
            except ImportError:
                logger.warning("GravityIndex not available")
        return self._index

    def record_pattern(
        self,
        fingerprint: str,
        cc_score: float = 0.0,
        impact: str = "low",
        domain: str = "cognition",
        intent: str = "",
        outcome: str = "observed",
        summary: str = "",
    ) -> Optional[Dict[str, Any]]:
        index = self._get_index()
        if index is None:
            return None
        rec, promotion = index.record_event(
            fingerprint=fingerprint,
            cc_score=cc_score,
            impact=impact,
            domain=domain,
            intent=intent,
            outcome=outcome,
            summary=summary,
        )
        result = rec.to_dict()
        if promotion:
            result["promotion"] = promotion
        return result

    def get_pattern(self, fingerprint: str) -> Optional[Dict[str, Any]]:
        index = self._get_index()
        if index is None:
            return None
        rec = index.get(fingerprint)
        return rec.to_dict() if rec else None

    def search_by_tier(self, tier: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
        index = self._get_index()
        if index is None:
            return {}
        tiers = [tier] if tier else None
        result = index.search_by_tier(tiers)
        return {k: [r.to_dict() for r in v] for k, v in result.items()}


# ---------------------------------------------------------------------------
# Signal Store
# ---------------------------------------------------------------------------

SIGNALS_PATH = os.path.join(VAULT_DIR, "cognition_signals.jsonl")


class SignalStore:
    """Circular history of classified signals (abstract only)."""

    def record(
        self,
        signal_id: str,
        source: str,
        category: str,
        priority: float,
        intent: str = "",
    ) -> Dict[str, Any]:
        rec = {
            "id": signal_id,
            "source": source,
            "category": category,
            "priority": round(priority, 4),
            "intent": intent,
            "ts": time.time(),
        }
        _append_jsonl(SIGNALS_PATH, rec)
        return rec

    def recent(self, n: int = 50) -> List[Dict[str, Any]]:
        return list(reversed(_read_jsonl(SIGNALS_PATH, limit=n)))

    def by_source(self, source: str) -> List[Dict[str, Any]]:
        return [s for s in _read_jsonl(SIGNALS_PATH) if s.get("source") == source]

    def by_category(self, category: str) -> List[Dict[str, Any]]:
        return [s for s in _read_jsonl(SIGNALS_PATH) if s.get("category") == category]


# ---------------------------------------------------------------------------
# Error Store
# ---------------------------------------------------------------------------

ERRORS_PATH = os.path.join(VAULT_DIR, "cognition_errors.jsonl")


class ErrorStore:
    """Log of errors and mitigations attempted."""

    def record(
        self,
        error_type: str,
        description: str,
        source: str = "",
        mitigation: str = "",
        resolved: bool = False,
    ) -> Dict[str, Any]:
        rec = {
            "error_type": error_type,
            "description": description[:200],
            "source": source,
            "mitigation": mitigation[:200],
            "resolved": resolved,
            "ts": time.time(),
        }
        _append_jsonl(ERRORS_PATH, rec)
        return rec

    def recent(self, n: int = 20) -> List[Dict[str, Any]]:
        return list(reversed(_read_jsonl(ERRORS_PATH, limit=n)))

    def unresolved(self) -> List[Dict[str, Any]]:
        return [e for e in _read_jsonl(ERRORS_PATH) if not e.get("resolved")]

    def by_type(self, error_type: str) -> List[Dict[str, Any]]:
        return [e for e in _read_jsonl(ERRORS_PATH) if e.get("error_type") == error_type]


# ---------------------------------------------------------------------------
# Principle Store
# ---------------------------------------------------------------------------

PRINCIPLES_PATH = os.path.join(VAULT_DIR, "cognition_principles.jsonl")


class PrincipleStore:
    """
    Active principles and restrictions.

    Loads immutable constitutional principles and allows adding
    operational restrictions.
    """

    def get_constitutional(self) -> List[Dict[str, Any]]:
        """Return the immutable constitutional principles."""
        try:
            from constitution import IMMUTABLE_PRINCIPLES
            return [
                {
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "type": "constitutional",
                }
                for p in IMMUTABLE_PRINCIPLES
            ]
        except ImportError:
            return []

    def add_restriction(
        self,
        name: str,
        description: str,
        scope: str = "global",
    ) -> Dict[str, Any]:
        """Add an operational restriction (not constitutional)."""
        rec = {
            "name": name,
            "description": description[:200],
            "scope": scope,
            "type": "operational",
            "active": True,
            "ts": time.time(),
        }
        _append_jsonl(PRINCIPLES_PATH, rec)
        return rec

    def get_all(self) -> List[Dict[str, Any]]:
        """All principles: constitutional + operational."""
        result = self.get_constitutional()
        result.extend(_read_jsonl(PRINCIPLES_PATH))
        return result

    def get_active(self) -> List[Dict[str, Any]]:
        """Only active principles/restrictions."""
        return [p for p in self.get_all() if p.get("active", True)]
