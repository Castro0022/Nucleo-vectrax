"""Test Policy Gates: sacred_core blocks, flexible defaults, roles."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from core.policy_gates import gate_sacred_core, gate_docs_tests_logs, evaluate_gates
from core.roles import Role, Permission, has_permission, get_permissions


# ---- Policy Gates ----

def test_sacred_core_blocks_governor():
    allowed, reason = gate_sacred_core("core/governor.py")
    assert allowed is False
    assert "SACRED_CORE" in reason


def test_sacred_core_blocks_risk_engine():
    allowed, reason = gate_sacred_core("core/risk_engine.py")
    assert allowed is False


def test_sacred_core_allows_docs():
    allowed, reason = gate_sacred_core("docs/README.md")
    assert allowed is True


def test_docs_tests_logs_default_off():
    """Flexible zone files should be denied by default (switch OFF)."""
    allowed, reason = gate_docs_tests_logs("docs/README.md")
    assert allowed is False
    assert "desactivado" in reason


def test_evaluate_gates_blocks_sacred():
    allowed, reason = evaluate_gates("core/governor.py")
    assert allowed is False
    assert "sacred_core" in reason


def test_evaluate_gates_blocks_flexible_default():
    """Flexible zone blocked because docs_tests_logs_auto is OFF."""
    allowed, reason = evaluate_gates("docs/README.md")
    assert allowed is False


# ---- Roles ----

def test_owner_has_all_permissions():
    for perm in Permission:
        assert has_permission(Role.OWNER, perm) is True


def test_viewer_cannot_propose():
    assert has_permission(Role.VIEWER, Permission.PROPOSE) is False


def test_viewer_can_read():
    assert has_permission(Role.VIEWER, Permission.CORE_READ) is True
    assert has_permission(Role.VIEWER, Permission.AUDIT_READ) is True


def test_operator_can_propose():
    assert has_permission(Role.OPERATOR, Permission.PROPOSE) is True


def test_operator_cannot_apply():
    assert has_permission(Role.OPERATOR, Permission.APPLY_PROPOSAL) is False


def test_get_permissions_returns_set():
    perms = get_permissions("owner")
    assert isinstance(perms, set)
    assert "propose" in perms


def test_invalid_role():
    assert has_permission("unknown_role", "propose") is False
