"""
Vectrax configuration — persistent state at ~/.vectrax/config.json

Stores:
  - observer_active:   whether permanent observer mode is running
  - wake_word_hash:    SHA-256 + salt hash of the secret wake word (never plain)
  - wake_word_salt:    random salt for the hash
  - creator_identity:  full name of the creator
  - version:           config schema version
  - activated_at:      timestamp of last wake word activation

Nothing in this file reveals the plain-text wake word.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

CONFIG_DIR = Path.home() / ".vectrax"
CONFIG_PATH = CONFIG_DIR / "config.json"

_DEFAULTS: dict = {
    "version": 1,
    "creator_identity": "Mario Bravo Castro",
    "observer_active": False,
    "wake_word_hash": None,
    "wake_word_salt": None,
    "activated_at": None,
    "observer_started_at": None,
    "total_events_observed": 0,
}


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------

def load() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        _write(_DEFAULTS.copy())
    raw = CONFIG_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    # Backfill any missing keys from defaults
    changed = False
    for k, v in _DEFAULTS.items():
        if k not in data:
            data[k] = v
            changed = True
    if changed:
        _write(data)
    return data


def _write(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)


def set(key: str, value: Any) -> None:
    data = load()
    data[key] = value
    _write(data)


def update(patches: dict) -> None:
    data = load()
    data.update(patches)
    _write(data)


# ---------------------------------------------------------------------------
# Convenience accessors
# ---------------------------------------------------------------------------

def is_observer_active() -> bool:
    return bool(get("observer_active", False))


def activate_observer() -> None:
    update({
        "observer_active": True,
        "observer_started_at": time.time(),
    })


def deactivate_observer() -> None:
    set("observer_active", False)


def record_activation(timestamp: Optional[float] = None) -> None:
    """Record a wake word activation event."""
    set("activated_at", timestamp or time.time())


def increment_events(count: int = 1) -> None:
    data = load()
    data["total_events_observed"] = data.get("total_events_observed", 0) + count
    _write(data)


def observer_summary() -> dict:
    data = load()
    return {
        "active": data.get("observer_active", False),
        "creator": data.get("creator_identity", ""),
        "started_at": data.get("observer_started_at"),
        "activated_at": data.get("activated_at"),
        "total_events": data.get("total_events_observed", 0),
        "wake_word_configured": data.get("wake_word_hash") is not None,
    }
