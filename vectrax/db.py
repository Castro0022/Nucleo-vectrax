"""
SQLite ledger for persistent storage of stars, constellations and proposals.
Database is stored at ~/.vectrax/vectrax.db
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import List, Optional

from vectrax.identity import CHANNEL_USER
from vectrax.models import (
    COLLECTIVE_OWNER,
    Constellation, MIN_MASS, Proposal, Star,
    STAR_TYPE_CONVERGENCE, STAR_TYPE_PRIMARY,
)

DB_DIR = Path.home() / ".vectrax"
DB_PATH = DB_DIR / "vectrax.db"


def _get_conn() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they do not yet exist, then run migrations."""
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS stars (
                id              TEXT PRIMARY KEY,
                content         TEXT NOT NULL,
                timestamp       REAL NOT NULL,
                embedding       BLOB,
                layer           TEXT NOT NULL DEFAULT 'outer',
                gravity_score   REAL NOT NULL DEFAULT 0.0,
                repetition_count INTEGER NOT NULL DEFAULT 1,
                success_count   INTEGER NOT NULL DEFAULT 0,
                total_count     INTEGER NOT NULL DEFAULT 1,
                channel         TEXT NOT NULL DEFAULT 'user',
                owner           TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS constellations (
                id              TEXT PRIMARY KEY,
                star_ids        TEXT NOT NULL DEFAULT '[]',
                coherence_score REAL NOT NULL DEFAULT 0.0,
                repetition_count INTEGER NOT NULL DEFAULT 1,
                success_rate    REAL NOT NULL DEFAULT 0.0,
                gravity_score   REAL NOT NULL DEFAULT 0.0,
                created_at      REAL NOT NULL,
                updated_at      REAL NOT NULL,
                channel         TEXT NOT NULL DEFAULT 'user',
                owner           TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS proposals (
                id              TEXT PRIMARY KEY,
                constellation_id TEXT NOT NULL,
                description     TEXT NOT NULL,
                evidence        TEXT NOT NULL DEFAULT '{}',
                status          TEXT NOT NULL DEFAULT 'pending',
                created_at      REAL NOT NULL,
                channel         TEXT NOT NULL DEFAULT 'creator'
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                timestamp   REAL NOT NULL,
                star_id     TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_conversations_session
                ON conversations(session_id);
            CREATE INDEX IF NOT EXISTS idx_conversations_timestamp
                ON conversations(timestamp);

            CREATE TABLE IF NOT EXISTS trajectories (
                id          TEXT PRIMARY KEY,
                star_id     TEXT NOT NULL,
                channel     TEXT NOT NULL DEFAULT 'user',
                owner       TEXT NOT NULL DEFAULT '',
                seq_num     INTEGER NOT NULL DEFAULT 0,
                timestamp   REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_traj_owner
                ON trajectories(channel, owner, timestamp);
            CREATE INDEX IF NOT EXISTS idx_traj_star
                ON trajectories(star_id);
        """)
    _migrate_db()


def _migrate_db() -> None:
    """Add new columns to existing tables without data loss."""
    migrations = [
        ("stars",         "channel TEXT NOT NULL DEFAULT 'user'"),
        ("stars",         "owner   TEXT NOT NULL DEFAULT ''"),
        ("constellations", "channel TEXT NOT NULL DEFAULT 'user'"),
        ("constellations", "owner   TEXT NOT NULL DEFAULT ''"),
        ("proposals",     "channel TEXT NOT NULL DEFAULT 'creator'"),
        # Gravitational mass fields
        ("stars",         "mass REAL NOT NULL DEFAULT 0.01"),
        ("stars",         "activation_count INTEGER NOT NULL DEFAULT 0"),
        ("stars",         "last_activated REAL NOT NULL DEFAULT 0"),
        ("stars",         "distance_to_core REAL NOT NULL DEFAULT 1.0"),
        # Star type
        ("stars",         "star_type TEXT NOT NULL DEFAULT 'primary'"),
    ]
    with _get_conn() as conn:
        for table, col_def in migrations:
            col_name = col_def.split()[0]
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
            except Exception:
                pass  # Column already exists — safe to ignore


# ---------------------------------------------------------------------------
# Stars
# ---------------------------------------------------------------------------

def insert_star(star: Star) -> None:
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO stars
               (id, content, timestamp, embedding, layer, gravity_score,
                repetition_count, success_count, total_count,
                mass, activation_count, last_activated, distance_to_core,
                star_type, channel, owner)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (star.id, star.content, star.timestamp, star.embedding,
             star.layer, star.gravity_score, star.repetition_count,
             star.success_count, star.total_count,
             star.mass, star.activation_count, star.last_activated,
             star.distance_to_core, star.star_type, star.channel, star.owner),
        )


def update_star(star: Star) -> None:
    with _get_conn() as conn:
        conn.execute(
            """UPDATE stars SET
               content=?, timestamp=?, embedding=?, layer=?,
               gravity_score=?, repetition_count=?, success_count=?, total_count=?,
               mass=?, activation_count=?, last_activated=?, distance_to_core=?
               WHERE id=?
               -- channel and owner are immutable after creation""",
            (star.content, star.timestamp, star.embedding, star.layer,
             star.gravity_score, star.repetition_count, star.success_count,
             star.total_count, star.mass, star.activation_count,
             star.last_activated, star.distance_to_core, star.id),
        )


def get_star(star_id: str) -> Optional[Star]:
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM stars WHERE id=?", (star_id,)).fetchone()
    return _row_to_star(row) if row else None


def get_all_stars(
    layer: Optional[str] = None,
    channel: Optional[str] = None,
    owner: Optional[str] = None,
) -> List[Star]:
    clauses, params = [], []
    if layer:
        clauses.append("layer=?")
        params.append(layer)
    if channel:
        clauses.append("channel=?")
        params.append(channel)
    if owner:
        clauses.append("owner=?")
        params.append(owner)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM stars {where} ORDER BY gravity_score DESC",
            params,
        ).fetchall()
    return [_row_to_star(r) for r in rows]


def _row_to_star(row: sqlite3.Row) -> Star:
    keys = row.keys()
    return Star(
        id=row["id"],
        content=row["content"],
        timestamp=row["timestamp"],
        embedding=row["embedding"],
        layer=row["layer"],
        gravity_score=row["gravity_score"],
        repetition_count=row["repetition_count"],
        success_count=row["success_count"],
        total_count=row["total_count"],
        mass=row["mass"] if "mass" in keys else MIN_MASS,
        activation_count=row["activation_count"] if "activation_count" in keys else 0,
        last_activated=row["last_activated"] if "last_activated" in keys else 0.0,
        distance_to_core=row["distance_to_core"] if "distance_to_core" in keys else 1.0,
        star_type=row["star_type"] if "star_type" in keys else STAR_TYPE_PRIMARY,
        channel=row["channel"] if "channel" in keys else CHANNEL_USER,
        owner=row["owner"] if "owner" in keys else "",
    )


def update_star_mass(star_id: str, mass: float, distance_to_core: float) -> None:
    """Update only the gravitational mass and distance fields of a star."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE stars SET mass=?, distance_to_core=? WHERE id=?",
            (mass, distance_to_core, star_id),
        )


def activate_star(star_id: str) -> None:
    """Increment activation_count and update last_activated timestamp."""
    import time as _time
    with _get_conn() as conn:
        conn.execute(
            """UPDATE stars SET
               activation_count = activation_count + 1,
               last_activated = ?
               WHERE id = ?""",
            (_time.time(), star_id),
        )


# ---------------------------------------------------------------------------
# Trajectories
# ---------------------------------------------------------------------------

def insert_trajectory_point(
    star_id: str,
    channel: str,
    owner: str,
) -> str:
    """Record a trajectory point. Returns the point ID."""
    import uuid
    import time as _time
    point_id = str(uuid.uuid4())
    # Get next sequence number for this user
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(seq_num) as mx FROM trajectories WHERE channel=? AND owner=?",
            (channel, owner),
        ).fetchone()
        next_seq = (row["mx"] or 0) + 1
        conn.execute(
            """INSERT INTO trajectories (id, star_id, channel, owner, seq_num, timestamp)
               VALUES (?,?,?,?,?,?)""",
            (point_id, star_id, channel, owner, next_seq, _time.time()),
        )
    return point_id


def get_trajectory(
    channel: str,
    owner: str,
    limit: int = 50,
) -> List[dict]:
    """Return the most recent trajectory points for a user, ordered by sequence."""
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT t.*, s.content, s.star_type, s.mass, s.distance_to_core, s.layer
               FROM trajectories t
               JOIN stars s ON t.star_id = s.id
               WHERE t.channel=? AND t.owner=?
               ORDER BY t.seq_num DESC LIMIT ?""",
            (channel, owner, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_trajectory_star_ids(
    channel: str,
    owner: str,
    limit: int = 50,
) -> List[str]:
    """Return recent trajectory star IDs for a user."""
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT star_id FROM trajectories
               WHERE channel=? AND owner=?
               ORDER BY seq_num DESC LIMIT ?""",
            (channel, owner, limit),
        ).fetchall()
    return [r["star_id"] for r in reversed(rows)]


# ---------------------------------------------------------------------------
# Convergence star queries
# ---------------------------------------------------------------------------

def get_convergence_stars(
    channel: Optional[str] = None,
    owner: Optional[str] = None,
) -> List[Star]:
    """Return all convergence-type stars, ordered by distance to core (ascending)."""
    clauses = ["star_type = ?"]
    params: list = [STAR_TYPE_CONVERGENCE]
    if channel:
        clauses.append("channel=?")
        params.append(channel)
    if owner:
        clauses.append("owner=?")
        params.append(owner)
    where = "WHERE " + " AND ".join(clauses)
    with _get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM stars {where} ORDER BY distance_to_core ASC",
            params,
        ).fetchall()
    return [_row_to_star(r) for r in rows]


def get_collective_convergence_stars(
    channel: Optional[str] = None,
) -> List[Star]:
    """Return collective convergence stars (owner=__collective__), ordered by distance."""
    clauses = ["star_type = ?", "owner = ?"]
    params: list = [STAR_TYPE_CONVERGENCE, COLLECTIVE_OWNER]
    if channel:
        clauses.append("channel=?")
        params.append(channel)
    where = "WHERE " + " AND ".join(clauses)
    with _get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM stars {where} ORDER BY distance_to_core ASC",
            params,
        ).fetchall()
    return [_row_to_star(r) for r in rows]


def get_stars_near_core(max_distance: float = 0.3) -> List[Star]:
    """Return stars whose distance_to_core <= max_distance."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM stars WHERE distance_to_core <= ? ORDER BY distance_to_core ASC",
            (max_distance,),
        ).fetchall()
    return [_row_to_star(r) for r in rows]


def get_stars_in_periphery(min_distance: float = 0.8) -> List[Star]:
    """Return stars whose distance_to_core >= min_distance."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM stars WHERE distance_to_core >= ? ORDER BY distance_to_core DESC",
            (min_distance,),
        ).fetchall()
    return [_row_to_star(r) for r in rows]


# ---------------------------------------------------------------------------
# Constellations
# ---------------------------------------------------------------------------

def upsert_constellation(c: Constellation) -> None:
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO constellations
               (id, star_ids, coherence_score, repetition_count, success_rate,
                gravity_score, created_at, updated_at, channel, owner)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
               star_ids=excluded.star_ids,
               coherence_score=excluded.coherence_score,
               repetition_count=excluded.repetition_count,
               success_rate=excluded.success_rate,
               gravity_score=excluded.gravity_score,
               updated_at=excluded.updated_at""",
            (c.id, json.dumps(c.star_ids), c.coherence_score, c.repetition_count,
             c.success_rate, c.gravity_score, c.created_at, c.updated_at,
             c.channel, c.owner),
        )


def get_all_constellations(
    channel: Optional[str] = None,
    owner: Optional[str] = None,
) -> List[Constellation]:
    clauses, params = [], []
    if channel:
        clauses.append("channel=?")
        params.append(channel)
    if owner:
        clauses.append("owner=?")
        params.append(owner)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM constellations {where} ORDER BY gravity_score DESC",
            params,
        ).fetchall()
    return [_row_to_constellation(r) for r in rows]


def get_constellation(cid: str) -> Optional[Constellation]:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM constellations WHERE id=?", (cid,)
        ).fetchone()
    return _row_to_constellation(row) if row else None


def _row_to_constellation(row: sqlite3.Row) -> Constellation:
    keys = row.keys()
    return Constellation(
        id=row["id"],
        star_ids=json.loads(row["star_ids"]),
        coherence_score=row["coherence_score"],
        repetition_count=row["repetition_count"],
        success_rate=row["success_rate"],
        gravity_score=row["gravity_score"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        channel=row["channel"] if "channel" in keys else CHANNEL_USER,
        owner=row["owner"] if "owner" in keys else "",
    )


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------

def insert_proposal(p: Proposal) -> None:
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO proposals
               (id, constellation_id, description, evidence, status, created_at)
               VALUES (?,?,?,?,?,?)""",
            (p.id, p.constellation_id, p.description,
             json.dumps(p.evidence), p.status, p.created_at),
        )


def update_proposal_status(proposal_id: str, status: str) -> None:
    with _get_conn() as conn:
        conn.execute(
            "UPDATE proposals SET status=? WHERE id=?", (status, proposal_id)
        )


def get_proposals(status: Optional[str] = None) -> List[Proposal]:
    with _get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM proposals WHERE status=? ORDER BY created_at DESC",
                (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM proposals ORDER BY created_at DESC"
            ).fetchall()
    return [_row_to_proposal(r) for r in rows]


def get_proposal(pid: str) -> Optional[Proposal]:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM proposals WHERE id=?", (pid,)
        ).fetchone()
    return _row_to_proposal(row) if row else None


def _row_to_proposal(row: sqlite3.Row) -> Proposal:
    return Proposal(
        id=row["id"],
        constellation_id=row["constellation_id"],
        description=row["description"],
        evidence=json.loads(row["evidence"]),
        status=row["status"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def get_counts() -> dict:
    with _get_conn() as conn:
        stars = conn.execute("SELECT COUNT(*) FROM stars").fetchone()[0]
        constellations = conn.execute(
            "SELECT COUNT(*) FROM constellations"
        ).fetchone()[0]
        proposals = conn.execute(
            "SELECT COUNT(*) FROM proposals WHERE status='pending'"
        ).fetchone()[0]
        layer_counts = {
            row["layer"]: row["cnt"]
            for row in conn.execute(
                "SELECT layer, COUNT(*) as cnt FROM stars GROUP BY layer"
            ).fetchall()
        }
    return {
        "stars": stars,
        "constellations": constellations,
        "pending_proposals": proposals,
        "layers": layer_counts,
    }


# ---------------------------------------------------------------------------
# Conversations  (motor de memoria conversacional)
# ---------------------------------------------------------------------------

def insert_message(
    session_id: str,
    role: str,
    content: str,
    star_id: Optional[str] = None,
) -> str:
    """Persist a single conversation turn. Returns the message id."""
    import uuid, time as _time
    msg_id = str(uuid.uuid4())
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO conversations
               (id, session_id, role, content, timestamp, star_id)
               VALUES (?,?,?,?,?,?)""",
            (msg_id, session_id, role, content, _time.time(), star_id),
        )
    return msg_id


def get_session_messages(session_id: str) -> List[dict]:
    """Return all messages of a session ordered by time."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM conversations WHERE session_id=? ORDER BY timestamp ASC",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent_messages(limit: int = 20) -> List[dict]:
    """Return the most recent messages across all sessions."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM conversations ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_sessions() -> List[dict]:
    """Return all sessions with message count and time range."""
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT session_id,
                   COUNT(*) as message_count,
                   MIN(timestamp) as started_at,
                   MAX(timestamp) as last_at
            FROM conversations
            GROUP BY session_id
            ORDER BY last_at DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_last_session() -> Optional[dict]:
    """Return metadata of the most recent session, or None."""
    sessions = get_sessions()
    return sessions[0] if sessions else None


# ---------------------------------------------------------------------------
# Resolution Learning Log
# ---------------------------------------------------------------------------

def _ensure_resolution_log() -> None:
    """Create the resolution_log table if it doesn't exist."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS resolution_log (
                id              TEXT PRIMARY KEY,
                question        TEXT NOT NULL,
                resolve_pattern TEXT NOT NULL,
                engines_used    TEXT NOT NULL DEFAULT '[]',
                consensus       TEXT NOT NULL DEFAULT '',
                useful          INTEGER NOT NULL DEFAULT 0,
                timestamp       REAL NOT NULL,
                channel         TEXT NOT NULL DEFAULT 'user',
                owner           TEXT NOT NULL DEFAULT ''
            )
        """)


def log_resolution(
    question: str,
    resolve_pattern: str,
    engines_used: List[str],
    consensus: str,
    channel: str,
    owner: str,
) -> str:
    """Log a resolution pattern for learning. Returns the log entry ID."""
    import uuid
    import time as _time
    _ensure_resolution_log()
    entry_id = str(uuid.uuid4())
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO resolution_log
               (id, question, resolve_pattern, engines_used, consensus,
                useful, timestamp, channel, owner)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (entry_id, question, resolve_pattern,
             json.dumps(engines_used), consensus,
             0, _time.time(), channel, owner),
        )
    return entry_id


def get_resolution_stats(
    channel: Optional[str] = None,
    owner: Optional[str] = None,
) -> dict:
    """Return aggregate stats from the resolution log."""
    _ensure_resolution_log()
    clauses, params = [], []
    if channel:
        clauses.append("channel=?")
        params.append(channel)
    if owner:
        clauses.append("owner=?")
        params.append(owner)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM resolution_log {where}", params
        ).fetchone()[0]
        patterns = {}
        for row in conn.execute(
            f"SELECT resolve_pattern, COUNT(*) as cnt FROM resolution_log {where} GROUP BY resolve_pattern",
            params,
        ).fetchall():
            patterns[row[0]] = row[1]
    return {"total": total, "patterns": patterns}
