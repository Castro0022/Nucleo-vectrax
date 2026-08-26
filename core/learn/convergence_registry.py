"""Canonical convergence entity registry.

Separates persistent convergence relationships from the legacy
``convergence_events`` ledger.  The legacy table is never modified here.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("vectrax.convergence_registry")

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "vault", "convergence_history.db",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS convergences (
    convergence_id TEXT PRIMARY KEY,
    entity_a_id TEXT NOT NULL,
    entity_a_type TEXT NOT NULL,
    entity_b_id TEXT NOT NULL,
    entity_b_type TEXT NOT NULL,
    domain_a TEXT NOT NULL,
    domain_b TEXT NOT NULL,
    relationship_type TEXT NOT NULL DEFAULT '',
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    confirmation_count INTEGER NOT NULL DEFAULT 1,
    combined_cc REAL NOT NULL DEFAULT 0,
    combined_hits INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    dissolved_at REAL,
    created_from TEXT NOT NULL DEFAULT 'live',
    extra TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_convergences_status ON convergences(status);
CREATE TABLE IF NOT EXISTS convergence_lifecycle_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    convergence_id TEXT NOT NULL,
    event TEXT NOT NULL,
    timestamp REAL NOT NULL,
    combined_cc REAL,
    combined_hits INTEGER,
    extra TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_lifecycle_convergence
    ON convergence_lifecycle_events(convergence_id);
CREATE TABLE IF NOT EXISTS convergence_event_map (
    event_id INTEGER PRIMARY KEY,
    convergence_id TEXT,
    ambiguous INTEGER NOT NULL DEFAULT 0
);
"""


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Open the registry DB and idempotently create additive tables."""
    path = db_path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=3)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


@dataclass(frozen=True)
class NormalizedEntity:
    raw_id: str
    canonical_id: str
    entity_type: str
    domain: str
    resolution: str
    confidence: float

    @property
    def sort_key(self) -> Tuple[str, str, str]:
        return self.entity_type, self.domain, self.canonical_id


def _is_corrupted_fragment(raw_id: str, context_ids: Iterable[str]) -> bool:
    """Recognize the confirmed legacy ``key.split(':')[-1]`` corruption."""
    return any(
        other != raw_id
        and other.count(":") >= 2
        and other.rsplit(":", 1)[-1] == raw_id
        for other in context_ids
    )


def normalize_entity_id(
    raw_id: str,
    entity_type: str = "star",
    known_domain: Optional[str] = None,
    live_fingerprints: Optional[Dict[str, str]] = None,
    context_ids: Optional[Iterable[str]] = None,
) -> NormalizedEntity:
    """Resolve a participant through the single identity SSOT.

    Resolution order (each step requires real, verifiable evidence — no
    alias tables, no fuzzy matching):
    1. ``raw_id`` already IS a live fingerprint → authoritative as-is.
    2. ``raw_id`` has no domain prefix, but a recorded ``known_domain``
       hint exists (e.g. from the event that observed it) AND
       ``f"{known_domain}:{raw_id}"`` is itself a live fingerprint → that
       reconstruction IS the same entity, verified against the real
       current index, not guessed. This is what unifies a historical
       alternate representation such as bare ``AAPL`` with the live
       ``market:AAPL`` fingerprint, using only the domain that was
       actually recorded alongside it.
    3. A confirmed corrupted legacy fragment (see ``_is_corrupted_fragment``)
       → reported as ambiguous, never merged into anything.
    4. Otherwise → kept as its own stable identity, verbatim.
    """
    live_fingerprints = live_fingerprints or {}
    if raw_id in live_fingerprints:
        return NormalizedEntity(
            raw_id, raw_id, entity_type, live_fingerprints[raw_id],
            "exact_live_match", 1.0,
        )
    if known_domain and ":" not in raw_id:
        reconstructed = f"{known_domain}:{raw_id}"
        if reconstructed in live_fingerprints:
            return NormalizedEntity(
                raw_id, reconstructed, entity_type,
                live_fingerprints[reconstructed], "evidence_reconstructed", 0.9,
            )
    if context_ids is not None and _is_corrupted_fragment(raw_id, context_ids):
        return NormalizedEntity(
            raw_id, raw_id, entity_type, known_domain or "unknown_legacy",
            "ambiguous_corrupted_fragment", 0.0,
        )
    return NormalizedEntity(
        raw_id, raw_id, entity_type, known_domain or "unknown_legacy",
        "legacy_verbatim", 0.5,
    )


def compute_convergence_id(
    entity_a: NormalizedEntity, entity_b: NormalizedEntity
) -> str:
    """Hash normalized, ordered participants so A↔B equals B↔A."""
    a, b = sorted((entity_a, entity_b), key=lambda entity: entity.sort_key)
    material = (
        f"{a.entity_type}|{a.domain}|{a.canonical_id}||"
        f"{b.entity_type}|{b.domain}|{b.canonical_id}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _live_domains() -> Dict[str, str]:
    try:
        from core.learn.gravity_engine import get_gravity_index
        return {
            fingerprint: record.domain
            for fingerprint, record in get_gravity_index().load_raw().items()
        }
    except Exception as exc:
        logger.debug("live fingerprint map unavailable: %s", exc)
        return {}


def normalize_candidate(
    candidate: Dict[str, Any],
    live_fingerprints: Dict[str, str],
    context_ids: Iterable[str],
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[Dict[str, str]]]:
    """Normalize and order one detector candidate.

    Returns ``(convergence_id, normalized_data, ambiguity)``.
    """
    domains = candidate.get("domains") or ["", ""]
    raw_a = str(candidate.get("star_a", ""))
    raw_b = str(candidate.get("star_b", ""))
    entity_a = normalize_entity_id(
        raw_a, "star", domains[0] if domains else None,
        live_fingerprints, context_ids,
    )
    entity_b = normalize_entity_id(
        raw_b, "star", domains[1] if len(domains) > 1 else None,
        live_fingerprints, context_ids,
    )
    if (
        entity_a.resolution == "ambiguous_corrupted_fragment"
        or entity_b.resolution == "ambiguous_corrupted_fragment"
    ):
        return None, None, {"star_a": raw_a, "star_b": raw_b}

    first, second = sorted((entity_a, entity_b), key=lambda entity: entity.sort_key)
    convergence_id = compute_convergence_id(first, second)
    return convergence_id, {
        "entity_a": first,
        "entity_b": second,
        "relationship_type": candidate.get("type", ""),
        "combined_cc": float(candidate.get("combined_cc") or 0),
        "combined_hits": int(candidate.get("combined_hits") or 0),
    }, None


def record_convergence_snapshot(
    candidates: List[Dict[str, Any]],
    live_fingerprints: Optional[Dict[str, str]] = None,
    db_path: Optional[str] = None,
    observed_at: Optional[float] = None,
) -> Dict[str, Any]:
    """Create, confirm, dissolve, or revive canonical relationships."""
    live_fingerprints = live_fingerprints or _live_domains()
    now = observed_at if observed_at is not None else time.time()
    context_ids = {
        str(candidate.get(key, ""))
        for candidate in candidates
        for key in ("star_a", "star_b")
    }
    current: Dict[str, Dict[str, Any]] = {}
    ambiguities: List[Dict[str, str]] = []
    for candidate in candidates:
        convergence_id, data, ambiguity = normalize_candidate(
            candidate, live_fingerprints, context_ids
        )
        if ambiguity:
            ambiguities.append(ambiguity)
        elif convergence_id and data:
            current[convergence_id] = data

    result = {
        "created": 0, "confirmed": 0, "dissolved": 0,
        "reappeared": 0, "ambiguous": ambiguities,
    }
    conn = connect(db_path)
    try:
        existing = {
            row["convergence_id"]: dict(row)
            for row in conn.execute("SELECT * FROM convergences").fetchall()
        }
        active_ids = {
            convergence_id
            for convergence_id, row in existing.items()
            if row["status"] == "active"
        }
        for convergence_id, data in current.items():
            entity_a = data["entity_a"]
            entity_b = data["entity_b"]
            prior = existing.get(convergence_id)
            if prior is None:
                conn.execute(
                    """INSERT INTO convergences
                    (convergence_id, entity_a_id, entity_a_type, entity_b_id,
                     entity_b_type, domain_a, domain_b, relationship_type,
                     first_seen, last_seen, confirmation_count, combined_cc,
                     combined_hits, status, created_from)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        convergence_id,
                        entity_a.canonical_id, entity_a.entity_type,
                        entity_b.canonical_id, entity_b.entity_type,
                        entity_a.domain, entity_b.domain,
                        data["relationship_type"], now, now, 1,
                        data["combined_cc"], data["combined_hits"],
                        "active", "live",
                    ),
                )
                lifecycle = "created"
                result["created"] += 1
            elif prior["status"] == "active":
                conn.execute(
                    """UPDATE convergences
                    SET confirmation_count=confirmation_count+1,
                        last_seen=?, combined_cc=?, combined_hits=?
                    WHERE convergence_id=?""",
                    (
                        now, data["combined_cc"], data["combined_hits"],
                        convergence_id,
                    ),
                )
                lifecycle = "confirmed"
                result["confirmed"] += 1
            else:
                conn.execute(
                    """UPDATE convergences
                    SET status='active', dissolved_at=NULL,
                        confirmation_count=confirmation_count+1,
                        last_seen=?, combined_cc=?, combined_hits=?
                    WHERE convergence_id=?""",
                    (
                        now, data["combined_cc"], data["combined_hits"],
                        convergence_id,
                    ),
                )
                lifecycle = "reappeared"
                result["reappeared"] += 1
            conn.execute(
                """INSERT INTO convergence_lifecycle_events
                (convergence_id, event, timestamp, combined_cc, combined_hits)
                VALUES (?,?,?,?,?)""",
                (
                    convergence_id, lifecycle, now,
                    data["combined_cc"], data["combined_hits"],
                ),
            )

        for convergence_id in active_ids - set(current):
            conn.execute(
                """UPDATE convergences
                SET status='dissolved', dissolved_at=?
                WHERE convergence_id=?""",
                (now, convergence_id),
            )
            conn.execute(
                """INSERT INTO convergence_lifecycle_events
                (convergence_id, event, timestamp) VALUES (?,?,?)""",
                (convergence_id, "dissolved", now),
            )
            result["dissolved"] += 1
        conn.commit()
    finally:
        conn.close()
    return result


def count_canonical_convergences(
    status: Optional[str] = None, db_path: Optional[str] = None
) -> int:
    conn = connect(db_path)
    try:
        if status:
            row = conn.execute(
                "SELECT COUNT(*) FROM convergences WHERE status=?", (status,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) FROM convergences").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def get_confirmation_total(db_path: Optional[str] = None) -> int:
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(confirmation_count), 0) FROM convergences"
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def get_canonical_convergences(
    status: Optional[str] = None,
    limit: Optional[int] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    conn = connect(db_path)
    try:
        sql = "SELECT * FROM convergences"
        params: List[Any] = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY confirmation_count DESC, last_seen DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
