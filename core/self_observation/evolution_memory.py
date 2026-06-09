"""
Vectrax — Evolution Memory
=============================
Daily snapshots of the system state for longitudinal comparison.

When Vectrax observes itself, it doesn't just see the current state —
it remembers where it was yesterday, last week, and last month.

Storage: vault/evolution_snapshots.db (SQLite, one row per day)
API:
  record_daily_snapshot()     → stores today's state (idempotent)
  get_evolution_context()     → comparison string for LLM injection
  get_snapshot(days_ago)      → raw snapshot dict

Creado: 2026-06-09
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.evolution_memory")

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "vault", "evolution_snapshots.db",
)

_CREATE = """
CREATE TABLE IF NOT EXISTS snapshots (
    date        TEXT PRIMARY KEY,
    timestamp   REAL NOT NULL,
    stars       INTEGER DEFAULT 0,
    gravity_stars INTEGER DEFAULT 0,
    user_stars  INTEGER DEFAULT 0,
    convergences INTEGER DEFAULT 0,
    patterns    INTEGER DEFAULT 0,
    users       INTEGER DEFAULT 0,
    interactions INTEGER DEFAULT 0,
    facts       INTEGER DEFAULT 0,
    signals     INTEGER DEFAULT 0,
    proposals   INTEGER DEFAULT 0,
    paper_trades INTEGER DEFAULT 0,
    errors_24h  INTEGER DEFAULT 0,
    worker_restarts INTEGER DEFAULT 0,
    extra       TEXT DEFAULT '{}'
);
"""


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=3)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    conn.executescript(_CREATE)
    return conn


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------

def record_daily_snapshot() -> Dict[str, Any]:
    """Capture today's state. Idempotent — overwrites if already exists."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    snap = _collect_current_state()
    snap["date"] = today
    snap["timestamp"] = time.time()

    conn = _conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO snapshots "
            "(date, timestamp, stars, gravity_stars, user_stars, convergences, "
            " patterns, users, interactions, facts, signals, proposals, "
            " paper_trades, errors_24h, worker_restarts, extra) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                snap["date"], snap["timestamp"],
                snap.get("stars", 0), snap.get("gravity_stars", 0),
                snap.get("user_stars", 0), snap.get("convergences", 0),
                snap.get("patterns", 0), snap.get("users", 0),
                snap.get("interactions", 0), snap.get("facts", 0),
                snap.get("signals", 0), snap.get("proposals", 0),
                snap.get("paper_trades", 0), snap.get("errors_24h", 0),
                snap.get("worker_restarts", 0),
                json.dumps(snap.get("extra", {})),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info("Evolution snapshot recorded: %s | stars=%d users=%d",
                today, snap.get("stars", 0), snap.get("users", 0))
    return snap


def _collect_current_state() -> Dict[str, Any]:
    """Gather current metrics from all sources."""
    state: Dict[str, Any] = {}

    # Universe stats
    try:
        from core.self_observation.universe_observer import observe_universe
        snap = observe_universe()
        state["stars"] = snap.gravity_total + snap.star_count
        state["gravity_stars"] = snap.gravity_total
        state["user_stars"] = snap.star_count
        state["convergences"] = len(snap.gravity_convergences)
        state["patterns"] = snap.pattern_count
    except Exception:
        state["stars"] = 0

    # User stats
    try:
        _db = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "vault", "user_memory.db",
        )
        conn = sqlite3.connect(_db, timeout=2)
        state["users"] = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM profiles WHERE user_id NOT LIKE 'test:%'"
        ).fetchone()[0]
        state["interactions"] = conn.execute(
            "SELECT COUNT(*) FROM interactions"
        ).fetchone()[0]
        try:
            state["facts"] = conn.execute("SELECT COUNT(*) FROM user_facts").fetchone()[0]
        except Exception:
            state["facts"] = 0
        conn.close()
    except Exception:
        pass

    # Market signals
    try:
        from connectors.etoro.signal_recorder import get_signal_stats
        stats = get_signal_stats()
        state["signals"] = stats.get("total", 0)
    except Exception:
        state["signals"] = 0

    # Proposals
    try:
        from connectors.etoro.learning_engine import load_proposals
        state["proposals"] = len(load_proposals(limit=1000))
    except Exception:
        state["proposals"] = 0

    # Paper trades
    try:
        from connectors.etoro.auto_executor import get_config
        cfg = get_config()
        state["paper_trades"] = cfg.get("paper_trades_total", 0)
    except Exception:
        state["paper_trades"] = 0

    # Errors
    try:
        from core.self_observation.state_collector import _error_count_window
        state["errors_24h"] = _error_count_window(86400)
    except Exception:
        state["errors_24h"] = 0

    return state


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def get_snapshot(days_ago: int = 0) -> Optional[Dict[str, Any]]:
    """Get snapshot for a specific day. 0 = today, 1 = yesterday, etc."""
    target = (datetime.utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM snapshots WHERE date = ?", (target,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_snapshots(limit: int = 30) -> List[Dict[str, Any]]:
    """Get the most recent snapshots, newest first."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM snapshots ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Evolution context for LLM injection
# ---------------------------------------------------------------------------

def get_evolution_context() -> str:
    """Build a comparison string showing how the system has evolved.

    Compares today vs yesterday, vs 7 days ago, vs 30 days ago.
    Returns empty string if no historical data exists.
    """
    # Ensure today is recorded (only if missing — never overwrite)
    today = get_snapshot(0)
    if not today:
        try:
            record_daily_snapshot()
            today = get_snapshot(0)
        except Exception:
            pass
    if not today:
        return ""

    yesterday = get_snapshot(1)
    week_ago = get_snapshot(7)
    month_ago = get_snapshot(30)

    lines = ["[EVOLUCIÓN DEL SISTEMA]"]

    # Pick the best comparison available
    comparisons = []
    if yesterday:
        comparisons.append(("ayer", yesterday))
    if week_ago:
        comparisons.append(("hace 7 días", week_ago))
    if month_ago:
        comparisons.append(("hace 30 días", month_ago))

    if not comparisons:
        lines.append("Primera observación registrada hoy. Sin historial para comparar.")
        lines.append(f"Estado inicial: {today.get('stars', 0)} estrellas, "
                     f"{today.get('users', 0)} usuarios, "
                     f"{today.get('convergences', 0)} convergencias.")
        return "\n".join(lines)

    def _delta(key, ref, label):
        now_val = today.get(key, 0)
        then_val = ref.get(key, 0)
        diff = now_val - then_val
        if diff == 0:
            return None
        pct = round(diff / max(then_val, 1) * 100) if then_val else 0
        sign = "+" if diff > 0 else ""
        return f"{label}: {then_val} → {now_val} ({sign}{diff}, {sign}{pct}%)"

    for period_name, ref in comparisons:
        changes = []
        for key, label in [
            ("stars", "Estrellas"),
            ("convergences", "Convergencias"),
            ("users", "Usuarios"),
            ("interactions", "Interacciones"),
            ("patterns", "Patrones"),
            ("signals", "Señales mercado"),
            ("facts", "Facts"),
        ]:
            d = _delta(key, ref, label)
            if d:
                changes.append(f"  {d}")

        if changes:
            lines.append(f"vs {period_name} ({ref.get('date', '?')}):")
            lines.extend(changes)

    # Growth narrative (most recent comparison)
    _, best_ref = comparisons[0]
    star_growth = today.get("stars", 0) - best_ref.get("stars", 0)
    user_growth = today.get("users", 0) - best_ref.get("users", 0)
    conv_growth = today.get("convergences", 0) - best_ref.get("convergences", 0)

    if star_growth > 0 or user_growth > 0 or conv_growth > 0:
        lines.append("Tendencia: crecimiento activo.")
    elif star_growth == 0 and user_growth == 0:
        lines.append("Tendencia: estable, sin crecimiento visible.")
    else:
        lines.append("Tendencia: contracción — investigar.")

    return "\n".join(lines)
