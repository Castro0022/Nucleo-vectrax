"""
Tests for core/self_observation/observation_ledger.py's optional
``timestamp`` parameter on ``record()``.

Historical replay (domain_ingester.ingest_event(..., event_timestamp=...))
must reach the Observation Ledger too, not just Gravity — otherwise replayed
evidence would be dated with the ingestion time (e.g. 2026) while Gravity
reflects the real historical event time. DB isolation is handled by the
repo's autouse ``_hermetic_base`` fixture (tests/conftest.py), which
redirects ``observation_ledger._DB_PATH`` to a per-test temp vault.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from core.self_observation import observation_ledger as ol


@pytest.fixture(autouse=True)
def _init_ledger():
    """Each test gets a fresh temp DB (via conftest's hermetic fixture);
    the table must be created before record()/get_recent() can be used."""
    ol.init_ledger()


class TestRecordTimestamp:
    def test_omitted_timestamp_uses_now(self):
        ol.record(domain="gravity", obs_type="test_event", summary="s")
        row = ol.get_recent(1)[0]
        ts = datetime.fromisoformat(row["timestamp"])
        assert (datetime.now(timezone.utc) - ts).total_seconds() < 5

    def test_explicit_timestamp_is_used_verbatim_when_aware(self):
        ol.record(
            domain="gravity", obs_type="test_event", summary="s",
            timestamp="2019-03-15T00:00:00+00:00",
        )
        row = ol.get_recent(1)[0]
        assert row["timestamp"] == "2019-03-15T00:00:00+00:00"

    def test_naive_timestamp_normalized_to_utc_aware(self):
        """No UTC offset supplied (e.g. a historical dataset export) must
        still be stored as an unambiguous, parseable, UTC-aware value."""
        ol.record(
            domain="gravity", obs_type="test_event", summary="s",
            timestamp="2019-03-15T10:00:00",
        )
        row = ol.get_recent(1)[0]
        dt = datetime.fromisoformat(row["timestamp"])
        assert dt.tzinfo is not None
        assert dt.utcoffset() == timedelta(0)
        assert dt.hour == 10

    def test_aware_non_utc_timestamp_converted_to_utc(self):
        ol.record(
            domain="gravity", obs_type="test_event", summary="s",
            timestamp="2019-03-15T10:00:00+05:00",
        )
        row = ol.get_recent(1)[0]
        dt = datetime.fromisoformat(row["timestamp"])
        assert dt.utcoffset() == timedelta(0)
        assert dt.hour == 5

    def test_invalid_timestamp_falls_back_to_now(self):
        ol.record(
            domain="gravity", obs_type="test_event", summary="s",
            timestamp="not-a-date",
        )
        row = ol.get_recent(1)[0]
        ts = datetime.fromisoformat(row["timestamp"])
        assert (datetime.now(timezone.utc) - ts).total_seconds() < 5
