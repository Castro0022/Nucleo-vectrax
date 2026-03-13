"""Test remote propose endpoint returns proper structure."""

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
def auth_headers():
    settings = get_settings()
    return {"Authorization": f"Bearer {settings.api_token}"}


def test_propose_requires_auth(client):
    resp = client.post("/v1/actions/propose", json={
        "description": "test change",
    })
    assert resp.status_code in (401, 403)


def test_propose_endpoint_responds(client, auth_headers):
    """
    Test the propose endpoint responds without unhandled errors.
    If Ollama is running, we get 200 with a proposal.
    If not, we get 503/500 with a detail message.
    """
    resp = client.post("/v1/actions/propose", json={
        "description": "Add a hello world function",
        "model": "qwen2.5-coder:7b",
    }, headers=auth_headers)

    data = resp.json()
    if resp.status_code == 200:
        # Ollama is running — we got a real proposal
        assert "proposal_id" in data
        assert "description" in data
        assert data["requires_confirmation"] is True
    else:
        # Ollama not running — expect error detail
        assert resp.status_code in (503, 500)
        assert "detail" in data


def test_connectors_endpoint(client, auth_headers):
    """Test connectors listing works and returns stubs."""
    resp = client.get("/v1/connectors", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "connectors" in data
    assert "total" in data
    # Should find the 3 stubs
    assert data["total"] == 3
    names = [c["name"] for c in data["connectors"]]
    assert "google_workspace" in names
    assert "icloud" in names
    assert "generic_webhook" in names
