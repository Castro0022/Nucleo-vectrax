"""
Tests for the Vectrax Universal Connection Engine (UCE).

Covers: contracts, normalization, security, learning, engine orchestration.
"""
from __future__ import annotations

import sys
import os
import tempfile
from pathlib import Path

# Ensure project root is in path
_root = str(Path(__file__).resolve().parents[1])
if _root not in sys.path:
    sys.path.insert(0, _root)

import pytest

# ---------------------------------------------------------------------------
# 1. Contract validation
# ---------------------------------------------------------------------------

from connectors.contracts.schema import ConnectorContract
from connectors.contracts.families import ConnectorFamily
from connectors.contracts.validator import validate_contract, validate_or_raise
from connectors.core.types import AuthMethod, ConnectorCapability
from connectors.core.errors import ContractViolation


class TestContractValidation:
    def test_valid_contract(self):
        contract = ConnectorContract(
            name="test_connector",
            family=ConnectorFamily.REST_API,
            version="0.1.0",
            capabilities=[ConnectorCapability.AUTHENTICATE, ConnectorCapability.READ],
            required_permissions=["read"],
            description="Test connector",
        )
        valid, violations = validate_contract(contract)
        assert valid is True
        assert violations == []

    def test_missing_name(self):
        contract = ConnectorContract(
            name="",
            family=ConnectorFamily.REST_API,
            description="Test",
        )
        valid, violations = validate_contract(contract)
        assert valid is False
        assert any("name" in v.lower() for v in violations)

    def test_invalid_version(self):
        contract = ConnectorContract(
            name="test",
            family=ConnectorFamily.REST_API,
            version="1.0",  # not semver
            description="Test",
        )
        valid, violations = validate_contract(contract)
        assert valid is False
        assert any("semver" in v.lower() for v in violations)

    def test_write_without_permission(self):
        contract = ConnectorContract(
            name="test",
            family=ConnectorFamily.REST_API,
            version="0.1.0",
            capabilities=[ConnectorCapability.AUTHENTICATE, ConnectorCapability.WRITE],
            required_permissions=["read"],  # missing "write"
            description="Test",
        )
        valid, violations = validate_contract(contract)
        assert valid is False
        assert any("write" in v.lower() for v in violations)

    def test_validate_or_raise(self):
        contract = ConnectorContract(
            name="",
            family=ConnectorFamily.CUSTOM,
            description="Test",
        )
        with pytest.raises(ContractViolation):
            validate_or_raise(contract)


# ---------------------------------------------------------------------------
# 2. Normalization
# ---------------------------------------------------------------------------

from connectors.normalization.engine import NormalizationEngine
from connectors.core.types import OperationType, Sensitivity, Severity


class TestNormalization:
    def setup_method(self):
        self.engine = NormalizationEngine()

    def test_normalize_read(self):
        raw = {"data": {"key": "value"}, "success": True}
        result = self.engine.normalize(raw, "test_conn", OperationType.READ)
        assert result.success is True
        assert result.source_connector == "test_conn"
        assert result.operation == "read"

    def test_normalize_error(self):
        raw = {"error": "Connection refused", "success": False}
        result = self.engine.normalize(raw, "test_conn", OperationType.READ)
        assert result.success is False
        assert result.severity == Severity.ERROR

    def test_sensitivity_detection(self):
        raw = {"data": "password=secret123"}
        result = self.engine.normalize(raw, "test_conn", OperationType.READ)
        assert result.sensitivity == Sensitivity.CRITICAL

    def test_to_event(self):
        raw = {"event_type": "file_changed", "description": "foo.txt modified"}
        event = self.engine.to_event(raw, "fs_conn")
        assert event.event_type == "file_changed"
        assert event.source_connector == "fs_conn"

    def test_to_entity(self):
        raw = {"name": "README.md", "type": "file", "content": "Hello"}
        entity = self.engine.to_entity(raw, "fs_conn")
        assert entity.name == "README.md"
        assert entity.content == "Hello"

    def test_to_action(self):
        raw = {"action_type": "write", "target": "/tmp/test.txt", "success": True}
        action = self.engine.to_action(raw, "fs_conn")
        assert action.success is True
        assert action.target == "/tmp/test.txt"


# ---------------------------------------------------------------------------
# 3. Security policy
# ---------------------------------------------------------------------------

from connectors.security.policy import ConnectionPolicy


class TestSecurityPolicy:
    def test_check_permissions_ok(self):
        contract = ConnectorContract(
            name="test", family=ConnectorFamily.REST_API,
            required_permissions=["read"],
            description="Test",
        )
        assert ConnectionPolicy.check_permissions(contract, ["read", "write"]) is True

    def test_check_permissions_denied(self):
        contract = ConnectorContract(
            name="test", family=ConnectorFamily.REST_API,
            required_permissions=["read", "write"],
            description="Test",
        )
        assert ConnectionPolicy.check_permissions(contract, ["read"]) is False

    def test_enforce_permissions_raises(self):
        from connectors.core.errors import PermissionDenied
        contract = ConnectorContract(
            name="test", family=ConnectorFamily.TERMINAL,
            required_permissions=["read", "execute"],
            description="Test",
        )
        with pytest.raises(PermissionDenied):
            ConnectionPolicy.enforce_permissions(contract, ["read"])

    def test_risk_assessment(self):
        contract = ConnectorContract(
            name="shell", family=ConnectorFamily.TERMINAL,
            capabilities=[ConnectorCapability.AUTHENTICATE, ConnectorCapability.EXECUTE],
            required_permissions=["read", "execute"],
            description="Shell",
        )
        risk = ConnectionPolicy.assess_risk(contract)
        assert risk["risk_level"] == "critical"

    def test_sensitivity_check(self):
        assert ConnectionPolicy.check_sensitivity(Sensitivity.LOW, Sensitivity.HIGH) is True
        assert ConnectionPolicy.check_sensitivity(Sensitivity.CRITICAL, Sensitivity.LOW) is False


# ---------------------------------------------------------------------------
# 4. Learning tracker
# ---------------------------------------------------------------------------

from connectors.learning.tracker import ConnectionTracker


class TestConnectionTracker:
    def test_track_and_retrieve(self):
        tracker = ConnectionTracker()
        entry = tracker.track_operation(
            connector_name="test_conn",
            operation="read",
            success=True,
            latency_ms=42.5,
        )
        assert entry.status == "success"
        logs = tracker.get_logs(connector_name="test_conn", limit=1)
        assert len(logs) >= 1
        assert logs[0].connector_name == "test_conn"

    def test_stats(self):
        tracker = ConnectionTracker()
        # Insert a few entries
        for i in range(3):
            tracker.track_operation("stats_test", "read", True, 10.0 + i)
        tracker.track_operation("stats_test", "read", False, 100.0, error="timeout")
        stats = tracker.get_stats("stats_test")
        assert stats["total"] >= 4
        assert stats["failures"] >= 1


# ---------------------------------------------------------------------------
# 5. Adapters — filesystem connector
# ---------------------------------------------------------------------------

from connectors.adapters.local_filesystem import LocalFilesystemConnector


class TestFilesystemConnector:
    def test_read_directory(self):
        conn = LocalFilesystemConnector(root=tempfile.gettempdir())
        conn.authenticate()
        result = conn.read({"path": "."})
        assert result["success"] is True
        assert "entries" in result

    def test_write_and_read_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = LocalFilesystemConnector(root=tmpdir)
            conn.authenticate()
            conn.write({"path": "hello.txt", "content": "Hello UCE"})
            result = conn.read({"path": "hello.txt"})
            assert result["success"] is True
            assert result["content"] == "Hello UCE"

    def test_sandbox_escape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = LocalFilesystemConnector(root=tmpdir)
            conn.authenticate()
            with pytest.raises(PermissionError):
                conn.read({"path": "../../etc/passwd"})


# ---------------------------------------------------------------------------
# 6. Adapters — shell connector
# ---------------------------------------------------------------------------

from connectors.adapters.shell import ShellConnector


class TestShellConnector:
    def test_read_env(self):
        conn = ShellConnector()
        conn.authenticate()
        result = conn.read({"var": "HOME"})
        assert result["success"] is True
        assert result["exists"] is True

    def test_healthcheck(self):
        conn = ShellConnector()
        assert conn.healthcheck() is True


# ---------------------------------------------------------------------------
# 7. Engine integration
# ---------------------------------------------------------------------------

from connectors.engine import ConnectionEngine
from connectors.core.errors import ApprovalRequired


class TestConnectionEngine:
    def test_register_and_list(self):
        engine = ConnectionEngine()
        conn = LocalFilesystemConnector(root=tempfile.gettempdir())
        from connectors.adapters.local_filesystem import FILESYSTEM_CONTRACT
        engine.register(conn, FILESYSTEM_CONTRACT)
        assert "local_filesystem" in engine.list_connectors()

    def test_propose_and_approve(self):
        engine = ConnectionEngine()
        conn = LocalFilesystemConnector(root=tempfile.gettempdir())
        from connectors.adapters.local_filesystem import FILESYSTEM_CONTRACT
        engine.register(conn, FILESYSTEM_CONTRACT)

        # Propose
        proposal = engine.propose_connection("local_filesystem")
        assert proposal["status"] == "pending"

        # Approve
        result = engine.approve(proposal["id"])
        assert result["status"] == "approved"

    def test_connect_requires_approval(self):
        from connectors.contracts.schema import ConnectorContract as CC
        from connectors.contracts.families import ConnectorFamily as CF
        from connectors.core.types import ConnectorCapability as Cap, AuthMethod
        from connectors.base import ConnectorSpec

        # Use a unique name that has never been approved
        unapproved_contract = CC(
            name="unapproved_fs_test",
            family=CF.LOCAL_FILE,
            version="0.1.0",
            capabilities=[Cap.AUTHENTICATE, Cap.READ, Cap.WRITE],
            required_permissions=["read", "write"],
            auth_method=AuthMethod.NONE,
            description="Unapproved test connector",
        )
        conn = LocalFilesystemConnector(root=tempfile.gettempdir())
        # Override spec name to match contract
        conn._spec = ConnectorSpec(
            name="unapproved_fs_test", scopes=["fs.read"], permissions=["read", "write"],
            version="0.1.0", description="test",
        )
        engine = ConnectionEngine()
        engine.register(conn, unapproved_contract)

        with pytest.raises(ApprovalRequired):
            engine.connect("unapproved_fs_test")

    def test_full_lifecycle(self):
        engine = ConnectionEngine()
        conn = LocalFilesystemConnector(root=tempfile.gettempdir())
        from connectors.adapters.local_filesystem import FILESYSTEM_CONTRACT
        engine.register(conn, FILESYSTEM_CONTRACT)

        # Propose + approve
        proposal = engine.propose_connection("local_filesystem")
        engine.approve(proposal["id"])

        # Connect
        assert engine.connect("local_filesystem") is True

        # Read
        result = engine.read("local_filesystem", {"path": "."})
        assert result.success is True

        # Status
        status = engine.status()
        assert status["total_connectors"] == 1
        assert "local_filesystem" in status["connectors"]
