"""
Tests for Worker Hardening Phase 1:
  - Gap 2: Priority Queue (dequeue order, migration, priority levels)
  - Gap 3: Stage Timeouts (_stage_timer instrumentation)
  - Gap 1: Memory Watchdog (check_worker_memory trigger logic)

Run:  python -m pytest tests/test_worker_hardening.py -v
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
from unittest.mock import patch, MagicMock

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def tmp_queue_db(tmp_path):
    """Create a temporary queue DB and patch message_queue to use it."""
    db_path = str(tmp_path / "test_queue.db")
    with patch("core.transport.message_queue._QUEUE_DB", db_path):
        yield db_path


# ===========================================================================
# Gap 2: Priority Queue Tests
# ===========================================================================

class TestPriorityQueue:
    """Test that messages are dequeued in priority order."""

    def test_priority_constants_exist(self):
        from core.transport.message_queue import (
            PRIORITY_CRITICAL, PRIORITY_HIGH, PRIORITY_NORMAL, PRIORITY_LOW,
        )
        assert PRIORITY_CRITICAL < PRIORITY_HIGH < PRIORITY_NORMAL < PRIORITY_LOW
        assert PRIORITY_CRITICAL == 0
        assert PRIORITY_HIGH == 1
        assert PRIORITY_NORMAL == 2
        assert PRIORITY_LOW == 3

    def test_enqueue_with_default_priority(self, tmp_queue_db):
        from core.transport.message_queue import enqueue, PRIORITY_HIGH

        msg_id = enqueue("user1", 123, "hello", "telegram")
        assert msg_id

        # Verify the default priority in DB
        conn = sqlite3.connect(tmp_queue_db)
        row = conn.execute(
            "SELECT priority FROM queue WHERE id = ?", (msg_id,)
        ).fetchone()
        conn.close()
        assert row[0] == PRIORITY_HIGH

    def test_enqueue_with_explicit_priority(self, tmp_queue_db):
        from core.transport.message_queue import (
            enqueue, PRIORITY_CRITICAL, PRIORITY_LOW,
        )

        id_crit = enqueue("creator", 100, "urgent", priority=PRIORITY_CRITICAL)
        id_low = enqueue("bg_task", 100, "digest", priority=PRIORITY_LOW)

        conn = sqlite3.connect(tmp_queue_db)
        row_c = conn.execute(
            "SELECT priority FROM queue WHERE id = ?", (id_crit,)
        ).fetchone()
        row_l = conn.execute(
            "SELECT priority FROM queue WHERE id = ?", (id_low,)
        ).fetchone()
        conn.close()

        assert row_c[0] == PRIORITY_CRITICAL
        assert row_l[0] == PRIORITY_LOW

    def test_dequeue_respects_priority_order(self, tmp_queue_db):
        """Critical messages dequeue before high, high before low."""
        from core.transport.message_queue import (
            enqueue, dequeue,
            PRIORITY_CRITICAL, PRIORITY_HIGH, PRIORITY_LOW,
        )

        # Enqueue in reverse priority order (low first, critical last)
        enqueue("bg", 100, "low_msg", priority=PRIORITY_LOW)
        time.sleep(0.01)  # ensure different created_at
        enqueue("user", 200, "high_msg", priority=PRIORITY_HIGH)
        time.sleep(0.01)
        enqueue("creator", 300, "critical_msg", priority=PRIORITY_CRITICAL)

        # Dequeue should return critical first, then high, then low
        m1 = dequeue()
        m2 = dequeue()
        m3 = dequeue()

        assert m1.content == "critical_msg"
        assert m1.priority == PRIORITY_CRITICAL
        assert m2.content == "high_msg"
        assert m2.priority == PRIORITY_HIGH
        assert m3.content == "low_msg"
        assert m3.priority == PRIORITY_LOW

    def test_same_priority_fifo(self, tmp_queue_db):
        """Within the same priority, older messages dequeue first (FIFO)."""
        from core.transport.message_queue import enqueue, dequeue, PRIORITY_HIGH

        enqueue("u1", 100, "first", priority=PRIORITY_HIGH)
        time.sleep(0.01)
        enqueue("u2", 200, "second", priority=PRIORITY_HIGH)
        time.sleep(0.01)
        enqueue("u3", 300, "third", priority=PRIORITY_HIGH)

        m1 = dequeue()
        m2 = dequeue()
        m3 = dequeue()

        assert m1.content == "first"
        assert m2.content == "second"
        assert m3.content == "third"

    def test_dequeue_empty_returns_none(self, tmp_queue_db):
        from core.transport.message_queue import dequeue
        assert dequeue() is None

    def test_queue_message_has_priority_field(self, tmp_queue_db):
        from core.transport.message_queue import (
            enqueue, dequeue, PRIORITY_NORMAL,
        )
        enqueue("u", 100, "test", priority=PRIORITY_NORMAL)
        msg = dequeue()
        assert hasattr(msg, "priority")
        assert msg.priority == PRIORITY_NORMAL

    def test_migration_adds_priority_column(self, tmp_path):
        """Simulate an old database without priority column."""
        db_path = str(tmp_path / "legacy_queue.db")

        # Create old schema without priority
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE queue (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT 'telegram',
                status TEXT NOT NULL DEFAULT 'pending',
                response TEXT DEFAULT '',
                created_at REAL NOT NULL,
                started_at REAL DEFAULT 0,
                done_at REAL DEFAULT 0,
                error TEXT DEFAULT ''
            )
        """)
        # Insert a legacy message
        conn.execute(
            "INSERT INTO queue (id, user_id, chat_id, content, created_at) "
            "VALUES ('old1', 'u1', 100, 'legacy', ?)",
            (time.time(),),
        )
        conn.commit()
        conn.close()

        # Now open via message_queue._conn() which should migrate
        with patch("core.transport.message_queue._QUEUE_DB", db_path):
            from core.transport.message_queue import _conn
            c = _conn()
            # Check priority column exists
            cols = {r[1] for r in c.execute("PRAGMA table_info(queue)").fetchall()}
            assert "priority" in cols
            # Legacy row should have default priority=1
            row = c.execute(
                "SELECT priority FROM queue WHERE id = 'old1'"
            ).fetchone()
            assert row[0] == 1
            c.close()


# ===========================================================================
# Gap 3: Stage Timer Tests
# ===========================================================================

class TestStageTimer:
    """Test the _stage_timer context manager."""

    def test_stage_timer_logs_normal(self, caplog):
        import logging
        from core.transport.pipeline_worker import _stage_timer

        with caplog.at_level(logging.INFO, logger="vectrax.pipeline_worker"):
            with _stage_timer("test_stage", "msg123"):
                time.sleep(0.01)

        assert any("STAGE msg123 | test_stage |" in r.message for r in caplog.records)
        # Should NOT log STAGE_SLOW for a fast stage
        assert not any("STAGE_SLOW" in r.message for r in caplog.records)

    def test_stage_timer_logs_slow(self, caplog):
        import logging
        from core.transport.pipeline_worker import _stage_timer, STAGE_SLOW_THRESHOLD

        # Temporarily lower threshold for test
        with patch("core.transport.pipeline_worker.STAGE_SLOW_THRESHOLD", 0.01):
            with caplog.at_level(logging.WARNING, logger="vectrax.pipeline_worker"):
                with _stage_timer("slow_stage", "msg456"):
                    time.sleep(0.02)

        assert any("STAGE_SLOW msg456 | slow_stage" in r.message for r in caplog.records)

    def test_msg_timeout_increased(self):
        from core.transport.pipeline_worker import MSG_TIMEOUT
        assert MSG_TIMEOUT == 45, f"MSG_TIMEOUT should be 45, got {MSG_TIMEOUT}"


# ===========================================================================
# Gap 1: Memory Watchdog Tests
# ===========================================================================

class TestMemoryWatchdog:
    """Test the memory watchdog logic in scalability_guard."""

    def test_check_worker_memory_below_threshold(self):
        """When RAM is below threshold, should_restart is False."""
        from core.scalability_guard import check_worker_memory

        ram_mb, should_restart = check_worker_memory(os.getpid())
        # Current process should be well below 1200MB
        assert not should_restart
        # On both Linux and macOS, we should get a real value > 0
        assert ram_mb > 0, "Expected non-zero RAM reading from current process"

    def test_check_worker_memory_above_threshold(self, tmp_path):
        """Simulate a /proc/PID/status file with high RAM."""
        from core.scalability_guard import check_worker_memory, WORKER_RAM_MAX_MB

        fake_pid = 99999
        proc_dir = tmp_path / "proc" / str(fake_pid)
        proc_dir.mkdir(parents=True)
        status_file = proc_dir / "status"
        # Write fake VmRSS: 2000000 kB (= ~1953 MB, above 1200MB threshold)
        status_file.write_text(
            "Name:\tfake\n"
            "VmPeak:\t3000000 kB\n"
            "VmRSS:\t2000000 kB\n"
            "Threads:\t5\n"
        )

        # Patch Path to use our fake /proc
        from pathlib import Path
        with patch("core.scalability_guard.Path") as mock_path:
            fake_path = MagicMock()
            fake_path.exists.return_value = True
            fake_path.read_text.return_value = status_file.read_text()
            mock_path.return_value = fake_path

            ram_mb, should_restart = check_worker_memory(fake_pid)
            assert ram_mb > WORKER_RAM_MAX_MB
            assert should_restart

    def test_watchdog_constants_exist(self):
        from core.transport.pipeline_worker import (
            MEMORY_WATCHDOG_INTERVAL,
            MEMORY_DRAIN_TIMEOUT,
        )
        assert MEMORY_WATCHDOG_INTERVAL == 30
        assert MEMORY_DRAIN_TIMEOUT == 30

    def test_check_worker_memory_handles_nonexistent_pid(self):
        """For a non-existent PID (not self), should return (0.0, False)."""
        from core.scalability_guard import check_worker_memory
        ram_mb, should_restart = check_worker_memory(999999999)
        assert ram_mb == 0.0
        assert not should_restart

    def test_get_rss_mb_returns_nonzero(self):
        """_get_rss_mb should return a real value on both Linux and macOS."""
        from core.transport.pipeline_worker import _get_rss_mb
        rss = _get_rss_mb()
        assert rss > 0, "Expected non-zero RSS from _get_rss_mb()"


# ===========================================================================
# Integration: queue_stats with priority
# ===========================================================================

class TestQueueStatsWithPriority:
    def test_queue_stats_still_works(self, tmp_queue_db):
        from core.transport.message_queue import (
            enqueue, queue_stats, PRIORITY_CRITICAL, PRIORITY_LOW,
        )
        enqueue("u1", 100, "a", priority=PRIORITY_CRITICAL)
        enqueue("u2", 200, "b", priority=PRIORITY_LOW)
        stats = queue_stats()
        assert stats.get("pending", 0) == 2
