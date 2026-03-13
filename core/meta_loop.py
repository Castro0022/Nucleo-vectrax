"""
Vectrax Meta-Loop
=================
Post-action reflection hook. Runs after each daemon cycle to
evaluate system behavior and record observations into cognitive state.

Reflection layers:
  1. Activity  — was something ingested this cycle?
  2. Health    — any errors accumulated?
  3. Rhythm    — cycle cadence and uptime awareness.
"""

import os
from datetime import datetime

from core import state_manager

RUNTIME_DIR = os.path.expanduser("~/.vectrax")
LOG_FILE = os.path.join(RUNTIME_DIR, "vectrax.log")


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _compute_uptime(boot_time_str):
    """Return human-readable uptime from boot_time ISO string."""
    if not boot_time_str:
        return "unknown"
    try:
        boot = datetime.fromisoformat(boot_time_str)
        delta = datetime.now() - boot
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes = remainder // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except (ValueError, TypeError):
        return "unknown"


def reflect(ingested_count=0):
    """
    Run one reflection cycle. Called after each daemon poll.

    Args:
        ingested_count: number of files ingested in this cycle.

    Returns:
        dict with reflection summary.
    """
    state = state_manager.load()

    # --- Layer 1: Activity ---
    activity = "idle"
    if ingested_count > 0:
        activity = f"active ({ingested_count} ingested)"

    # --- Layer 2: Health ---
    errors = state.get("errors_since_boot", 0)
    if errors == 0:
        health = "nominal"
    elif errors < 5:
        health = f"degraded ({errors} errors)"
    else:
        health = f"critical ({errors} errors)"

    # --- Layer 3: Rhythm ---
    cycles = state.get("cycles", 0)
    uptime = _compute_uptime(state.get("boot_time"))

    reflection = {
        "timestamp": _now_iso(),
        "activity": activity,
        "health": health,
        "cycles": cycles,
        "uptime": uptime,
    }

    # Persist reflection into state
    state["last_reflection"] = reflection
    state_manager.save(state)

    return reflection
