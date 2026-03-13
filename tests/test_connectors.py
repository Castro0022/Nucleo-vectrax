"""Test Connector framework: registry, stubs, healthchecks."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from connectors.base import ConnectorSpec, ConnectorAdapter
from connectors.registry import ConnectorRegistry, get_connector_registry


def test_registry_loads_three_stubs():
    """ConnectorRegistry auto-loads 3 built-in stubs."""
    # Use a fresh registry to avoid cross-test pollution
    from connectors.registry import _load_stubs
    reg = ConnectorRegistry()
    _load_stubs(reg)
    names = reg.names()
    assert len(names) == 3
    assert "google_workspace" in names
    assert "icloud" in names
    assert "generic_webhook" in names


def test_google_stub_healthcheck():
    from connectors.stubs.google_stub import GoogleStubConnector
    conn = GoogleStubConnector()
    assert conn.healthcheck() is True


def test_icloud_stub_healthcheck():
    from connectors.stubs.icloud_stub import ICloudStubConnector
    conn = ICloudStubConnector()
    assert conn.healthcheck() is True


def test_webhook_stub_healthcheck():
    from connectors.stubs.generic_webhook_stub import GenericWebhookStubConnector
    conn = GenericWebhookStubConnector()
    assert conn.healthcheck() is True


def test_google_stub_list_resources():
    from connectors.stubs.google_stub import GoogleStubConnector
    conn = GoogleStubConnector()
    resources = conn.list_resources()
    assert len(resources) > 0
    assert all("id" in r and "name" in r for r in resources)


def test_google_stub_test():
    from connectors.stubs.google_stub import GoogleStubConnector
    conn = GoogleStubConnector()
    result = conn.test()
    assert result["passed"] is True


def test_stub_auth_handshake():
    from connectors.stubs.google_stub import GoogleStubConnector
    conn = GoogleStubConnector()
    assert conn.auth_handshake() is True


def test_connector_spec_fields():
    from connectors.stubs.google_stub import GoogleStubConnector
    conn = GoogleStubConnector()
    spec = conn.spec
    assert spec.name == "google_workspace"
    assert isinstance(spec.scopes, list)
    assert isinstance(spec.permissions, list)
    assert spec.version == "0.1.0"
