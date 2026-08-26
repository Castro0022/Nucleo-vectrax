"""
Tests for POST /v1/ingest/{domain} (services/core/routes/ingest_api.py).

The endpoint is deliberately domain-agnostic: it works for ANY domain with
a config/domain_templates/{domain}.json file, including sales_trends,
without any domain-specific code. These tests validate that generic
contract end-to-end (auth, domain restriction, rate limiting) and guard
against a real bug found while activating sales_trends: ``event.timestamp``
was defined on the request model but never forwarded to
``core.domain_ingester.ingest_event()``, so historical/precise timestamps
supplied by a real tenant were silently discarded.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import core.tenant as tenant_mod
from services.core.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Isolate the tenant DB and gravity index for this test (never touch
    # the real ~/.vectrax state), following the pattern already used by
    # tests/test_domain_ingest.py and tests/test_freight_pipeline.py.
    tenant_mod._DB_PATH = str(tmp_path / "tenants.db")

    import core.learn.gravity_engine as ge
    orig_index = ge._index
    ge._index = ge.GravityIndex(path=str(tmp_path / "gravity_index.json"))

    app = create_app()
    yield TestClient(app)

    ge._index = orig_index


@pytest.fixture
def sales_tenant():
    t = tenant_mod.create_tenant("Sales Test Co", plan="pro", domain="sales_trends")
    return t


class TestGenericIngestDomainAgnostic:
    """The endpoint must not special-case sales_trends (or any domain) —
    it works purely from the presence of a domain template on disk."""

    def test_sales_trends_ingest_succeeds(self, client, sales_tenant):
        resp = client.post(
            "/v1/ingest/sales_trends",
            headers={"x-api-key": sales_tenant["api_key"]},
            json={
                "event_type": "sale",
                "data": {
                    "product": "SKU-TEST-1", "category": "tools", "region": "EU",
                    "quantity": 3, "amount": 42.0,
                },
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["domain"] == "sales_trends"
        assert body["star_id"]

    def test_unknown_domain_still_ingests_generically(self, client):
        """No domain template on disk for 'some_new_domain' -> falls back to
        generic key=value text formatting, still succeeds (domain-agnostic
        design, not an allowlist of pre-registered domains)."""
        t = tenant_mod.create_tenant("Generic Co", plan="free", domain="some_new_domain")
        resp = client.post(
            "/v1/ingest/some_new_domain",
            headers={"x-api-key": t["api_key"]},
            json={"event_type": "custom", "data": {"x": 1}},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True


class TestIngestAuth:
    def test_invalid_api_key_rejected(self, client):
        resp = client.post(
            "/v1/ingest/sales_trends",
            headers={"x-api-key": "vx_invalid"},
            json={"event_type": "sale", "data": {}},
        )
        assert resp.status_code == 401

    def test_missing_api_key_rejected(self, client):
        resp = client.post("/v1/ingest/sales_trends", json={"event_type": "sale", "data": {}})
        assert resp.status_code == 422  # header required

    def test_domain_locked_tenant_cannot_post_other_domain(self, client, sales_tenant):
        resp = client.post(
            "/v1/ingest/cybersecurity",
            headers={"x-api-key": sales_tenant["api_key"]},
            json={"event_type": "cve_family", "data": {}},
        )
        assert resp.status_code == 403


class TestEventTimestampPropagation:
    """Regression guard: event.timestamp must reach Gravity, not be dropped."""

    def test_timestamp_reaches_gravity(self, client, sales_tenant):
        import core.learn.gravity_engine as ge

        resp = client.post(
            "/v1/ingest/sales_trends",
            headers={"x-api-key": sales_tenant["api_key"]},
            json={
                "event_type": "sale",
                "data": {"product": "SKU-TS", "category": "tools", "region": "EU",
                         "quantity": 1, "amount": 10.0},
                "timestamp": "2019-03-15T00:00:00+00:00",
            },
        )
        assert resp.status_code == 200
        star_id = resp.json()["star_id"]
        rec = ge.get_gravity_index().get(star_id)
        assert rec is not None
        assert rec.first_seen == "2019-03-15T00:00:00+00:00"
        assert "2019-03-15T00:00:00+00:00" in rec.activation_history

    def test_omitted_timestamp_uses_ingestion_time(self, client, sales_tenant):
        import core.learn.gravity_engine as ge

        resp = client.post(
            "/v1/ingest/sales_trends",
            headers={"x-api-key": sales_tenant["api_key"]},
            json={
                "event_type": "sale",
                "data": {"product": "SKU-NOW", "category": "tools", "region": "EU",
                         "quantity": 1, "amount": 10.0},
            },
        )
        assert resp.status_code == 200
        star_id = resp.json()["star_id"]
        rec = ge.get_gravity_index().get(star_id)
        ts = datetime.fromisoformat(rec.first_seen)
        assert (datetime.now(timezone.utc) - ts).total_seconds() < 10
