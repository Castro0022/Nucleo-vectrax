"""
Vectrax Core — Token Manager
================================
Per-user bearer tokens backed by SQLite.

Each token is prefixed with ``vx-`` followed by 32 random URL-safe chars.
Tokens are stored as SHA-256 hashes — the plain token is returned only at
issue time and never stored.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("vectrax.core.tokens")

_DB_DIR = Path.home() / ".vectrax"
_DB_PATH = _DB_DIR / "vectrax.db"

# Default token TTL: 30 days
_DEFAULT_TTL = 30 * 24 * 3600


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class TokenInfo:
    """Result of a successful token validation."""
    user_id: str
    username: str
    role: str
    channel: str
    owner: str      # same as username for normal users


# ---------------------------------------------------------------------------
# Token Manager
# ---------------------------------------------------------------------------

class TokenManager:
    """
    Issue, validate and revoke per-user bearer tokens.

    Tokens are stored hashed (SHA-256).  The plain-text token is only
    returned from ``issue_token()`` — it cannot be recovered afterwards.

    Legacy support: if a token matches the global ``VX_API_TOKEN`` env var,
    it is treated as the creator/owner token (backwards compatibility).
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db = db_path or _DB_PATH
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_table()

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
                CREATE TABLE IF NOT EXISTS user_tokens (
                    token_hash  TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL,
                    username    TEXT NOT NULL,
                    role        TEXT NOT NULL,
                    channel     TEXT NOT NULL,
                    created_at  REAL NOT NULL,
                    expires_at  REAL NOT NULL,
                    revoked     INTEGER NOT NULL DEFAULT 0
                )
            """)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def issue_token(
        self,
        user_id: str,
        username: str,
        role: str,
        channel: str,
        ttl: int = _DEFAULT_TTL,
    ) -> str:
        """
        Create a new token for a user.

        Returns the plain-text token (``vx-...``).  This is the ONLY time
        the plain token is available — store it securely.
        """
        plain = "vx-" + secrets.token_urlsafe(32)
        token_hash = self._hash(plain)
        now = time.time()
        expires = now + ttl

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO user_tokens
                   (token_hash, user_id, username, role, channel, created_at, expires_at, revoked)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
                (token_hash, user_id, username, role, channel, now, expires),
            )

        logger.info("Token issued for user '%s' (expires in %ds)", username, ttl)
        return plain

    def validate_token(self, plain_token: str) -> Optional[TokenInfo]:
        """
        Validate a bearer token.

        Returns ``TokenInfo`` if valid, ``None`` if invalid/expired/revoked.
        Also checks the legacy ``VX_API_TOKEN`` for backwards compatibility.
        """
        # --- Legacy fallback ---
        legacy = self._check_legacy(plain_token)
        if legacy is not None:
            return legacy

        # --- Normal token lookup ---
        token_hash = self._hash(plain_token)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM user_tokens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()

        if not row:
            return None
        if row["revoked"]:
            return None
        if row["expires_at"] < time.time():
            return None

        return TokenInfo(
            user_id=row["user_id"],
            username=row["username"],
            role=row["role"],
            channel=row["channel"],
            owner=row["username"],
        )

    def revoke_token(self, plain_token: str) -> bool:
        """Revoke a specific token."""
        token_hash = self._hash(plain_token)
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE user_tokens SET revoked = 1 WHERE token_hash = ?",
                (token_hash,),
            )
        return cursor.rowcount > 0

    def revoke_all_for_user(self, user_id: str) -> int:
        """Revoke all tokens for a user.  Returns count revoked."""
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE user_tokens SET revoked = 1 WHERE user_id = ? AND revoked = 0",
                (user_id,),
            )
        count = cursor.rowcount
        if count:
            logger.info("Revoked %d tokens for user_id=%s", count, user_id)
        return count

    def cleanup_expired(self) -> int:
        """Remove expired tokens from the database."""
        now = time.time()
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM user_tokens WHERE expires_at < ? OR revoked = 1",
                (now,),
            )
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _hash(plain_token: str) -> str:
        return hashlib.sha256(plain_token.encode("utf-8")).hexdigest()

    @staticmethod
    def _check_legacy(plain_token: str) -> Optional[TokenInfo]:
        """
        If the token matches a STRONG, explicitly-configured VX_API_TOKEN,
        treat it as the owner (backwards compatibility for existing agents).

        Security: there is NO insecure default. The legacy owner path is
        DISABLED unless VX_API_TOKEN is set to a non-empty value, and the old
        hardcoded default ("vx-dev-token-local") is never accepted — it was a
        backdoor granting owner access to anyone who could reach the API.
        """
        import os
        from vectrax.identity import CHANNEL_CREATOR, CREATOR_OWNER

        legacy = os.getenv("VX_API_TOKEN", "").strip()
        if not legacy or legacy == "vx-dev-token-local":
            return None
        if plain_token == legacy:
            return TokenInfo(
                user_id="legacy-owner",
                username=CREATOR_OWNER,
                role="owner",
                channel=CHANNEL_CREATOR,
                owner=CREATOR_OWNER,
            )
        return None
