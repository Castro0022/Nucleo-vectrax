"""
Vectrax State Manager
=====================
Persistent cognitive state stored as JSON.
Survives daemon restarts. Thread-safe writes via atomic rename.
State file: ~/.vectrax/cognition_state.json
"""

import json
import os
import tempfile
from datetime import datetime

RUNTIME_DIR = os.path.expanduser("~/.vectrax")
STATE_PATH = os.path.join(RUNTIME_DIR, "cognition_state.json")

DEFAULT_STATE = {
    "boot_time": None,
    "cycles": 0,
    "ingests_total": 0,
    "last_cycle_at": None,
    "last_ingest_at": None,
    "last_reflection": None,
    "errors_since_boot": 0,
    # Governor / policy fields
    "governor_mode": "observe",
    "governor_reason": "",
    "governor_decided_at": None,
    "governor_autopatch_allowed": False,
    "governor_risk_score": None,
    "governor_risk_level": None,
    "error_history": [],
    "clean_streak": 0,
    # Risk engine baselines (EMA)
    "risk_baselines": {},
    # Shadow mode
    "shadow_mode": {},
    "meta": {},
    # Architecture C: operational mode preference
    "operational_mode": "HOME_AUTO",
    # Active Learning subsystem
    "learning_mode": "OBSERVATION",
    "learning_activated_at": None,
    "hypotheses_generated": 0,
    "rules_learned": 0,
    "learning_cycles": 0,
    # Total Convergence
    "convergence_mode": "INACTIVE",
    "convergence_activated_at": None,
    # Presencia Pura
    "nucleus_mode": "STANDARD",
    "nucleus_mode_activated_at": None,
    "nucleus_mode_activated_by": None,
    # PresenciaObserver
    "observer_mode": "OBSERVER",
    "observer_mode_activated_at": None,
    "observer_mode_activated_by": None,
}


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def load():
    """Load cognitive state from disk. Returns default if missing/corrupt."""
    if not os.path.isfile(STATE_PATH):
        return dict(DEFAULT_STATE)
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Merge with defaults to fill any missing keys
        merged = dict(DEFAULT_STATE)
        merged.update(data)
        return merged
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_STATE)


def save(state):
    """Atomically write state to disk."""
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=RUNTIME_DIR, prefix="state_", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, STATE_PATH)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get(key, default=None):
    """Read a single key from state."""
    return load().get(key, default)


def put(key, value):
    """Write a single key to state."""
    state = load()
    state[key] = value
    save(state)


def update(patch):
    """Merge a dict into state."""
    state = load()
    state.update(patch)
    save(state)


def increment(key, amount=1):
    """Increment a numeric field."""
    state = load()
    state[key] = state.get(key, 0) + amount
    save(state)


def record_boot():
    """Mark daemon boot in state."""
    state = load()
    state["boot_time"] = _now_iso()
    state["cycles"] = 0
    state["errors_since_boot"] = 0
    # Reset governor to observe on fresh boot
    state["governor_mode"] = "observe"
    state["governor_reason"] = "Boot — stabilizing"
    state["governor_autopatch_allowed"] = False
    state["error_history"] = []
    state["clean_streak"] = 0
    save(state)


def record_cycle():
    """Mark one daemon poll cycle."""
    state = load()
    state["cycles"] = state.get("cycles", 0) + 1
    state["last_cycle_at"] = _now_iso()
    save(state)


def record_ingest():
    """Mark one file ingested."""
    state = load()
    state["ingests_total"] = state.get("ingests_total", 0) + 1
    state["last_ingest_at"] = _now_iso()
    save(state)


def record_error():
    """Increment error counter."""
    increment("errors_since_boot")
