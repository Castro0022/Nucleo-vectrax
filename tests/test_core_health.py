"""Test Core Central Service health endpoint."""

import sys
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from services.core.app import create_app


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


def test_health_returns_200(client):
    resp = client.get("/v1/health")
    assert resp.status_code == 200


def test_health_response_body(client):
    resp = client.get("/v1/health")
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "1.0.0"
    assert "uptime_seconds" in data
    assert "components" in data


def test_health_no_auth_required(client):
    """Health endpoint should NOT require auth."""
    resp = client.get("/v1/health")
    assert resp.status_code == 200
