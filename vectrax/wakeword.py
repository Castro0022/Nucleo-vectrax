"""
Vectrax Wake Word — secret activation mechanism.

Design principles:
  - The wake word is NEVER stored in plain text, only as a salted SHA-256 hash
  - Only the creator (Mario) can set or trigger the wake word
  - The hash is stored at ~/.vectrax/config.json — source code reveals nothing
  - A wake event in the creator channel triggers observer mode activation
  - The word cannot be imitated without knowing it: hash collisions are computationally infeasible
  - Input is always read with no echo (getpass) — invisible to terminal history

Activation flow:
  1. Creator runs `vectrax creator wake --set` → sets hash silently
  2. When `vectrax creator ingest "<text>"` is called and the text contains
     the wake word, detect_in_text() matches the hash → activation event fires
  3. Observer mode is marked active and a foundational star is created
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Optional

from vectrax import config as cfg


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------

def _hash(plain: str, salt: str) -> str:
    """Return a hex SHA-256 PBKDF2 digest of the wake word."""
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        plain.encode("utf-8"),
        salt.encode("utf-8"),
        iterations=200_000,
    )
    return dk.hex()


def _new_salt() -> str:
    return os.urandom(32).hex()


# ---------------------------------------------------------------------------
# Set / verify
# ---------------------------------------------------------------------------

def set_wake_word(plain: str) -> None:
    """
    Hash the wake word and store only the hash + salt.
    The plain word is never written anywhere — it is discarded after hashing.
    """
    salt = _new_salt()
    digest = _hash(plain, salt)
    cfg.update({
        "wake_word_hash": digest,
        "wake_word_salt": salt,
    })
    # Explicitly zero-out the reference (best-effort in CPython)
    plain = "\x00" * len(plain)


def verify_wake_word(candidate: str) -> bool:
    """Return True if the candidate matches the stored hash."""
    data = cfg.load()
    stored_hash = data.get("wake_word_hash")
    stored_salt = data.get("wake_word_salt")
    if not stored_hash or not stored_salt:
        return False
    candidate_hash = _hash(candidate, stored_salt)
    # Constant-time comparison to prevent timing attacks
    return hashlib.compare_digest(candidate_hash, stored_hash)


def is_configured() -> bool:
    return cfg.get("wake_word_hash") is not None


# ---------------------------------------------------------------------------
# Detection in ingested text (creator channel only)
# ---------------------------------------------------------------------------

def detect_in_text(text: str, channel: str) -> bool:
    """
    Scan ingested text for the wake word (creator channel only).
    Each token in the text is checked; returns True on first match.
    The plain text tokens are hashed transiently — never stored.
    """
    from vectrax.identity import CHANNEL_CREATOR
    if channel != CHANNEL_CREATOR:
        return False
    if not is_configured():
        return False

    data = cfg.load()
    salt = data["wake_word_salt"]
    stored = data["wake_word_hash"]

    # Check full text and each whitespace-separated token
    candidates = [text.strip()] + text.strip().split()
    for token in candidates:
        if not token:
            continue
        token_hash = _hash(token, salt)
        if hashlib.compare_digest(token_hash, stored):
            return True
    return False


# ---------------------------------------------------------------------------
# Activation event
# ---------------------------------------------------------------------------

def trigger_activation(source: str = "creator") -> dict:
    """
    Record a wake word activation and return a summary dict.
    Creates a foundational star marking the activation moment.
    """
    now = time.time()
    cfg.record_activation(now)
    cfg.activate_observer()

    return {
        "activated": True,
        "source": source,
        "timestamp": now,
    }


def activation_status() -> dict:
    summary = cfg.observer_summary()
    return {
        "wake_word_configured": summary["wake_word_configured"],
        "observer_active": summary["active"],
        "last_activation": summary["activated_at"],
        "total_events_observed": summary["total_events"],
    }
