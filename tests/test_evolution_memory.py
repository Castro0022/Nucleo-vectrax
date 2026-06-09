"""
Tests for Evolution Memory — daily snapshots + longitudinal comparison.
Run: python -m pytest tests/test_evolution_memory.py -v
"""
import os
import sys
import sqlite3
import time
from unittest.mock import patch
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def tmp_evolution_db(tmp_path):
    """Use a temp DB and prevent record_daily_snapshot from overwriting test data."""
    db_path = str(tmp_path / "evolution_snapshots.db")
    with patch("core.self_observation.evolution_memory._DB_PATH", db_path), \
         patch("core.self_observation.evolution_memory._collect_current_state",
               return_value={"stars": 0, "gravity_stars": 0, "user_stars": 0,
                             "convergences": 0, "patterns": 0, "users": 0,
                             "interactions": 0, "facts": 0, "signals": 0,
                             "proposals": 0, "paper_trades": 0, "errors_24h": 0}):
        yield db_path


def _insert_snapshot(db_path, date, **kwargs):
    """Helper to insert a snapshot directly."""
    defaults = {
        "timestamp": time.time(), "stars": 0, "gravity_stars": 0,
        "user_stars": 0, "convergences": 0, "patterns": 0,
        "users": 0, "interactions": 0, "facts": 0,
        "signals": 0, "proposals": 0, "paper_trades": 0,
        "errors_24h": 0, "worker_restarts": 0,
    }
    defaults.update(kwargs)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS snapshots ("
        "date TEXT PRIMARY KEY, timestamp REAL, stars INTEGER DEFAULT 0,"
        "gravity_stars INTEGER DEFAULT 0, user_stars INTEGER DEFAULT 0,"
        "convergences INTEGER DEFAULT 0, patterns INTEGER DEFAULT 0,"
        "users INTEGER DEFAULT 0, interactions INTEGER DEFAULT 0,"
        "facts INTEGER DEFAULT 0, signals INTEGER DEFAULT 0,"
        "proposals INTEGER DEFAULT 0, paper_trades INTEGER DEFAULT 0,"
        "errors_24h INTEGER DEFAULT 0, worker_restarts INTEGER DEFAULT 0,"
        "extra TEXT DEFAULT '{}')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO snapshots "
        "(date,timestamp,stars,gravity_stars,user_stars,convergences,"
        "patterns,users,interactions,facts,signals,proposals,"
        "paper_trades,errors_24h,worker_restarts,extra) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'{}')",
        (date, defaults["timestamp"], defaults["stars"],
         defaults["gravity_stars"], defaults["user_stars"],
         defaults["convergences"], defaults["patterns"],
         defaults["users"], defaults["interactions"], defaults["facts"],
         defaults["signals"], defaults["proposals"],
         defaults["paper_trades"], defaults["errors_24h"],
         defaults["worker_restarts"]),
    )
    conn.commit()
    conn.close()


# ===========================================================================
# Snapshot storage
# ===========================================================================

class TestSnapshotStorage:
    def test_get_snapshot_empty(self, tmp_evolution_db):
        from core.self_observation.evolution_memory import get_snapshot
        assert get_snapshot(0) is None

    def test_get_snapshot_exists(self, tmp_evolution_db):
        from core.self_observation.evolution_memory import get_snapshot
        today = datetime.utcnow().strftime("%Y-%m-%d")
        _insert_snapshot(tmp_evolution_db, today, stars=100, users=10)
        snap = get_snapshot(0)
        assert snap is not None
        assert snap["stars"] == 100
        assert snap["users"] == 10

    def test_get_all_snapshots(self, tmp_evolution_db):
        from core.self_observation.evolution_memory import get_all_snapshots
        for i in range(5):
            date = (datetime.utcnow() - timedelta(days=i)).strftime("%Y-%m-%d")
            _insert_snapshot(tmp_evolution_db, date, stars=100 + i * 10)
        snaps = get_all_snapshots(limit=3)
        assert len(snaps) == 3
        # Newest first
        assert snaps[0]["stars"] == 100
        assert snaps[2]["stars"] == 120

    def test_snapshot_idempotent(self, tmp_evolution_db):
        from core.self_observation.evolution_memory import get_all_snapshots
        today = datetime.utcnow().strftime("%Y-%m-%d")
        _insert_snapshot(tmp_evolution_db, today, stars=50)
        _insert_snapshot(tmp_evolution_db, today, stars=60)
        snaps = get_all_snapshots()
        assert len(snaps) == 1
        assert snaps[0]["stars"] == 60  # overwritten


# ===========================================================================
# Evolution context — growth
# ===========================================================================

class TestEvolutionGrowth:
    def test_first_day_no_history(self, tmp_evolution_db):
        from core.self_observation.evolution_memory import get_evolution_context
        today = datetime.utcnow().strftime("%Y-%m-%d")
        _insert_snapshot(tmp_evolution_db, today, stars=100, users=10, convergences=5)
        ctx = get_evolution_context()
        assert "Primera observación" in ctx
        assert "100 estrellas" in ctx
        assert "10 usuarios" in ctx

    def test_growth_vs_yesterday(self, tmp_evolution_db):
        from core.self_observation.evolution_memory import get_evolution_context
        today = datetime.utcnow().strftime("%Y-%m-%d")
        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_snapshot(tmp_evolution_db, yesterday, stars=100, users=10, patterns=50)
        _insert_snapshot(tmp_evolution_db, today, stars=150, users=15, patterns=80)
        ctx = get_evolution_context()
        assert "vs ayer" in ctx
        assert "100 → 150" in ctx
        assert "+50" in ctx
        assert "+50%" in ctx
        assert "crecimiento activo" in ctx

    def test_growth_vs_week(self, tmp_evolution_db):
        from core.self_observation.evolution_memory import get_evolution_context
        today = datetime.utcnow().strftime("%Y-%m-%d")
        week_ago = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
        _insert_snapshot(tmp_evolution_db, week_ago, stars=50, convergences=2)
        _insert_snapshot(tmp_evolution_db, today, stars=200, convergences=10)
        ctx = get_evolution_context()
        assert "hace 7 días" in ctx
        assert "50 → 200" in ctx
        assert "+300%" in ctx

    def test_growth_vs_month(self, tmp_evolution_db):
        from core.self_observation.evolution_memory import get_evolution_context
        today = datetime.utcnow().strftime("%Y-%m-%d")
        month_ago = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
        _insert_snapshot(tmp_evolution_db, month_ago, stars=10)
        _insert_snapshot(tmp_evolution_db, today, stars=500)
        ctx = get_evolution_context()
        assert "hace 30 días" in ctx
        assert "10 → 500" in ctx

    def test_all_three_windows(self, tmp_evolution_db):
        from core.self_observation.evolution_memory import get_evolution_context
        today = datetime.utcnow().strftime("%Y-%m-%d")
        for days, stars in [(1, 400), (7, 200), (30, 50)]:
            date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
            _insert_snapshot(tmp_evolution_db, date, stars=stars)
        _insert_snapshot(tmp_evolution_db, today, stars=500)
        ctx = get_evolution_context()
        assert "vs ayer" in ctx
        assert "hace 7 días" in ctx
        assert "hace 30 días" in ctx


# ===========================================================================
# Evolution context — zero growth
# ===========================================================================

class TestEvolutionZeroGrowth:
    def test_no_change(self, tmp_evolution_db):
        from core.self_observation.evolution_memory import get_evolution_context
        today = datetime.utcnow().strftime("%Y-%m-%d")
        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_snapshot(tmp_evolution_db, yesterday, stars=100, users=10, convergences=5)
        _insert_snapshot(tmp_evolution_db, today, stars=100, users=10, convergences=5)
        ctx = get_evolution_context()
        assert "estable" in ctx
        # No delta lines when values are identical
        assert "→" not in ctx

    def test_partial_change(self, tmp_evolution_db):
        from core.self_observation.evolution_memory import get_evolution_context
        today = datetime.utcnow().strftime("%Y-%m-%d")
        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_snapshot(tmp_evolution_db, yesterday, stars=100, users=10, patterns=50)
        _insert_snapshot(tmp_evolution_db, today, stars=100, users=10, patterns=55)
        ctx = get_evolution_context()
        # Stars and users unchanged — no delta for them
        assert "Estrellas" not in ctx
        assert "Usuarios" not in ctx
        # Patterns changed
        assert "Patrones: 50 → 55" in ctx


# ===========================================================================
# Evolution context — negative trends
# ===========================================================================

class TestEvolutionNegativeTrends:
    def test_decline(self, tmp_evolution_db):
        from core.self_observation.evolution_memory import get_evolution_context
        today = datetime.utcnow().strftime("%Y-%m-%d")
        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_snapshot(tmp_evolution_db, yesterday, stars=200, users=20, convergences=10)
        _insert_snapshot(tmp_evolution_db, today, stars=180, users=18, convergences=8)
        ctx = get_evolution_context()
        assert "-20" in ctx
        assert "-10%" in ctx
        assert "contracción" in ctx

    def test_convergences_drop_to_zero(self, tmp_evolution_db):
        from core.self_observation.evolution_memory import get_evolution_context
        today = datetime.utcnow().strftime("%Y-%m-%d")
        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_snapshot(tmp_evolution_db, yesterday, stars=100, convergences=15)
        _insert_snapshot(tmp_evolution_db, today, stars=100, convergences=0)
        ctx = get_evolution_context()
        assert "15 → 0" in ctx
        assert "-100%" in ctx

    def test_from_zero_to_positive(self, tmp_evolution_db):
        from core.self_observation.evolution_memory import get_evolution_context
        today = datetime.utcnow().strftime("%Y-%m-%d")
        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_snapshot(tmp_evolution_db, yesterday, signals=0)
        _insert_snapshot(tmp_evolution_db, today, signals=10)
        ctx = get_evolution_context()
        assert "0 → 10" in ctx
        assert "+10" in ctx


# ===========================================================================
# Edge cases
# ===========================================================================

class TestEvolutionEdgeCases:
    def test_empty_db(self, tmp_evolution_db):
        from core.self_observation.evolution_memory import get_evolution_context
        ctx = get_evolution_context()
        # record_daily_snapshot creates a zero snapshot via mock
        assert isinstance(ctx, str)
        assert "Primera observación" in ctx or "EVOLUCIÓN" in ctx

    def test_only_old_snapshot(self, tmp_evolution_db):
        """Snapshot from 60 days ago — outside all comparison windows."""
        from core.self_observation.evolution_memory import get_evolution_context
        today = datetime.utcnow().strftime("%Y-%m-%d")
        old = (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d")
        _insert_snapshot(tmp_evolution_db, old, stars=10)
        _insert_snapshot(tmp_evolution_db, today, stars=500)
        ctx = get_evolution_context()
        # 60 days ago doesn't match 1, 7, or 30 day windows
        assert "Primera observación" in ctx or "EVOLUCIÓN" in ctx

    def test_large_numbers(self, tmp_evolution_db):
        from core.self_observation.evolution_memory import get_evolution_context
        today = datetime.utcnow().strftime("%Y-%m-%d")
        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_snapshot(tmp_evolution_db, yesterday, stars=1000000, interactions=5000000)
        _insert_snapshot(tmp_evolution_db, today, stars=1000050, interactions=5000200)
        ctx = get_evolution_context()
        assert "+50" in ctx
        assert "+200" in ctx
