"""
Vectrax Core — User Store
============================
Multi-user registration and authentication backed by SQLite.

Uses the same database as vectrax/db.py (~/.vectrax/vectrax.db).
Password hashing via hashlib (PBKDF2-SHA256) — no extra dependency.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from vectrax.identity import CHANNEL_CREATOR, CHANNEL_USER, CREATOR_OWNER

logger = logging.getLogger("vectrax.core.users")

_DB_DIR = Path.home() / ".vectrax"
_DB_PATH = _DB_DIR / "vectrax.db"

# PBKDF2 iterations (OWASP 2023 recommendation for SHA-256)
_PBKDF2_ITERATIONS = 600_000


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class User:
    id: str
    username: str
    role: str           # owner | operator | viewer  (from core.roles.Role)
    channel: str        # creator | user              (from vectrax.identity)
    created_at: float
    is_active: bool

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "channel": self.channel,
            "created_at": self.created_at,
            "is_active": self.is_active,
        }


# ---------------------------------------------------------------------------
# Password hashing (PBKDF2-SHA256, stdlib only)
# ---------------------------------------------------------------------------

def _hash_password(password: str, salt: bytes | None = None) -> str:
    """Return 'salt_hex$hash_hex' string."""
    if salt is None:
        salt = os.urandom(32)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return salt.hex() + "$" + dk.hex()


def _verify_password(password: str, stored: str) -> bool:
    salt_hex, hash_hex = stored.split("$", 1)
    salt = bytes.fromhex(salt_hex)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return secrets.compare_digest(dk.hex(), hash_hex)


# ---------------------------------------------------------------------------
# UserStore
# ---------------------------------------------------------------------------

class UserStore:
    """
    SQLite-backed user registry.

    On first init, creates the ``users`` table and seeds the creator
    account (mario / owner) with a random password printed once to logs.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db = db_path or _DB_PATH
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_table()
        self._seed_creator()
        self._seed_dev_owner()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _ensure_table(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id                   TEXT PRIMARY KEY,
                    username             TEXT UNIQUE NOT NULL,
                    password_hash        TEXT NOT NULL,
                    role                 TEXT NOT NULL DEFAULT 'viewer',
                    channel              TEXT NOT NULL DEFAULT 'user',
                    created_at           REAL NOT NULL,
                    is_active            INTEGER NOT NULL DEFAULT 1,
                    domain               TEXT NOT NULL DEFAULT 'otro',
                    must_change_password INTEGER NOT NULL DEFAULT 0,
                    provisioned          INTEGER NOT NULL DEFAULT 0
                )
            """)
        self._migrate_users()

    def _migrate_users(self) -> None:
        """Add new columns to existing users table without data loss."""
        migrations = [
            "domain               TEXT NOT NULL DEFAULT 'otro'",
            "must_change_password INTEGER NOT NULL DEFAULT 0",
            "provisioned          INTEGER NOT NULL DEFAULT 0",
        ]
        with self._conn() as conn:
            for col_def in migrations:
                try:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {col_def}")
                except Exception:
                    pass  # Column already exists

    def _seed_creator(self) -> None:
        """Ensure creator (mario) exists.  If not, create with a random password."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE username = ?", (CREATOR_OWNER,)
            ).fetchone()
            if row:
                return

        # First-time seed
        seed_password = secrets.token_urlsafe(24)
        self.create_user(
            username=CREATOR_OWNER,
            password=seed_password,
            role="owner",
            channel=CHANNEL_CREATOR,
        )
        logger.warning(
            "Creator account '%s' seeded. "
            "Initial password (change it): %s",
            CREATOR_OWNER,
            seed_password,
        )

    def _seed_dev_owner(self) -> None:
        """Seed a deterministic 'owner' account for local API login.

        In dev/local only (never production), this provides a frictionless
        owner login with an EMPTY password so local tooling/tests can obtain
        an owner token without knowing the creator's random seed password.
        Gated on settings.is_dev so production never exposes an empty
        credential. Idempotent.
        """
        try:
            from services.core.config.settings import get_settings
            if not get_settings().is_dev:
                return
        except Exception:
            return

        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE username = ?", ("owner",)
            ).fetchone()
            if row:
                return
            conn.execute(
                """INSERT INTO users
                       (id, username, password_hash, role, channel, created_at, is_active)
                   VALUES (?, ?, ?, 'owner', ?, ?, 1)""",
                (
                    str(uuid.uuid4())[:12],
                    "owner",
                    _hash_password(""),
                    CHANNEL_CREATOR,
                    time.time(),
                ),
            )
        logger.info("Dev owner account seeded (empty password, dev/local only)")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_user(
        self,
        username: str,
        password: str,
        role: str = "viewer",
        channel: str = CHANNEL_USER,
    ) -> User:
        """Register a new user.  Raises ValueError on conflict."""
        from core.roles import Role

        # Validate role
        try:
            Role(role)
        except ValueError:
            raise ValueError(f"Invalid role '{role}'. Must be one of: owner, operator, viewer")

        # Enforce creator constraints
        if username == CREATOR_OWNER:
            role = "owner"
            channel = CHANNEL_CREATOR
        else:
            if role == "owner":
                raise ValueError("Only the creator account can have role 'owner'")
            channel = CHANNEL_USER

        uid = str(uuid.uuid4())[:12]
        pw_hash = _hash_password(password)
        now = time.time()

        try:
            with self._conn() as conn:
                conn.execute(
                    """INSERT INTO users (id, username, password_hash, role, channel, created_at, is_active)
                       VALUES (?, ?, ?, ?, ?, ?, 1)""",
                    (uid, username, pw_hash, role, channel, now),
                )
        except sqlite3.IntegrityError:
            raise ValueError(f"Username '{username}' already exists")

        logger.info("User created: %s (role=%s, channel=%s)", username, role, channel)
        return User(id=uid, username=username, role=role, channel=channel,
                    created_at=now, is_active=True)

    def authenticate(self, username: str, password: str) -> Optional[User]:
        """Verify credentials.  Returns User if valid, None otherwise."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ? AND is_active = 1",
                (username,),
            ).fetchone()
        if not row:
            return None
        if not _verify_password(password, row["password_hash"]):
            return None
        return self._row_to_user(row)

    def get_user(self, user_id: str) -> Optional[User]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return self._row_to_user(row) if row else None

    def get_by_username(self, username: str) -> Optional[User]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
        return self._row_to_user(row) if row else None

    def list_users(self, active_only: bool = True) -> List[User]:
        query = "SELECT * FROM users"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY created_at ASC"
        with self._conn() as conn:
            rows = conn.execute(query).fetchall()
        return [self._row_to_user(r) for r in rows]

    def update_role(self, user_id: str, new_role: str) -> Optional[User]:
        """Change a user's role.  Cannot change the creator's role."""
        from core.roles import Role
        try:
            Role(new_role)
        except ValueError:
            raise ValueError(f"Invalid role '{new_role}'")

        user = self.get_user(user_id)
        if not user:
            return None
        if user.username == CREATOR_OWNER:
            raise ValueError("Cannot change the creator's role")
        if new_role == "owner":
            raise ValueError("Only the creator account can have role 'owner'")

        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET role = ? WHERE id = ?", (new_role, user_id)
            )
        user.role = new_role
        return user

    def deactivate(self, user_id: str) -> bool:
        """Deactivate a user (soft delete).  Cannot deactivate the creator."""
        user = self.get_user(user_id)
        if not user:
            return False
        if user.username == CREATOR_OWNER:
            raise ValueError("Cannot deactivate the creator account")
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET is_active = 0 WHERE id = ?", (user_id,)
            )
        logger.info("User deactivated: %s (%s)", user.username, user_id)
        return True

    def change_password(self, user_id: str, new_password: str) -> bool:
        """Update a user's password and clear must_change_password flag."""
        pw_hash = _hash_password(new_password)
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ? AND is_active = 1",
                (pw_hash, user_id),
            )
        return cursor.rowcount > 0

    def set_user_domain(self, user_id: str, domain: str) -> bool:
        """Set the user's primary domain."""
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE users SET domain = ? WHERE id = ?", (domain, user_id)
            )
        return cursor.rowcount > 0

    def set_must_change_password(self, user_id: str, flag: bool) -> bool:
        """Set or clear the must_change_password flag."""
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE users SET must_change_password = ? WHERE id = ?",
                (1 if flag else 0, user_id),
            )
        return cursor.rowcount > 0

    def mark_provisioned(self, user_id: str) -> bool:
        """Mark a user as fully provisioned."""
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE users SET provisioned = 1 WHERE id = ?", (user_id,)
            )
        return cursor.rowcount > 0

    def get_user_domain(self, user_id: str) -> Optional[str]:
        """Get the user's assigned domain."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT domain FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return row["domain"] if row else None

    def needs_password_change(self, user_id: str) -> bool:
        """Check if user must change password on next login."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT must_change_password FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return bool(row["must_change_password"]) if row else False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            username=row["username"],
            role=row["role"],
            channel=row["channel"],
            created_at=row["created_at"],
            is_active=bool(row["is_active"]),
        )
