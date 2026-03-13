"""
Test — User Independence Flow
=================================
Validates that:
  1. Provisioning creates the complete independence package
  2. No artifact touches channel='creator' or owner='mario'
  3. Constellations are isolated per user
  4. Nucleus remains intact after any number of provisions
  5. Password/token policies are enforced
  6. Domain assignment is correct
"""
import os
import sys
import tempfile
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from services.core.independence import (
    VALID_DOMAINS,
    DOMAIN_SEEDS,
    IndependencePackage,
    UserIndependenceEngine,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Use a temporary database for every test."""
    db_dir = tmp_path / ".vectrax"
    db_dir.mkdir()
    db_path = db_dir / "vectrax.db"

    # Patch all DB paths to use temp
    monkeypatch.setattr("services.core.users._DB_DIR", db_dir)
    monkeypatch.setattr("services.core.users._DB_PATH", db_path)
    monkeypatch.setattr("services.core.tokens._DB_DIR", db_dir)
    monkeypatch.setattr("services.core.tokens._DB_PATH", db_path)

    # Patch vectrax.db paths
    import vectrax.db as vdb
    monkeypatch.setattr(vdb, "DB_DIR", db_dir)
    monkeypatch.setattr(vdb, "DB_PATH", db_path)
    vdb.init_db()

    yield db_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestIndependencePackageCreation:
    """Verify the complete independence package is generated."""

    def test_provision_creates_full_package(self):
        engine = UserIndependenceEngine()
        pkg = engine.provision(username="testuser", role="viewer", domain="tecnológico")

        assert pkg.validation_passed is True
        assert pkg.user_id != ""
        assert pkg.username == "testuser"
        assert pkg.role == "viewer"
        assert pkg.channel == "user"
        assert pkg.owner == "testuser"
        assert pkg.temp_password != ""
        assert pkg.token.startswith("vx-")
        assert pkg.domain == "tecnológico"
        assert pkg.domain_context != ""
        assert len(pkg.domain_rules) > 0
        assert len(pkg.domain_routes) > 0
        assert pkg.constellation_id != ""
        assert pkg.seed_stars_created > 0
        assert pkg.portal_url == "/portal/testuser"
        assert pkg.nucleus_intact is True

    def test_provision_auto_generates_password(self):
        engine = UserIndependenceEngine()
        pkg = engine.provision(username="autopass", role="viewer")

        assert pkg.must_change_password is True
        assert len(pkg.temp_password) > 10

    def test_provision_with_explicit_password(self):
        engine = UserIndependenceEngine()
        pkg = engine.provision(username="explicitpass", password="mypassword123")

        assert pkg.must_change_password is False
        assert pkg.temp_password == "mypassword123"


class TestNucleusIntegrity:
    """Verify that no provision touches the creator channel."""

    def test_no_creator_channel_contamination(self):
        engine = UserIndependenceEngine()
        pkg = engine.provision(username="externaluser", domain="financiero")

        assert pkg.nucleus_intact is True

        # Verify explicitly: no stars in creator channel for this user
        from vectrax import db
        from vectrax.identity import CHANNEL_CREATOR
        creator_stars = db.get_all_stars(channel=CHANNEL_CREATOR, owner="externaluser")
        assert len(creator_stars) == 0

    def test_cannot_provision_creator(self):
        engine = UserIndependenceEngine()
        pkg = engine.provision(username="mario")

        assert pkg.validation_passed is False
        assert "creator account" in pkg.validation_errors[0].lower()

    def test_user_stars_only_in_user_channel(self):
        engine = UserIndependenceEngine()
        pkg = engine.provision(username="isolateduser", domain="personal")

        from vectrax import db
        user_stars = db.get_all_stars(channel="user", owner="isolateduser")
        assert len(user_stars) > 0

        # None in creator
        creator_stars = db.get_all_stars(channel="creator", owner="isolateduser")
        assert len(creator_stars) == 0


class TestConstellationIsolation:
    """Verify constellations are isolated per user."""

    def test_constellation_belongs_to_user(self):
        engine = UserIndependenceEngine()
        pkg = engine.provision(username="constuser", domain="empresarial")

        from vectrax import db
        constellations = db.get_all_constellations(channel="user", owner="constuser")
        assert len(constellations) >= 1
        assert constellations[0].owner == "constuser"
        assert constellations[0].channel == "user"

    def test_two_users_have_separate_constellations(self):
        engine = UserIndependenceEngine()
        pkg_a = engine.provision(username="user_a", domain="personal")
        pkg_b = engine.provision(username="user_b", domain="financiero")

        assert pkg_a.constellation_id != pkg_b.constellation_id

        from vectrax import db
        const_a = db.get_all_constellations(channel="user", owner="user_a")
        const_b = db.get_all_constellations(channel="user", owner="user_b")

        a_ids = {c.id for c in const_a}
        b_ids = {c.id for c in const_b}
        assert a_ids.isdisjoint(b_ids), "Constellations must not overlap between users"


class TestDomainAssignment:
    """Verify domain assignment and cognitive path seeding."""

    @pytest.mark.parametrize("domain", VALID_DOMAINS)
    def test_all_domains_provision_correctly(self, domain):
        engine = UserIndependenceEngine()
        safe_name = f"domaintest_{domain.replace('ó','o').replace('í','i')}"
        pkg = engine.provision(username=safe_name, domain=domain)

        assert pkg.validation_passed is True
        assert pkg.domain == domain
        assert pkg.domain_context == DOMAIN_SEEDS[domain]["context"]
        assert pkg.seed_stars_created > 0

    def test_invalid_domain_falls_back_to_otro(self):
        engine = UserIndependenceEngine()
        pkg = engine.provision(username="fallbackuser", domain="nonexistent")

        assert pkg.domain == "otro"


class TestPasswordPolicy:
    """Verify password and token policies."""

    def test_token_is_issued(self):
        engine = UserIndependenceEngine()
        pkg = engine.provision(username="tokenuser")

        assert pkg.token.startswith("vx-")
        assert pkg.token_expires_in == 30 * 86400

    def test_duplicate_user_fails(self):
        engine = UserIndependenceEngine()
        pkg1 = engine.provision(username="dupeuser")
        assert pkg1.validation_passed is True

        pkg2 = engine.provision(username="dupeuser")
        assert pkg2.validation_passed is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
