"""
core/scalability_guard.py — Protecciones de escalabilidad.

Prepara Vectrax para miles de usuarios:

  1. WAL mode en todas las SQLite databases (previene 'database is locked')
  2. Worker memory watchdog (auto-restart si RAM > threshold)
  3. Dynamic concurrency (escala workers según RAM disponible)
  4. Queue TTL cleanup (purga mensajes procesados >1h)

Se ejecuta al arranque del supervisor y periódicamente.

API pública:
    enforce_wal_all()          → force WAL on all databases
    check_worker_memory(pid)   → (ram_mb, should_restart)
    get_optimal_concurrency()  → int (number of workers)
    cleanup_stale_queue()      → int (rows deleted)
"""

from __future__ import annotations

import glob
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger("vectrax.scalability")

# Thresholds
WORKER_RAM_MAX_MB = int(os.environ.get("VX_WORKER_RAM_MAX_MB", "300"))
QUEUE_TTL_SECONDS = int(os.environ.get("VX_QUEUE_TTL_S", "3600"))  # 1 hour

_VAULT = os.environ.get(
    "VECTRAX_VAULT_DIR",
    os.path.join(os.path.expanduser("~"), "Vectrax", "vault"),
)
_RUNTIME = os.path.join(os.path.expanduser("~"), ".vectrax")


# ── 1. WAL Mode Enforcement ──────────────────────────────────────────

def enforce_wal_all() -> List[str]:
    """Force WAL journal mode on ALL SQLite databases.

    WAL (Write-Ahead Logging) allows concurrent reads during writes.
    Without WAL, concurrent access causes 'database is locked' errors.
    Critical for multi-user scalability.

    Returns list of databases that were changed.
    """
    changed = []
    db_paths = set()
    for pattern in [
        os.path.join(_VAULT, "*.db"),
        os.path.join(_RUNTIME, "*.db"),
    ]:
        db_paths.update(glob.glob(pattern))

    for db_path in sorted(db_paths):
        name = os.path.basename(db_path)
        if os.path.getsize(db_path) == 0:
            continue
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            current = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if current != "wal":
                conn.execute("PRAGMA journal_mode=WAL")
                changed.append(name)
                logger.info("WAL enforced: %s (%s → wal)", name, current)
            conn.close()
        except Exception as exc:
            logger.warning("WAL enforce failed for %s: %s", name, exc)

    if changed:
        logger.info("WAL enforced on %d databases: %s", len(changed), changed)
    else:
        logger.debug("All databases already in WAL mode")

    return changed


# ── 2. Worker Memory Watchdog ─────────────────────────────────────────

def check_worker_memory(pid: int) -> Tuple[float, bool]:
    """Check worker RSS memory and determine if restart is needed.

    Returns (ram_mb, should_restart).
    Prevents the 923MB memory leak from causing OOM.
    """
    ram_mb = 0.0
    try:
        status_path = Path(f"/proc/{pid}/status")
        if status_path.exists():
            for line in status_path.read_text().split("\n"):
                if line.startswith("VmRSS:"):
                    ram_mb = int(line.split()[1]) / 1024
                    break
    except Exception:
        return 0.0, False

    should_restart = ram_mb > WORKER_RAM_MAX_MB
    if should_restart:
        logger.warning(
            "MEMORY WATCHDOG | PID %d: %.0fMB > %dMB threshold — restart recommended",
            pid, ram_mb, WORKER_RAM_MAX_MB,
        )
    return ram_mb, should_restart


# ── 3. Dynamic Concurrency ───────────────────────────────────────────

def get_optimal_concurrency() -> int:
    """Calculate optimal worker thread count based on available RAM.

    Returns:
        3  if RAM < 2GB  (current default)
        6  if RAM 2-4GB
        10 if RAM 4-8GB
        15 if RAM > 8GB

    Can be overridden with VX_WORKER_CONCURRENT env var.
    """
    override = os.environ.get("VX_WORKER_CONCURRENT")
    if override:
        return int(override)

    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                    total_gb = total_kb / (1024 * 1024)
                    if total_gb >= 8:
                        return 15
                    if total_gb >= 4:
                        return 10
                    if total_gb >= 2:
                        return 6
                    return 3
    except Exception:
        pass
    return 3


# ── 4. Queue TTL Cleanup ─────────────────────────────────────────────

def cleanup_stale_queue() -> int:
    """Delete processed messages older than QUEUE_TTL_SECONDS.

    Prevents message_queue.db from growing infinitely.
    Only deletes 'done' and 'error' status messages.
    Returns number of rows deleted.
    """
    queue_db = os.path.join(_VAULT, "message_queue.db")
    if not os.path.exists(queue_db):
        return 0

    cutoff = time.time() - QUEUE_TTL_SECONDS
    try:
        conn = sqlite3.connect(queue_db, timeout=5)
        deleted = conn.execute(
            "DELETE FROM queue WHERE status IN ('done', 'error') "
            "AND created_at < ?",
            (cutoff,),
        ).rowcount
        conn.commit()
        conn.close()
        if deleted > 0:
            logger.info("Queue cleanup: %d stale messages deleted", deleted)
        return deleted
    except Exception as exc:
        logger.debug("Queue cleanup failed: %s", exc)
        return 0


# ── Startup ───────────────────────────────────────────────────────────

def run_startup_checks() -> dict:
    """Run all scalability checks at system startup.

    Called once from the supervisor or unified process.
    Returns summary dict.
    """
    results = {}

    # 1. WAL
    results["wal_changed"] = enforce_wal_all()

    # 2. Concurrency
    results["optimal_concurrency"] = get_optimal_concurrency()

    # 3. Queue cleanup
    results["queue_cleaned"] = cleanup_stale_queue()

    logger.info(
        "Scalability guard: WAL=%d changed, concurrency=%d, queue=%d cleaned",
        len(results["wal_changed"]),
        results["optimal_concurrency"],
        results["queue_cleaned"],
    )
    return results
