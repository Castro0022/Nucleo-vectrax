"""Test Agent registration and event sending via Core API."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from services.core.app import create_app
from services.core.config import get_settings


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture
def auth_headers(monkeypatch):
    # The insecure default owner token ("vx-dev-token-local") was removed as a
    # security fix, so the gate now requires an explicitly-configured, strong
    # VX_API_TOKEN. Set one for the test and authenticate with it (exercises
    # the real owner path via TokenManager._check_legacy).
    token = "vx-test-owner-strong-0123456789abcdef"
    monkeypatch.setenv("VX_API_TOKEN", token)
    return {"Authorization": f"Bearer {token}"}


def test_events_require_auth(client):
    resp = client.post("/v1/events", json={
        "event_type": "agent.register",
        "agent_id": "test-agent",
        "data": {},
    })
    assert resp.status_code in (401, 403)


def test_agent_register_event(client, auth_headers):
    resp = client.post("/v1/events", json={
        "event_type": "agent.register",
        "agent_id": "test-agent-001",
        "data": {
            "hostname": "test-host",
            "platform": "Test",
            "mode": "online",
        },
    }, headers=auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] is True
    assert data["event_id"] != ""
    assert "agent.register" in data["message"]


def test_agent_heartbeat_event(client, auth_headers):
    resp = client.post("/v1/events", json={
        "event_type": "agent.heartbeat",
        "agent_id": "test-agent-001",
        "data": {"status": "alive"},
    }, headers=auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] is True


def test_auth_login(client):
    resp = client.post("/v1/auth/login", json={
        "username": "owner",
        "password": "",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert data["role"] == "owner"


def test_auth_login_unknown_user(client):
    resp = client.post("/v1/auth/login", json={
        "username": "hacker",
        "password": "try",
    })
    assert resp.status_code == 401
