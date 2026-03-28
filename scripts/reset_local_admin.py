#!/usr/bin/env python3
"""
Vectrax — Reset Local Admin Password
======================================
Resets the 'mario' (owner) password in the local SQLite user store.

Usage:
    python scripts/reset_local_admin.py

What it does:
  1. Opens ~/.vectrax/vectrax.db (read/write)
  2. Generates a new random temporary password
  3. Hashes it with PBKDF2-SHA256 (same params as UserStore)
  4. Updates ONLY the password_hash column for user 'mario'
  5. Sets must_change_password = 1
  6. Prints the new temporary credentials ONCE

What it does NOT touch:
  - No schema changes
  - No new rows created
  - No nucleus, memory, or constellation data modified
  - No tokens invalidated (existing tokens still work until expiry)
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import sys
from pathlib import Path

# Same constants as services/core/users.py
_DB_PATH = Path.home() / ".vectrax" / "vectrax.db"
_PBKDF2_ITERATIONS = 600_000
_USERNAME = "mario"


def _hash_password(password: str) -> str:
    """Produce 'salt_hex$hash_hex' — identical to UserStore._hash_password."""
    salt = os.urandom(32)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return salt.hex() + "$" + dk.hex()


def main() -> None:
    if not _DB_PATH.exists():
        print(f"ERROR: Database not found at {_DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row

    # Verify user exists
    row = conn.execute(
        "SELECT id, username, role FROM users WHERE username = ?", (_USERNAME,)
    ).fetchone()

    if not row:
        print(f"ERROR: User '{_USERNAME}' not found in {_DB_PATH}")
        conn.close()
        sys.exit(1)

    # Generate temporary password
    temp_password = secrets.token_urlsafe(16)
    pw_hash = _hash_password(temp_password)

    # Update only password_hash and flag
    conn.execute(
        "UPDATE users SET password_hash = ?, must_change_password = 1 WHERE username = ?",
        (pw_hash, _USERNAME),
    )
    conn.commit()
    conn.close()

    print("=" * 50)
    print("  Vectrax — Local Admin Password Reset")
    print("=" * 50)
    print(f"  User:     {_USERNAME}")
    print(f"  Role:     {row['role']}")
    print(f"  Password: {temp_password}")
    print()
    print("  ⚠ This is a TEMPORARY password.")
    print("  Change it after login via POST /v1/auth/change-password")
    print("=" * 50)


if __name__ == "__main__":
    main()
