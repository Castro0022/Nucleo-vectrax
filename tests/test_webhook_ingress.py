"""
Tests for the Telegram webhook ingress (services/core/routes/webhook.py).

Validates the durable webhook contract:
  - Authenticated update → 200, enqueued once to the gateway pool, offset advances.
  - Heartbeat is refreshed on receipt (webhook mode has no heartbeat thread).
  - Duplicate update_id (< offset) → 200 dedup, NOT re-enqueued.
  - Wrong secret → 403, never reaches the gateway.

Hermetic: uses a fake gateway and a minimal FastAPI app mounting only the
webhook router. No network, no real TelegramGateway.
"""
from __future__ import annotations

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.core.routes import webhook


_SECRET = "test-webhook-secret"


class _FakePool:
    def __init__(self):
        self.submitted = []

    def submit(self, fn, update):
        self.submitted.append(update)


class _FakeGateway:
    """Minimal stand-in for TelegramGateway in webhook mode."""

    def __init__(self):
        self._offset = 0
        self._pool = _FakePool()
        self.heartbeats = 0

    def _write_heartbeat(self):
        self.heartbeats += 1

    def _handle(self, update):  # pragma: no cover - never actually run
        pass


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", _SECRET)
    # Isolate the persisted offset file to a temp dir.
    monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path))
    gw = _FakeGateway()
    webhook.set_gateway_instance(gw)
    app = FastAPI()
    app.include_router(webhook.router, prefix="/v1")
    return TestClient(app), gw


def _post(c, secret, update):
    return c.post(
        f"/v1/webhook/telegram/{secret}",
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
    )


def test_authenticated_update_is_enqueued_and_heartbeat_written(client):
    c, gw = client
    r = _post(c, _SECRET, {"update_id": 10, "message": {"text": "hola", "chat": {"id": 1}}})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    # Enqueued exactly once, offset advanced, heartbeat refreshed.
    assert len(gw._pool.submitted) == 1
    assert gw._offset == 11
    assert gw.heartbeats >= 1


def test_duplicate_update_id_is_deduped(client):
    c, gw = client
    gw._offset = 20  # we've already processed up to update_id 19
    r = _post(c, _SECRET, {"update_id": 5, "message": {"text": "dup"}})
    assert r.status_code == 200
    assert r.json().get("dedup") is True
    assert len(gw._pool.submitted) == 0  # NOT re-enqueued


def test_wrong_secret_is_rejected(client):
    c, gw = client
    r = c.post(
        "/v1/webhook/telegram/wrong-secret",
        json={"update_id": 1, "message": {"text": "x"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
    )
    assert r.status_code == 403
    assert len(gw._pool.submitted) == 0


def test_missing_update_id_is_rejected(client):
    c, gw = client
    r = _post(c, _SECRET, {"message": {"text": "no id"}})
    assert r.status_code == 400
    assert len(gw._pool.submitted) == 0
