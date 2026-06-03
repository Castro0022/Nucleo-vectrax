"""
Tests for cross-domain convergence detection and alert history.

Covers:
  - GravityIndex.cross_domain_convergences (intent overlap + temporal proximity)
  - classify / threshold logic
  - check_convergence_alerts deduplication
  - Alert history persistence (append, trim, read)
  - universe_summary domain breakdown
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

# Ensure /app-style imports work from repo root
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.learn.schemas import GravityRecord, Tier
from core.learn.gravity_engine import (
    GravityIndex,
    check_convergence_alerts,
    send_convergence_alerts,
    get_alert_history,
    format_alert_history,
    _append_alert_history,
    _load_sent_alerts,
    _save_sent_alerts,
    ALERT_MIN_HITS,
    ALERT_MIN_CC,
    ALERT_GROWTH_FACTOR,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_vault(tmp_path):
    """Patch VAULT_DIR at the source (core.learn) so all modules that
    import from it get the temp path. Also patch the module-level
    constants in gravity_engine that were computed at import time."""
    vault = str(tmp_path / "vault")
    os.makedirs(vault, exist_ok=True)
    sent_file = os.path.join(vault, "convergence_alerts_sent.json")
    hist_file = os.path.join(vault, "convergence_alert_history.jsonl")

    import core.learn
    import core.learn.gravity_engine as ge

    orig_vault = core.learn.VAULT_DIR
    orig_ge_vault = ge.VAULT_DIR
    orig_sent = ge._ALERTS_SENT_FILE
    orig_hist = ge._ALERT_HISTORY_FILE

    core.learn.VAULT_DIR = vault
    ge.VAULT_DIR = vault
    ge._ALERTS_SENT_FILE = sent_file
    ge._ALERT_HISTORY_FILE = hist_file

    yield vault

    core.learn.VAULT_DIR = orig_vault
    ge.VAULT_DIR = orig_ge_vault
    ge._ALERTS_SENT_FILE = orig_sent
    ge._ALERT_HISTORY_FILE = orig_hist


@pytest.fixture
def gravity_index(tmp_path):
    """Create a fresh GravityIndex in a temp directory."""
    path = str(tmp_path / "gravity_index.json")
    return GravityIndex(path=path)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _old_iso(days=30):
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# cross_domain_convergences — intent overlap
# ---------------------------------------------------------------------------

class TestIntentOverlap:
    def test_exact_intent_match(self, gravity_index):
        """market:NVDA and user_interest:NVDA share intent=NVDA → convergence."""
        gi = gravity_index
        gi.record_event("market:NVDA", cc_score=0.0, domain="market", intent="NVDA")
        gi.record_event("user_interest:NVDA", cc_score=0.5, domain="user_interest", intent="NVDA")

        convs = gi.cross_domain_convergences(domain_a="market")
        intent_convs = [c for c in convs if c["type"] == "intent_overlap"]

        assert len(intent_convs) == 1
        assert intent_convs[0]["intent"] == "NVDA"
        assert intent_convs[0]["star_a"] == "market:NVDA"
        assert intent_convs[0]["star_b"] == "user_interest:NVDA"

    def test_partial_intent_match(self, gravity_index):
        """market:BTCUSD contains 'BTC' which matches user_interest:BTC."""
        gi = gravity_index
        gi.record_event("market:BTCUSD", cc_score=0.3, domain="market", intent="BTCUSD")
        gi.record_event("user_interest:BTC", cc_score=0.5, domain="user_interest", intent="BTC")

        convs = gi.cross_domain_convergences(domain_a="market")
        intent_convs = [c for c in convs if c["type"] == "intent_overlap"]

        assert len(intent_convs) == 1
        assert intent_convs[0]["intent"] == "BTCUSD"

    def test_no_match_different_intents(self, gravity_index):
        """market:AAPL and user_interest:TSLA should NOT converge."""
        gi = gravity_index
        gi.record_event("market:AAPL", cc_score=0.5, domain="market", intent="AAPL")
        gi.record_event("user_interest:TSLA", cc_score=0.5, domain="user_interest", intent="TSLA")

        convs = gi.cross_domain_convergences(domain_a="market")
        intent_convs = [c for c in convs if c["type"] == "intent_overlap"]

        assert len(intent_convs) == 0

    def test_multiple_convergences(self, gravity_index):
        """Multiple symbols should produce independent convergences."""
        gi = gravity_index
        for sym in ["NVDA", "AAPL", "BTC"]:
            gi.record_event(f"market:{sym}", domain="market", intent=sym)
            gi.record_event(f"user_interest:{sym}", domain="user_interest", intent=sym)

        convs = gi.cross_domain_convergences(domain_a="market")
        intent_convs = [c for c in convs if c["type"] == "intent_overlap"]

        assert len(intent_convs) == 3
        intents = {c["intent"] for c in intent_convs}
        assert intents == {"NVDA", "AAPL", "BTC"}

    def test_domain_b_filter(self, gravity_index):
        """When domain_b is specified, only match against that domain."""
        gi = gravity_index
        gi.record_event("market:NVDA", domain="market", intent="NVDA")
        gi.record_event("user_interest:NVDA", domain="user_interest", intent="NVDA")
        gi.record_event("unknown:NVDA", domain="unknown", intent="NVDA")

        # Only match against user_interest
        convs = gi.cross_domain_convergences(domain_a="market", domain_b="user_interest")
        assert len(convs) == 1

        # Match against all non-market
        convs_all = gi.cross_domain_convergences(domain_a="market", domain_b="")
        assert len(convs_all) == 2


# ---------------------------------------------------------------------------
# cross_domain_convergences — temporal proximity
# ---------------------------------------------------------------------------

class TestTemporalProximity:
    def test_recent_high_cc_produces_convergence(self, gravity_index):
        """Two recent stars with cc >= 0.3 each → temporal convergence."""
        gi = gravity_index
        gi.record_event("market:ETH", cc_score=0.4, domain="market", intent="ETH")
        gi.record_event("code:refactor", cc_score=0.5, domain="unknown", intent="REFACTOR")

        convs = gi.cross_domain_convergences(domain_a="market")
        temporal = [c for c in convs if c["type"] == "temporal_proximity"]

        assert len(temporal) == 1

    def test_low_cc_no_temporal_convergence(self, gravity_index):
        """cc=0.0 on market star → combined < 0.3 → no temporal convergence."""
        gi = gravity_index
        gi.record_event("market:SOL", cc_score=0.0, domain="market", intent="SOL")
        gi.record_event("code:test", cc_score=0.5, domain="unknown", intent="TEST")

        convs = gi.cross_domain_convergences(domain_a="market")
        temporal = [c for c in convs if c["type"] == "temporal_proximity"]

        assert len(temporal) == 0

    def test_old_star_no_temporal(self, gravity_index):
        """Star with last_seen > 7 days ago should not produce temporal convergence."""
        gi = gravity_index
        now = _now_iso()
        old = _old_iso(days=10)

        records = gi.load_raw()
        records["market:OLD"] = GravityRecord(
            fingerprint="market:OLD", tier="HOT", hits=5,
            first_seen=old, last_seen=old, cc_score=0.5,
            domain="market", intent="OLD",
        )
        records["recent:NEW"] = GravityRecord(
            fingerprint="recent:NEW", tier="HOT", hits=3,
            first_seen=now, last_seen=now, cc_score=0.5,
            domain="unknown", intent="NEW",
        )
        gi.update_records(records)

        convs = gi.cross_domain_convergences(domain_a="market")
        temporal = [c for c in convs if c["type"] == "temporal_proximity"]

        assert len(temporal) == 0


# ---------------------------------------------------------------------------
# check_convergence_alerts — threshold + deduplication
# ---------------------------------------------------------------------------

class TestAlertThresholds:
    def test_no_alert_below_threshold(self, gravity_index, tmp_vault):
        """Convergence with hits < ALERT_MIN_HITS should not trigger alert."""
        gi = gravity_index
        gi.record_event("market:X", domain="market", intent="X")
        gi.record_event("user:X", domain="user_interest", intent="X")
        # hits=1+1=2, below threshold of 5

        with patch("core.learn.gravity_engine.get_gravity_index", return_value=gi):
            alerts = check_convergence_alerts()

        assert len(alerts) == 0

    def test_alert_on_hits_threshold(self, gravity_index, tmp_vault):
        """Convergence with combined_hits >= ALERT_MIN_HITS triggers alert."""
        gi = gravity_index
        # Accumulate hits
        for _ in range(ALERT_MIN_HITS):
            gi.record_event("market:Y", domain="market", intent="Y")
        gi.record_event("user:Y", domain="user_interest", intent="Y")

        with patch("core.learn.gravity_engine.get_gravity_index", return_value=gi):
            alerts = check_convergence_alerts()

        assert len(alerts) == 1
        assert "hits=" in alerts[0]["alert_reasons"][0]

    def test_alert_on_cc_threshold(self, gravity_index, tmp_vault):
        """Convergence with combined_cc >= ALERT_MIN_CC triggers alert."""
        gi = gravity_index
        gi.record_event("market:Z", cc_score=0.5, domain="market", intent="Z")
        gi.record_event("user:Z", cc_score=0.4, domain="user_interest", intent="Z")
        # combined_cc = 0.45 > 0.4

        with patch("core.learn.gravity_engine.get_gravity_index", return_value=gi):
            alerts = check_convergence_alerts()

        assert len(alerts) == 1
        assert any("coherencia" in r for r in alerts[0]["alert_reasons"])

    def test_deduplication_no_repeat(self, gravity_index, tmp_vault):
        """Same convergence should not alert twice without growth."""
        gi = gravity_index
        for _ in range(ALERT_MIN_HITS):
            gi.record_event("market:W", domain="market", intent="W")
        gi.record_event("user:W", domain="user_interest", intent="W")

        with patch("core.learn.gravity_engine.get_gravity_index", return_value=gi):
            first = check_convergence_alerts()
            assert len(first) == 1

            # Second check without changes → no new alerts
            second = check_convergence_alerts()
            assert len(second) == 0

    def test_growth_retriggers_alert(self, gravity_index, tmp_vault):
        """Doubling hits should retrigger the alert."""
        gi = gravity_index
        for _ in range(ALERT_MIN_HITS):
            gi.record_event("market:G", domain="market", intent="G")
        gi.record_event("user:G", domain="user_interest", intent="G")

        with patch("core.learn.gravity_engine.get_gravity_index", return_value=gi):
            first = check_convergence_alerts()
            assert len(first) == 1

            # Double the hits
            for _ in range(ALERT_MIN_HITS + 1):
                gi.record_event("market:G", domain="market", intent="G")

            second = check_convergence_alerts()
            assert len(second) == 1
            assert any("x" in r for r in second[0]["alert_reasons"])


# ---------------------------------------------------------------------------
# Alert history persistence
# ---------------------------------------------------------------------------

class TestAlertHistory:
    def test_append_and_read(self, tmp_vault):
        """Appending an alert should be readable from history."""
        alert = {
            "star_a": "market:TEST",
            "star_b": "user:TEST",
            "intent": "TEST",
            "type": "intent_overlap",
            "combined_hits": 10,
            "combined_cc": 0.5,
            "domains": ["market", "user_interest"],
            "alert_reasons": ["test reason"],
        }
        _append_alert_history(alert)

        history = get_alert_history(limit=5)
        assert len(history) == 1
        assert history[0]["intent"] == "TEST"
        assert history[0]["combined_hits"] == 10
        assert "timestamp" in history[0]

    def test_history_order_newest_first(self, tmp_vault):
        """History should return newest first."""
        for i in range(3):
            _append_alert_history({
                "star_a": f"a:{i}", "star_b": f"b:{i}",
                "intent": f"SYM{i}", "alert_reasons": [],
            })
            time.sleep(0.01)

        history = get_alert_history(limit=3)
        assert history[0]["intent"] == "SYM2"
        assert history[2]["intent"] == "SYM0"

    def test_history_limit(self, tmp_vault):
        """Limit parameter should cap results."""
        for i in range(10):
            _append_alert_history({"intent": f"S{i}", "alert_reasons": []})

        assert len(get_alert_history(limit=3)) == 3
        assert len(get_alert_history(limit=20)) == 10

    def test_format_empty(self, tmp_vault):
        """Empty history should return a clean message."""
        msg = format_alert_history()
        assert "Sin alertas" in msg

    def test_format_with_data(self, tmp_vault):
        """Formatted output should include intent and hits."""
        _append_alert_history({
            "intent": "NVDA", "combined_hits": 8, "combined_cc": 0.45,
            "star_a": "market:NVDA", "star_b": "user:NVDA",
            "alert_reasons": ["hits=8 superó umbral"],
        })

        msg = format_alert_history()
        assert "NVDA" in msg
        assert "hits=8" in msg

    def test_trim_keeps_max(self, tmp_vault):
        """Trim should keep only _MAX_ALERT_HISTORY entries."""
        from core.learn.gravity_engine import _trim_alert_history, _MAX_ALERT_HISTORY, _ALERT_HISTORY_FILE

        # Write more than max
        os.makedirs(os.path.dirname(_ALERT_HISTORY_FILE), exist_ok=True)
        with open(_ALERT_HISTORY_FILE, "w") as f:
            for i in range(_MAX_ALERT_HISTORY + 50):
                f.write(json.dumps({"intent": f"S{i}"}) + "\n")

        _trim_alert_history()

        with open(_ALERT_HISTORY_FILE) as f:
            lines = [l for l in f if l.strip()]
        assert len(lines) == _MAX_ALERT_HISTORY


# ---------------------------------------------------------------------------
# Domain stats + universe summary
# ---------------------------------------------------------------------------

class TestDomainStats:
    def test_domain_separation(self, gravity_index):
        """Stars from different domains should appear in separate stats."""
        gi = gravity_index
        gi.record_event("m1", domain="market", intent="BTC")
        gi.record_event("m2", domain="market", intent="ETH")
        gi.record_event("u1", domain="user_interest", intent="BTC")
        gi.record_event("c1", domain="unknown", intent="INFRA")

        stats = gi.domain_stats()
        assert stats["market"]["count"] == 2
        assert stats["user_interest"]["count"] == 1
        assert stats["unknown"]["count"] == 1

    def test_universe_summary_structure(self, gravity_index):
        """universe_summary should return all expected keys."""
        gi = gravity_index
        gi.record_event("s1", domain="market", intent="X")

        s = gi.universe_summary()
        assert "total_stars" in s
        assert "tiers" in s
        assert "domains" in s
        assert "cross_domain_convergences" in s
        assert "convergence_details" in s
        assert s["total_stars"] == 1


# ---------------------------------------------------------------------------
# Sent alerts file persistence
# ---------------------------------------------------------------------------

class TestSentAlertsPersistence:
    def test_save_and_load(self, tmp_vault):
        """Saved sent alerts should be loadable."""
        data = {"key1": {"hits": 5, "cc": 0.4, "alerted_at": time.time()}}
        _save_sent_alerts(data)
        loaded = _load_sent_alerts()
        assert loaded["key1"]["hits"] == 5

    def test_empty_load(self, tmp_vault):
        """Loading without file should return empty dict."""
        loaded = _load_sent_alerts()
        assert loaded == {}
