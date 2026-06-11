"""
Vectrax — Convergence History
================================
Persistent record of every convergence event: birth, evolution, dissolution.

Converts computed convergences into remembered events with timestamps,
participants, scores, and lifecycle status.

Storage: vault/convergence_history.db (SQLite WAL)
API:
  record_snapshot(convergences)  → compare vs stored, record births/deaths
  get_recent(limit)              → last N events (births + deaths)
  get_active()                   → currently active convergences
  get_timeline(days)             → full history for a time window
  build_context()                → LLM-injectable summary

Creado: 2026-06-11
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.convergence_history")

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "vault", "convergence_history.db",
)

_CREATE = """
CREATE TABLE IF NOT EXISTS convergence_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    key          TEXT NOT NULL,
    timestamp    REAL NOT NULL,
    event        TEXT NOT NULL,
    star_a       TEXT NOT NULL,
    star_b       TEXT NOT NULL,
    type         TEXT DEFAULT '',
    intent       TEXT DEFAULT '',
    domains      TEXT DEFAULT '[]',
    combined_cc  REAL DEFAULT 0,
    combined_hits INTEGER DEFAULT 0,
    status       TEXT DEFAULT 'active',
    dissolved_at REAL DEFAULT 0,
    extra        TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_conv_key ON convergence_events(key);
CREATE INDEX IF NOT EXISTS idx_conv_event ON convergence_events(event);
CREATE INDEX IF NOT EXISTS idx_conv_ts ON convergence_events(timestamp);
"""


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=3)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    conn.executescript(_CREATE)
    return conn


def _make_key(star_a: str, star_b: str) -> str:
    """Stable key for a convergence pair (order-independent)."""
    return ":".join(sorted([star_a, star_b]))


# ---------------------------------------------------------------------------
# Core: snapshot comparison
# ---------------------------------------------------------------------------

def record_snapshot(convergences: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compare current convergences against stored active ones.

    Records births (new) and dissolutions (disappeared).
    Updates scores for existing ones.

    Returns: {"births": N, "dissolutions": N, "active": N}
    """
    now = time.time()
    conn = _conn()
    try:
        # Load currently active from DB
        active_rows = conn.execute(
            "SELECT key, combined_cc, combined_hits FROM convergence_events "
            "WHERE status = 'active' GROUP BY key "
            "HAVING id = MAX(id)"
        ).fetchall()
        active_keys = {r["key"]: dict(r) for r in active_rows}

        # Current convergences from gravity engine
        current_keys = {}
        for c in convergences:
            key = _make_key(c.get("star_a", ""), c.get("star_b", ""))
            current_keys[key] = c

        births = 0
        dissolutions = 0

        # Detect births (in current but not in active)
        for key, c in current_keys.items():
            if key not in active_keys:
                conn.execute(
                    "INSERT INTO convergence_events "
                    "(key, timestamp, event, star_a, star_b, type, intent, "
                    " domains, combined_cc, combined_hits, status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        key, now, "birth",
                        c.get("star_a", ""), c.get("star_b", ""),
                        c.get("type", ""), c.get("intent", c.get("a_intent", "")),
                        json.dumps(c.get("domains", [])),
                        c.get("combined_cc", 0), c.get("combined_hits", 0),
                        "active",
                    ),
                )
                births += 1
                logger.info(
                    "CONVERGENCE_BIRTH | %s | %s ↔ %s | cc=%.3f hits=%d",
                    c.get("intent", "?"), c.get("star_a", "")[:20],
                    c.get("star_b", "")[:20], c.get("combined_cc", 0),
                    c.get("combined_hits", 0),
                )

        # Detect dissolutions (in active but not in current)
        for key in active_keys:
            if key not in current_keys:
                conn.execute(
                    "INSERT INTO convergence_events "
                    "(key, timestamp, event, star_a, star_b, status, dissolved_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (key, now, "dissolution", key.split(":")[0],
                     key.split(":")[-1], "dissolved", now),
                )
                # Mark all active rows for this key as dissolved
                conn.execute(
                    "UPDATE convergence_events SET status='dissolved', dissolved_at=? "
                    "WHERE key=? AND status='active'",
                    (now, key),
                )
                dissolutions += 1
                logger.info("CONVERGENCE_DISSOLVED | %s", key)

        conn.commit()
        active_count = len(current_keys)
        return {"births": births, "dissolutions": dissolutions, "active": active_count}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def get_recent(limit: int = 10) -> List[Dict[str, Any]]:
    """Last N convergence events (births + dissolutions), newest first."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM convergence_events ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_active() -> List[Dict[str, Any]]:
    """Currently active convergences (latest record per key)."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM convergence_events "
            "WHERE status='active' AND event='birth' "
            "GROUP BY key HAVING id = MAX(id) "
            "ORDER BY combined_cc DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_timeline(days: int = 7) -> List[Dict[str, Any]]:
    """All events within a time window."""
    cutoff = time.time() - days * 86400
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM convergence_events WHERE timestamp > ? "
            "ORDER BY timestamp DESC",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_events() -> Dict[str, int]:
    """Count events by type."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT event, COUNT(*) as c FROM convergence_events GROUP BY event"
        ).fetchall()
        return {r["event"]: r["c"] for r in rows}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# LLM context
# ---------------------------------------------------------------------------

def build_context(limit: int = 5) -> str:
    """Build injectable context for the LLM about convergence history."""
    events = get_recent(limit=limit)
    if not events:
        return ""

    active = get_active()
    counts = count_events()

    lines = [
        f"[HISTORIAL DE CONVERGENCIAS — {counts.get('birth', 0)} nacimientos, "
        f"{counts.get('dissolution', 0)} disoluciones]",
    ]

    if active:
        lines.append(f"Activas ahora: {len(active)}")
        for a in active[:3]:
            ts = datetime.fromtimestamp(a["timestamp"]).strftime("%m/%d %H:%M")
            lines.append(
                f"  {a['intent'] or '?'} | {a['star_a'][:15]}↔{a['star_b'][:15]} | "
                f"cc={a['combined_cc']:.2f} hits={a['combined_hits']} | desde {ts}"
            )

    recent_births = [e for e in events if e["event"] == "birth"]
    recent_deaths = [e for e in events if e["event"] == "dissolution"]

    if recent_births:
        lines.append("Últimos nacimientos:")
        for b in recent_births[:3]:
            ts = datetime.fromtimestamp(b["timestamp"]).strftime("%m/%d %H:%M")
            lines.append(
                f"  {ts} | {b['intent'] or '?'} | "
                f"{b['star_a'][:15]}↔{b['star_b'][:15]} | cc={b['combined_cc']:.2f}"
            )

    if recent_deaths:
        lines.append("Últimas disoluciones:")
        for d in recent_deaths[:2]:
            ts = datetime.fromtimestamp(d["timestamp"]).strftime("%m/%d %H:%M")
            lines.append(f"  {ts} | {d['key']}")

    return "\n".join(lines)
