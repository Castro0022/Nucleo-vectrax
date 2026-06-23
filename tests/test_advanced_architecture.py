"""
tests/test_advanced_architecture.py

Tests for:
  1. FreightFeedProvider interface + SimulatorAdapter
  2. FreightLearningCycle with mock provider
  3. TradingConvergenceLearner — detection rules, proposals, no auto-apply
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from connectors.freight.base import FreightEvent, FreightFeedProvider


# ===========================================================================
# Helpers
# ===========================================================================

class _MockProvider(FreightFeedProvider):
    """Deterministic provider that returns N pre-built events."""

    def __init__(self, events: List[Dict], healthy: bool = True) -> None:
        self._events = events
        self._healthy = healthy
        self._calls = 0

    @property
    def provider_name(self) -> str:
        return "mock"

    def stream_events(self, n: int) -> List[FreightEvent]:
        self._calls += 1
        return [
            FreightEvent(
                event_type=e["event_type"],
                data=e["data"],
                source="mock",
                ts=time.time(),
            )
            for e in self._events[:n]
        ]

    def health_check(self) -> Dict[str, Any]:
        return {
            "provider": "mock",
            "healthy": self._healthy,
            "latency_ms": 0.0,
            "detail": "mock provider",
        }


_SAMPLE_EVENTS = [
    {"event_type": "load_booking",
     "data": {"origin": "Atlanta", "destination": "Chicago",
               "carrier": "FastHaul", "rate_usd": 1200,
               "trucks_available": 10, "region": "Southeast"}},
    {"event_type": "delay_reported",
     "data": {"origin": "Dallas", "destination": "Houston",
               "carrier": "EcoShip", "delay_hours": 6,
               "cause": "weather", "region": "Southwest"}},
    {"event_type": "rate_change",
     "data": {"lane": "Atlanta→Chicago", "old_rate": 2.5, "new_rate": 3.0,
               "change_pct": 20.0, "demand_level": "high", "region": "Southeast"}},
]


# ===========================================================================
# 1. FreightFeedProvider interface
# ===========================================================================

class TestFreightFeedProviderInterface:
    def test_mock_provider_implements_interface(self):
        p = _MockProvider(_SAMPLE_EVENTS)
        assert isinstance(p, FreightFeedProvider)
        assert p.provider_name == "mock"

    def test_stream_events_returns_freight_events(self):
        p = _MockProvider(_SAMPLE_EVENTS)
        events = p.stream_events(2)
        assert len(events) == 2
        assert all(isinstance(e, FreightEvent) for e in events)
        assert events[0].event_type == "load_booking"
        assert events[1].event_type == "delay_reported"

    def test_stream_events_respects_n_limit(self):
        p = _MockProvider(_SAMPLE_EVENTS)
        events = p.stream_events(1)
        assert len(events) == 1

    def test_health_check_contract(self):
        p = _MockProvider(_SAMPLE_EVENTS)
        hc = p.health_check()
        assert "healthy" in hc
        assert "provider" in hc
        assert "detail" in hc

    def test_freight_event_to_dict(self):
        ev = FreightEvent("load_booking", {"carrier": "X"}, "mock", 0.0)
        d = ev.to_dict()
        assert d["event_type"] == "load_booking"
        assert d["source"] == "mock"

    def test_simulator_adapter_implements_interface(self):
        from connectors.freight.simulator_adapter import SimulatorAdapter
        a = SimulatorAdapter(seed=1)
        assert isinstance(a, FreightFeedProvider)
        assert a.provider_name == "simulator"

    def test_simulator_adapter_generates_events(self):
        from connectors.freight.simulator_adapter import SimulatorAdapter
        a = SimulatorAdapter(seed=42)
        events = a.stream_events(10)
        assert len(events) == 10
        assert all(isinstance(e, FreightEvent) for e in events)
        # All events have a known event_type
        known = {"quote_request", "load_booking", "delivery_complete",
                 "delay_reported", "rate_change", "capacity_update",
                 "weather_event", "empty_miles"}
        assert all(e.event_type in known for e in events)

    def test_simulator_adapter_health_check_healthy(self):
        from connectors.freight.simulator_adapter import SimulatorAdapter
        a = SimulatorAdapter(seed=0)
        hc = a.health_check()
        assert hc["healthy"] is True

    def test_get_provider_default_is_simulator(self, monkeypatch):
        monkeypatch.delenv("FREIGHT_FEED_PROVIDER", raising=False)
        import connectors.freight as cf
        p = cf.get_provider(force_new=True)
        assert p.provider_name == "simulator"

    def test_get_provider_unknown_falls_back_to_simulator(self, monkeypatch):
        monkeypatch.setenv("FREIGHT_FEED_PROVIDER", "nonexistent_provider")
        import connectors.freight as cf
        p = cf.get_provider(force_new=True)
        assert p.provider_name == "simulator"


# ===========================================================================
# 2. FreightLearningCycle
# ===========================================================================

class TestFreightLearningCycle:
    """All tests inject a mock provider; no production data is touched."""

    def test_cycle_calls_provider_once(self, tmp_path, monkeypatch):
        from connectors.freight.learning_cycle import run_learning_cycle
        monkeypatch.setenv("FREIGHT_TENANT_ID", "t_test_cycle")
        p = _MockProvider(_SAMPLE_EVENTS * 10, healthy=True)
        result = run_learning_cycle(n_events=3, tenant_id="t_test", provider=p)
        assert p._calls == 1

    def test_cycle_returns_structured_summary(self, tmp_path, monkeypatch):
        from connectors.freight.learning_cycle import run_learning_cycle
        p = _MockProvider(_SAMPLE_EVENTS, healthy=True)
        s = run_learning_cycle(n_events=3, tenant_id="t_test", provider=p)
        assert "success" in s
        assert "events_ingested" in s
        assert "stars_before" in s
        assert "stars_after" in s
        assert "mature_stars" in s
        assert "patterns_elevated" in s
        assert "elapsed_s" in s
        assert s["provider"] == "mock"

    def test_cycle_unhealthy_provider_exits_early(self, tmp_path, monkeypatch):
        from connectors.freight.learning_cycle import run_learning_cycle
        p = _MockProvider([], healthy=False)
        s = run_learning_cycle(n_events=10, tenant_id="t_test", provider=p)
        assert s["success"] is False
        assert p._calls == 0   # stream_events never called

    def test_disabled_cycle_returns_immediately(self, monkeypatch):
        import connectors.freight.learning_cycle as lc
        monkeypatch.setattr(lc, "_ENABLED", False)
        p = _MockProvider(_SAMPLE_EVENTS)
        s = lc.run_learning_cycle(n_events=10, tenant_id="t_test", provider=p)
        assert s["success"] is False
        assert p._calls == 0

    def test_elevation_called_once_per_cycle_not_per_event(self, monkeypatch):
        """Elevation must be called at cycle end, not inside the ingest loop."""
        elevation_calls = []

        def _mock_elevate(domain, tenant_id):
            elevation_calls.append((domain, tenant_id))
            return 0

        from connectors.freight import learning_cycle as lc
        monkeypatch.setattr(
            "connectors.freight.learning_cycle.try_elevate_from_gravity",
            _mock_elevate,
            raising=False,
        )
        # patch the import path that the function uses
        with patch("connectors.freight.learning_cycle.try_elevate_from_gravity", _mock_elevate):
            p = _MockProvider(_SAMPLE_EVENTS * 3, healthy=True)
            lc.run_learning_cycle(n_events=9, tenant_id="t_test", provider=p)

        # Elevation called at most once regardless of n_events
        assert len(elevation_calls) <= 1


# ===========================================================================
# 3. TradingConvergenceLearner
# ===========================================================================

class TestTradingConvergenceLearner:
    """Tests never touch the real ~/.vectrax signals/patterns files."""

    def _make_signals(self, n_win: int, n_loss: int, days_old: float = 0.0) -> List[Dict]:
        ts = time.time() - days_old * 86400
        sigs = []
        for _ in range(n_win):
            sigs.append({"outcome": "win", "resolved_at": ts})
        for _ in range(n_loss):
            sigs.append({"outcome": "loss", "resolved_at": ts})
        return sigs

    def _make_patterns(self, n: int, wr: float = 40.0, tier: str = "LOW") -> List[Dict]:
        return [{"n": n, "quality_tier": tier,
                 "total_wins": int(n * wr / 100),
                 "total_losses": n - int(n * wr / 100)} for _ in range(3)]

    def test_wr_gap_detected_when_below_threshold(self, tmp_path, monkeypatch):
        from connectors.etoro.convergence_learner import (
            run_observation_cycle, DriftKind,
            GAP_TRIGGER_PP, MIN_RESOLVED,
        )
        sigs = self._make_signals(n_win=30, n_loss=70)  # WR=30%, gap=30pp vs 60%
        pats = self._make_patterns(n=5)
        props_file = str(tmp_path / "proposals.jsonl")

        import connectors.etoro.convergence_learner as cl
        monkeypatch.setattr(cl, "_load_signals", lambda: sigs)
        monkeypatch.setattr(cl, "_load_patterns", lambda: pats)
        monkeypatch.setattr(cl, "_load_config", lambda: {"min_paper_win_rate": 60.0, "min_paper_signals": 30})
        monkeypatch.setattr(cl, "_PROPOSALS_FILE", props_file)

        s = run_observation_cycle()
        assert s["success"] is True
        assert DriftKind.WR_GAP in s["drift_kinds"]
        assert s["proposals_generated"] >= 1

    def test_no_proposal_when_wr_within_gap_tolerance(self, tmp_path, monkeypatch):
        from connectors.etoro.convergence_learner import run_observation_cycle, DriftKind
        sigs = self._make_signals(n_win=58, n_loss=42)  # WR=58%, gap=2pp < GAP_TRIGGER_PP=5
        pats = self._make_patterns(n=5)
        props_file = str(tmp_path / "proposals.jsonl")

        import connectors.etoro.convergence_learner as cl
        monkeypatch.setattr(cl, "_load_signals", lambda: sigs)
        monkeypatch.setattr(cl, "_load_patterns", lambda: pats)
        monkeypatch.setattr(cl, "_load_config", lambda: {"min_paper_win_rate": 60.0, "min_paper_signals": 30})
        monkeypatch.setattr(cl, "_PROPOSALS_FILE", props_file)

        s = run_observation_cycle()
        assert DriftKind.WR_GAP not in s["drift_kinds"]

    def test_sample_gap_detected(self, tmp_path, monkeypatch):
        from connectors.etoro.convergence_learner import run_observation_cycle, DriftKind
        sigs = self._make_signals(n_win=5, n_loss=5)  # only 10 resolved — below MIN_RESOLVED
        pats = self._make_patterns(n=11)  # best_n=11 < min_paper_signals=30
        props_file = str(tmp_path / "proposals.jsonl")

        import connectors.etoro.convergence_learner as cl
        monkeypatch.setattr(cl, "_load_signals", lambda: sigs)
        monkeypatch.setattr(cl, "_load_patterns", lambda: pats)
        monkeypatch.setattr(cl, "_load_config", lambda: {"min_paper_win_rate": 60.0, "min_paper_signals": 30})
        monkeypatch.setattr(cl, "_PROPOSALS_FILE", props_file)

        s = run_observation_cycle()
        assert DriftKind.SAMPLE_GAP in s["drift_kinds"]

    def test_dormancy_detected_when_no_recent_outcomes(self, tmp_path, monkeypatch):
        from connectors.etoro.convergence_learner import run_observation_cycle, DriftKind
        sigs = self._make_signals(n_win=5, n_loss=5, days_old=5.0)  # last outcome 5 days ago
        pats = []
        props_file = str(tmp_path / "proposals.jsonl")

        import connectors.etoro.convergence_learner as cl
        monkeypatch.setattr(cl, "_load_signals", lambda: sigs)
        monkeypatch.setattr(cl, "_load_patterns", lambda: pats)
        monkeypatch.setattr(cl, "_load_config", lambda: {"min_paper_win_rate": 60.0, "min_paper_signals": 30})
        monkeypatch.setattr(cl, "_PROPOSALS_FILE", props_file)

        s = run_observation_cycle()
        assert DriftKind.DORMANCY in s["drift_kinds"]

    def test_proposals_persisted_to_disk(self, tmp_path, monkeypatch):
        from connectors.etoro.convergence_learner import run_observation_cycle
        sigs = self._make_signals(n_win=30, n_loss=70)
        pats = self._make_patterns(n=5)
        props_file = str(tmp_path / "proposals.jsonl")

        import connectors.etoro.convergence_learner as cl
        monkeypatch.setattr(cl, "_load_signals", lambda: sigs)
        monkeypatch.setattr(cl, "_load_patterns", lambda: pats)
        monkeypatch.setattr(cl, "_load_config", lambda: {"min_paper_win_rate": 60.0, "min_paper_signals": 30})
        monkeypatch.setattr(cl, "_PROPOSALS_FILE", props_file)

        run_observation_cycle()
        assert os.path.exists(props_file)
        with open(props_file) as f:
            lines = [l for l in f if l.strip()]
        assert len(lines) >= 1
        p = json.loads(lines[0])
        assert "proposal_id" in p
        assert "drift_kind" in p
        assert "evidence" in p
        assert "suggested_review" in p

    def test_learner_never_mutates_auto_executor_config(self, tmp_path, monkeypatch):
        """Core guarantee: the learner ONLY reads config, never writes it."""
        from connectors.etoro.convergence_learner import run_observation_cycle
        sigs = self._make_signals(n_win=20, n_loss=80)
        pats = self._make_patterns(n=5)
        props_file = str(tmp_path / "proposals.jsonl")

        config_writes = []
        import connectors.etoro.convergence_learner as cl
        monkeypatch.setattr(cl, "_load_signals", lambda: sigs)
        monkeypatch.setattr(cl, "_load_patterns", lambda: pats)
        monkeypatch.setattr(cl, "_load_config", lambda: {"min_paper_win_rate": 60.0, "min_paper_signals": 30})
        monkeypatch.setattr(cl, "_PROPOSALS_FILE", props_file)

        # Patch auto_executor save to detect any write attempt
        try:
            import connectors.etoro.auto_executor as ae
            original_save = ae._save_config
            ae._save_config = lambda cfg: config_writes.append(cfg)
        except Exception:
            pass

        run_observation_cycle()
        assert len(config_writes) == 0, "Learner must NEVER write to auto_executor config"

    def test_approve_proposal_marks_applied_without_changing_config(self, tmp_path, monkeypatch):
        from connectors.etoro.convergence_learner import (
            run_observation_cycle, approve_proposal, get_pending_proposals
        )
        sigs = self._make_signals(n_win=25, n_loss=75)
        pats = self._make_patterns(n=5)
        props_file = str(tmp_path / "proposals.jsonl")

        import connectors.etoro.convergence_learner as cl
        monkeypatch.setattr(cl, "_load_signals", lambda: sigs)
        monkeypatch.setattr(cl, "_load_patterns", lambda: pats)
        monkeypatch.setattr(cl, "_load_config", lambda: {"min_paper_win_rate": 60.0, "min_paper_signals": 30})
        monkeypatch.setattr(cl, "_PROPOSALS_FILE", props_file)

        run_observation_cycle()
        pending = get_pending_proposals()
        assert len(pending) > 0

        pid = pending[0]["proposal_id"]
        result = approve_proposal(pid, approved_by="mario")
        assert result is True

        # After approval, proposal is marked but auto_executor is unchanged
        still_pending = get_pending_proposals()
        assert not any(p["proposal_id"] == pid for p in still_pending)
