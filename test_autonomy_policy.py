"""
Tests for Vectrax Progressive Autonomy Policy
================================================
Verifies: zone classification, auto-apply rules, hard limits,
default-deny behaviour, and batch proposal classification.
"""

import pytest

from core.autonomy_policy import (
    Zone,
    ZONE_LABELS,
    classify_path,
    can_auto_apply,
    is_hard_limited,
    classify_proposal,
    load_autonomy_config,
    DEFAULT_THRESHOLDS,
)


# ===================================================================
# 1. Zone classification — Sacred Core
# ===================================================================

class TestSacredCore:
    """Core Sagrado: archivos que NUNCA deben ser auto-apply."""

    @pytest.mark.parametrize("path", [
        "core/governor.py",
        "core/risk_engine.py",
        "core/state_manager.py",
        "core/smoke.py",
        "core/autonomy_policy.py",
        "core/resilience/retry.py",
        "core/resilience/errors.py",
        "core/routing/smart_router.py",
        "core/routing/circuit_breaker.py",
        "core/abstraction/base.py",
        "core/abstraction/registry.py",
        "schema.sql",
        ".env",
        "vault/vectrax_ledger.db",
        "config/config.yaml",
        "config/autonomy.json",
    ])
    def test_sacred_core_classification(self, path):
        assert classify_path(path) == Zone.SACRED_CORE

    @pytest.mark.parametrize("path", [
        "core/governor.py",
        "core/risk_engine.py",
        ".env",
        "config/config.yaml",
        "config/autonomy.json",
    ])
    def test_sacred_core_never_auto_apply_even_with_switch_on(self, path):
        """Core Sagrado NUNCA permite auto-apply, incluso con switch ON."""
        config = {
            "auto_apply_enabled": True,
            "max_risk_score": 0.15,
            "min_confidence_score": 0.90,
            "max_diff_lines": 50,
        }
        allowed, reason = can_auto_apply(
            path=path,
            risk_score=0.01,       # very low risk
            confidence_score=0.99,  # very high confidence
            diff_lines=5,           # tiny diff
            config=config,
        )
        assert allowed is False
        assert "Core Sagrado" in reason or "HARD LIMIT" in reason or "BLOQUEADO" in reason


# ===================================================================
# 2. Zone classification — Semi-Safe
# ===================================================================

class TestSemiSafe:
    """Zona Semi-Segura: siempre requiere confirmación."""

    @pytest.mark.parametrize("path", [
        "core/proposal_engine.py",
        "core/autopatch.py",
        "core/meta_loop.py",
        "core/shadow_mode.py",
        "core/ingest.py",
        "core/memory_sqlite.py",
        "core/providers/ollama_provider.py",
        "core/workflows/orchestrator.py",
        "cli/vx_main.py",
        "cli/observe.py",
        "scripts/vx",
        "app.py",
        "nucleo.py",
        "vectrax_engine.py",
        "vectrax_unified.py",
        "setup.py",
    ])
    def test_semi_safe_classification(self, path):
        assert classify_path(path) == Zone.SEMI_SAFE

    def test_semi_safe_never_auto_apply(self):
        """Semi-Segura SIEMPRE requiere confirmación, incluso con switch ON."""
        config = {
            "auto_apply_enabled": True,
            "max_risk_score": 0.15,
            "min_confidence_score": 0.90,
            "max_diff_lines": 50,
        }
        allowed, reason = can_auto_apply(
            path="cli/vx_main.py",
            risk_score=0.01,
            confidence_score=0.99,
            diff_lines=5,
            config=config,
        )
        assert allowed is False
        assert "Semi-Segura" in reason


# ===================================================================
# 3. Zone classification — Flexible
# ===================================================================

class TestFlexible:
    """Zona Flexible: auto-apply posible bajo condiciones."""

    @pytest.mark.parametrize("path", [
        "core/daily_report.py",
        "core/reporter.py",
        "core/observability/metrics.py",
        "core/observability/logging.py",
        "core/rules.py",
        "docs/PHASE1_COMPLETE.md",
        "docs/operational_philosophy.md",
        "reports/daily_2026-03-02.json",
        "test_phase1.py",
        "test_risk_engine.py",
    ])
    def test_flexible_classification(self, path):
        assert classify_path(path) == Zone.FLEXIBLE

    def test_flexible_auto_apply_when_all_conditions_met(self):
        """Zona Flexible permite auto-apply cuando switch ON + umbrales OK."""
        config = {
            "auto_apply_enabled": True,
            "max_risk_score": 0.15,
            "min_confidence_score": 0.90,
            "max_diff_lines": 50,
        }
        allowed, reason = can_auto_apply(
            path="docs/README.md",
            risk_score=0.10,
            confidence_score=0.95,
            diff_lines=20,
            config=config,
        )
        assert allowed is True
        assert "Auto-apply permitido" in reason

    def test_flexible_blocked_when_switch_off(self):
        """Zona Flexible bloqueada cuando switch está OFF (por defecto)."""
        config = {
            "auto_apply_enabled": False,
            "max_risk_score": 0.15,
            "min_confidence_score": 0.90,
            "max_diff_lines": 50,
        }
        allowed, reason = can_auto_apply(
            path="docs/README.md",
            risk_score=0.10,
            confidence_score=0.95,
            diff_lines=20,
            config=config,
        )
        assert allowed is False
        assert "desactivado" in reason.lower()

    def test_flexible_blocked_when_risk_too_high(self):
        config = {"auto_apply_enabled": True, "max_risk_score": 0.15,
                  "min_confidence_score": 0.90, "max_diff_lines": 50}
        allowed, _ = can_auto_apply(
            path="docs/README.md",
            risk_score=0.20,  # exceeds 0.15
            confidence_score=0.95,
            diff_lines=20,
            config=config,
        )
        assert allowed is False

    def test_flexible_blocked_when_confidence_too_low(self):
        config = {"auto_apply_enabled": True, "max_risk_score": 0.15,
                  "min_confidence_score": 0.90, "max_diff_lines": 50}
        allowed, _ = can_auto_apply(
            path="docs/README.md",
            risk_score=0.05,
            confidence_score=0.80,  # below 0.90
            diff_lines=20,
            config=config,
        )
        assert allowed is False

    def test_flexible_blocked_when_diff_too_large(self):
        config = {"auto_apply_enabled": True, "max_risk_score": 0.15,
                  "min_confidence_score": 0.90, "max_diff_lines": 50}
        allowed, _ = can_auto_apply(
            path="docs/README.md",
            risk_score=0.05,
            confidence_score=0.95,
            diff_lines=100,  # exceeds 50
            config=config,
        )
        assert allowed is False


# ===================================================================
# 4. Hard limits
# ===================================================================

class TestHardLimits:
    """Hard limits: NUNCA auto-apply, sin importar zona o switch."""

    @pytest.mark.parametrize("path", [
        ".env",
        "vault/vectrax_ledger.db",
        "vault/some_data.json",
        ".git/config",
        "keys/private.pem",
        "secrets/api_tokens.json",
        "vectrax.db",
        "vectrax.db-shm",
        "vectrax.db-wal",
        "some_secret_file.txt",
        "api_key_backup.json",
        "user_credentials.yaml",
        "auth_token_cache.json",
        "my_password_store.txt",
        "data/private_key.pem",
    ])
    def test_hard_limited(self, path):
        assert is_hard_limited(path) is True

    @pytest.mark.parametrize("path", [
        ".env",
        "vault/data.json",
        "vectrax.db",
        "secrets/token.json",
    ])
    def test_hard_limit_blocks_even_with_switch_on(self, path):
        config = {
            "auto_apply_enabled": True,
            "max_risk_score": 0.15,
            "min_confidence_score": 0.90,
            "max_diff_lines": 50,
        }
        allowed, reason = can_auto_apply(
            path=path,
            risk_score=0.01,
            confidence_score=0.99,
            diff_lines=1,
            config=config,
        )
        assert allowed is False
        assert "HARD LIMIT" in reason

    def test_non_hard_limited_files(self):
        """Normal files should not be hard-limited."""
        assert is_hard_limited("docs/README.md") is False
        assert is_hard_limited("core/rules.py") is False
        assert is_hard_limited("test_phase1.py") is False


# ===================================================================
# 5. Default-deny behaviour
# ===================================================================

class TestDefaultDeny:
    """Por defecto, TODO requiere confirmación."""

    def test_default_config_denies_auto_apply(self):
        """With default config, auto-apply is always denied."""
        # Default config has auto_apply_enabled = False
        config = dict(DEFAULT_THRESHOLDS)
        assert config["auto_apply_enabled"] is False

        # Even a perfect candidate is denied
        allowed, reason = can_auto_apply(
            path="docs/README.md",
            risk_score=0.01,
            confidence_score=0.99,
            diff_lines=5,
            config=config,
        )
        assert allowed is False

    def test_unknown_path_classified_as_semi_safe(self):
        """Paths not in any zone map default to Semi-Safe (conservative)."""
        zone = classify_path("totally_unknown_file.xyz")
        assert zone == Zone.SEMI_SAFE

    def test_absolute_path_normalisation(self):
        """Absolute paths should normalise correctly."""
        import os
        abs_path = os.path.expanduser("~/Vectrax/core/governor.py")
        assert classify_path(abs_path) == Zone.SACRED_CORE


# ===================================================================
# 6. Batch proposal classification
# ===================================================================

class TestProposalClassification:
    """classify_proposal: entire proposal classification."""

    def test_mixed_zones_use_most_restrictive(self):
        """A proposal touching Sacred Core is always blocked."""
        result = classify_proposal(
            file_paths=["docs/README.md", "core/governor.py"],
            risk_score=0.05,
            confidence_score=0.95,
            diff_lines=10,
            config={"auto_apply_enabled": True, "max_risk_score": 0.15,
                    "min_confidence_score": 0.90, "max_diff_lines": 50},
        )
        assert result["highest_zone"] == "SACRED_CORE"
        assert result["auto_apply_allowed"] is False

    def test_all_flexible_allowed(self):
        """A proposal with all Flexible files and good scores is allowed."""
        result = classify_proposal(
            file_paths=["docs/README.md", "reports/daily.json"],
            risk_score=0.05,
            confidence_score=0.95,
            diff_lines=10,
            config={"auto_apply_enabled": True, "max_risk_score": 0.15,
                    "min_confidence_score": 0.90, "max_diff_lines": 50},
        )
        assert result["auto_apply_allowed"] is True

    def test_empty_proposal_not_allowed(self):
        """Empty file list is never auto-apply."""
        result = classify_proposal(
            file_paths=[],
            risk_score=0.05,
            confidence_score=0.95,
            diff_lines=10,
        )
        assert result["auto_apply_allowed"] is False


# ===================================================================
# 7. Zone labels
# ===================================================================

class TestZoneLabels:
    def test_all_zones_have_labels(self):
        for zone in Zone:
            assert zone in ZONE_LABELS
            assert isinstance(ZONE_LABELS[zone], str)
            assert len(ZONE_LABELS[zone]) > 0
