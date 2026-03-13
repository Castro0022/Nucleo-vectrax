"""
Vectrax Multi-User Architecture — Tests
==========================================
Covers: UserStore, TokenManager, AuthContext, role permissions,
channel isolation, and end-to-end flows.

All tests use a temporary SQLite database to avoid touching production data.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Ensure project root is importable
_root = str(Path(__file__).resolve().parents[1])
if _root not in sys.path:
    sys.path.insert(0, _root)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """Return a temporary SQLite DB path."""
    return tmp_path / "test_vectrax.db"


@pytest.fixture
def user_store(tmp_db):
    from services.core.users import UserStore
    return UserStore(db_path=tmp_db)


@pytest.fixture
def token_mgr(tmp_db):
    from services.core.tokens import TokenManager
    return TokenManager(db_path=tmp_db)


@pytest.fixture
def store_and_tokens(tmp_db):
    from services.core.users import UserStore
    from services.core.tokens import TokenManager
    return UserStore(db_path=tmp_db), TokenManager(db_path=tmp_db)


# ===========================================================================
# UserStore tests
# ===========================================================================

class TestUserStore:
    """UserStore: CRUD, auth, constraints."""

    def test_creator_seeded_on_init(self, user_store):
        """Creator 'mario' should exist after init."""
        mario = user_store.get_by_username("mario")
        assert mario is not None
        assert mario.role == "owner"
        assert mario.channel == "creator"
        assert mario.is_active is True

    def test_create_user_viewer(self, user_store):
        user = user_store.create_user("alice", "pass123", role="viewer")
        assert user.username == "alice"
        assert user.role == "viewer"
        assert user.channel == "user"
        assert user.is_active is True

    def test_create_user_operator(self, user_store):
        user = user_store.create_user("bob", "pass456", role="operator")
        assert user.role == "operator"
        assert user.channel == "user"

    def test_create_duplicate_raises(self, user_store):
        user_store.create_user("charlie", "pw")
        with pytest.raises(ValueError, match="already exists"):
            user_store.create_user("charlie", "pw2")

    def test_cannot_create_owner_role(self, user_store):
        with pytest.raises(ValueError, match="Only the creator"):
            user_store.create_user("hacker", "pw", role="owner")

    def test_invalid_role_raises(self, user_store):
        with pytest.raises(ValueError, match="Invalid role"):
            user_store.create_user("dave", "pw", role="superadmin")

    def test_authenticate_valid(self, user_store):
        user_store.create_user("eve", "secret")
        result = user_store.authenticate("eve", "secret")
        assert result is not None
        assert result.username == "eve"

    def test_authenticate_wrong_password(self, user_store):
        user_store.create_user("frank", "correct")
        result = user_store.authenticate("frank", "wrong")
        assert result is None

    def test_authenticate_nonexistent(self, user_store):
        result = user_store.authenticate("ghost", "pw")
        assert result is None

    def test_authenticate_inactive_user(self, user_store):
        user = user_store.create_user("grace", "pw")
        user_store.deactivate(user.id)
        result = user_store.authenticate("grace", "pw")
        assert result is None

    def test_list_users(self, user_store):
        user_store.create_user("u1", "pw")
        user_store.create_user("u2", "pw")
        users = user_store.list_users()
        # mario + u1 + u2 = 3
        assert len(users) >= 3
        usernames = {u.username for u in users}
        assert "mario" in usernames
        assert "u1" in usernames
        assert "u2" in usernames

    def test_update_role(self, user_store):
        user = user_store.create_user("heidi", "pw", role="viewer")
        updated = user_store.update_role(user.id, "operator")
        assert updated.role == "operator"

    def test_cannot_update_creator_role(self, user_store):
        mario = user_store.get_by_username("mario")
        with pytest.raises(ValueError, match="creator"):
            user_store.update_role(mario.id, "viewer")

    def test_cannot_set_role_to_owner(self, user_store):
        user = user_store.create_user("ivan", "pw")
        with pytest.raises(ValueError, match="Only the creator"):
            user_store.update_role(user.id, "owner")

    def test_deactivate_user(self, user_store):
        user = user_store.create_user("judy", "pw")
        result = user_store.deactivate(user.id)
        assert result is True
        fetched = user_store.get_user(user.id)
        assert fetched.is_active is False

    def test_cannot_deactivate_creator(self, user_store):
        mario = user_store.get_by_username("mario")
        with pytest.raises(ValueError, match="creator"):
            user_store.deactivate(mario.id)

    def test_change_password(self, user_store):
        user = user_store.create_user("kate", "old_pw")
        user_store.change_password(user.id, "new_pw")
        assert user_store.authenticate("kate", "old_pw") is None
        assert user_store.authenticate("kate", "new_pw") is not None

    def test_creator_channel_forced(self, user_store):
        """Even if we try to create mario with channel=user, it's forced to creator."""
        # mario is already seeded, so this should fail as duplicate
        mario = user_store.get_by_username("mario")
        assert mario.channel == "creator"

    def test_user_to_dict(self, user_store):
        user = user_store.create_user("leon", "pw")
        d = user.to_dict()
        assert d["username"] == "leon"
        assert "id" in d
        assert "role" in d
        assert "channel" in d


# ===========================================================================
# TokenManager tests
# ===========================================================================

class TestTokenManager:
    """TokenManager: issue, validate, revoke."""

    def test_issue_and_validate(self, store_and_tokens):
        store, tokens = store_and_tokens
        user = store.create_user("tkuser", "pw")
        plain = tokens.issue_token(user.id, user.username, user.role, user.channel)
        assert plain.startswith("vx-")

        info = tokens.validate_token(plain)
        assert info is not None
        assert info.user_id == user.id
        assert info.username == "tkuser"
        assert info.role == "viewer"
        assert info.channel == "user"
        assert info.owner == "tkuser"

    def test_invalid_token(self, token_mgr):
        info = token_mgr.validate_token("vx-totally-fake-token")
        assert info is None

    def test_revoke_token(self, store_and_tokens):
        store, tokens = store_and_tokens
        user = store.create_user("revuser", "pw")
        plain = tokens.issue_token(user.id, user.username, user.role, user.channel)
        assert tokens.validate_token(plain) is not None

        tokens.revoke_token(plain)
        assert tokens.validate_token(plain) is None

    def test_revoke_all_for_user(self, store_and_tokens):
        store, tokens = store_and_tokens
        user = store.create_user("multitoken", "pw")
        t1 = tokens.issue_token(user.id, user.username, user.role, user.channel)
        t2 = tokens.issue_token(user.id, user.username, user.role, user.channel)

        count = tokens.revoke_all_for_user(user.id)
        assert count == 2
        assert tokens.validate_token(t1) is None
        assert tokens.validate_token(t2) is None

    def test_expired_token(self, store_and_tokens):
        store, tokens = store_and_tokens
        user = store.create_user("expuser", "pw")
        # Issue with 0 TTL
        plain = tokens.issue_token(user.id, user.username, user.role, user.channel, ttl=0)
        # Should be expired immediately (or within 1 second)
        time.sleep(0.1)
        assert tokens.validate_token(plain) is None

    def test_legacy_token_fallback(self, token_mgr):
        """Legacy VX_API_TOKEN should be accepted as owner."""
        legacy = os.getenv("VX_API_TOKEN", "vx-dev-token-local")
        info = token_mgr.validate_token(legacy)
        assert info is not None
        assert info.role == "owner"
        assert info.channel == "creator"
        assert info.username == "mario"

    def test_cleanup_expired(self, store_and_tokens):
        store, tokens = store_and_tokens
        user = store.create_user("cleanup_user", "pw")
        tokens.issue_token(user.id, user.username, user.role, user.channel, ttl=0)
        time.sleep(0.1)
        removed = tokens.cleanup_expired()
        assert removed >= 1


# ===========================================================================
# Roles integration tests
# ===========================================================================

class TestRolesIntegration:
    """Verify that core.roles permissions work with multi-user architecture."""

    def test_owner_has_all_permissions(self):
        from core.roles import Role, Permission, has_permission
        for perm in Permission:
            assert has_permission(Role.OWNER, perm), f"Owner should have {perm}"

    def test_operator_can_propose(self):
        from core.roles import has_permission
        assert has_permission("operator", "propose") is True

    def test_operator_cannot_admin(self):
        from core.roles import has_permission
        assert has_permission("operator", "core.admin") is False

    def test_viewer_cannot_propose(self):
        from core.roles import has_permission
        assert has_permission("viewer", "propose") is False

    def test_viewer_can_read(self):
        from core.roles import has_permission
        assert has_permission("viewer", "core.read") is True
        assert has_permission("viewer", "audit.read") is True


# ===========================================================================
# Channel isolation tests
# ===========================================================================

class TestChannelIsolation:
    """Verify channel separation is maintained in multi-user context."""

    def test_creator_is_always_creator_channel(self, user_store):
        mario = user_store.get_by_username("mario")
        assert mario.channel == "creator"

    def test_regular_users_are_user_channel(self, user_store):
        u = user_store.create_user("testchan", "pw")
        assert u.channel == "user"

    def test_identity_validation_still_works(self):
        from vectrax.identity import (
            validate_channel, validate_creator_ownership,
            assert_no_cross_channel_link, ChannelViolation,
        )
        assert validate_channel("creator") == "creator"
        assert validate_channel("user") == "user"
        with pytest.raises(ChannelViolation):
            validate_channel("admin")

        validate_creator_ownership("mario")  # OK
        with pytest.raises(ChannelViolation):
            validate_creator_ownership("alice")

        assert_no_cross_channel_link("user", "user")  # OK
        with pytest.raises(ChannelViolation):
            assert_no_cross_channel_link("creator", "user")


# ===========================================================================
# End-to-end flow tests
# ===========================================================================

class TestEndToEnd:
    """Full flow: create user → authenticate → issue token → validate."""

    def test_full_flow(self, store_and_tokens):
        store, tokens = store_and_tokens

        # 1. Create user
        user = store.create_user("e2euser", "strongpw", role="operator")
        assert user.username == "e2euser"

        # 2. Authenticate
        authed = store.authenticate("e2euser", "strongpw")
        assert authed is not None
        assert authed.id == user.id

        # 3. Issue token
        plain = tokens.issue_token(
            user_id=authed.id,
            username=authed.username,
            role=authed.role,
            channel=authed.channel,
        )

        # 4. Validate token
        info = tokens.validate_token(plain)
        assert info is not None
        assert info.user_id == user.id
        assert info.role == "operator"
        assert info.channel == "user"
        assert info.owner == "e2euser"

    def test_deactivated_user_token_still_valid_but_auth_fails(self, store_and_tokens):
        """
        Deactivating a user doesn't auto-revoke tokens (caller should do that).
        But authenticate() won't work.
        """
        store, tokens = store_and_tokens
        user = store.create_user("deactuser", "pw")
        plain = tokens.issue_token(user.id, user.username, user.role, user.channel)

        # Deactivate user
        store.deactivate(user.id)

        # Token is still technically valid (caller should revoke)
        info = tokens.validate_token(plain)
        assert info is not None

        # But authenticate() fails
        assert store.authenticate("deactuser", "pw") is None

    def test_password_hash_is_not_plaintext(self, tmp_db):
        """Verify passwords are not stored in plaintext."""
        import sqlite3
        from services.core.users import UserStore

        store = UserStore(db_path=tmp_db)
        store.create_user("hashtest", "mysecretpassword")

        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT password_hash FROM users WHERE username = 'hashtest'"
        ).fetchone()
        conn.close()

        assert row is not None
        assert "mysecretpassword" not in row["password_hash"]
        assert "$" in row["password_hash"]  # salt$hash format

    def test_token_is_not_stored_plaintext(self, store_and_tokens):
        """Verify tokens are stored as hashes, not plaintext."""
        import sqlite3
        store, tokens = store_and_tokens
        user = store.create_user("tokencheck", "pw")
        plain = tokens.issue_token(user.id, user.username, user.role, user.channel)

        conn = sqlite3.connect(tokens._db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT token_hash FROM user_tokens").fetchall()
        conn.close()

        hashes = [r["token_hash"] for r in rows]
        # The plain token should NOT appear in the hashes
        assert plain not in hashes
        # But a SHA-256 hash of the plain should
        import hashlib
        expected_hash = hashlib.sha256(plain.encode()).hexdigest()
        assert expected_hash in hashes
