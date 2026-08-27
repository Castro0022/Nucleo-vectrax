"""
Tests for the 2026-08-27 Dashboard/Observatory UI audit backend changes:

1. dashboard_observatory() now loads the gravity index from disk ONCE
   (gi.load_raw()) and reuses it for domain_stats/tier_counts/
   cross_domain_convergences/top_stars/growth_trends, instead of five
   independent reloads. Verified here by checking the endpoint's gravity
   section matches values computed independently from the same raw dict.

2. dashboard_operator()'s Universe mini-card no longer calls the heavy
   observe_universe() (which also collects ALL user stars, up to 500
   convergence_history rows, gravity engine + eToro injection, word
   gravity, and quality entities). It now reads lightweight, targeted
   sources instead: core.universe_census.get_census(),
   vectrax.core_nucleus.get_core_info(), and
   core.self_observation.state_collector.collect_state().

3. /dashboard/operator and /system/monitor both source their "runtime"
   and "governor" sections from the single shared helper
   core.operator.system_monitor.runtime_and_governor_snapshot(), so the
   two endpoints can never drift in what fields they expose.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.learn.gravity_engine as ge
from core.learn.gravity_engine import GravityIndex


class _TempGravityIndexMixin:
    """Swap the module-level GravityIndex singleton for a temp-backed one,
    so these tests never read/write the real ~/.vectrax/gravity_index.json
    (see test_universe_performance.py's TestGravitySnapshotCache for the
    same established pattern)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="vectrax_test_dash_obs_")
        self._orig_index = ge._index
        ge._index = GravityIndex(path=os.path.join(self.tmpdir, "gravity_index.json"))

        # The census module TTL-caches its result at module scope (10s) —
        # reset it so a previous test's cached census in the same process
        # can never leak into these assertions.
        import core.universe_census as census_mod
        self._orig_census_cache = dict(census_mod._cache)
        census_mod._cache["census"] = None
        census_mod._cache["ts"] = 0.0

    def tearDown(self):
        ge._index = self._orig_index
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        import core.universe_census as census_mod
        census_mod._cache.update(self._orig_census_cache)


class TestDashboardObservatoryGravityReuse(_TempGravityIndexMixin, unittest.IsolatedAsyncioTestCase):
    """dashboard_observatory()'s gravity section must be equivalent to
    independently calling domain_stats/tier_counts/growth_trends on the
    same raw records dict."""

    def setUp(self):
        super().setUp()
        gi = ge.get_gravity_index()
        gi.record_event("market:AAPL", domain="market", intent="AAPL", cc_score=0.8)
        gi.record_event("market:MSFT", domain="market", intent="MSFT", cc_score=0.6)
        gi.record_event("cognitive:x", domain="cognitive", intent="x", cc_score=0.5)

    async def test_gravity_domains_and_tiers_match_independent_computation(self):
        from services.core.routes.dashboard import dashboard_observatory

        result = await dashboard_observatory()
        gravity = result["gravity"]

        gi = ge.get_gravity_index()
        raw = gi.load_raw()
        self.assertEqual(gravity["domains"], gi.domain_stats(records=raw))
        self.assertEqual(gravity["tiers"], gi.tier_counts(records=raw))

    async def test_gravity_trends_match_independent_computation(self):
        from services.core.routes.dashboard import dashboard_observatory

        result = await dashboard_observatory()
        gravity = result["gravity"]

        gi = ge.get_gravity_index()
        raw = gi.load_raw()
        trends_7d = gi.growth_trends(days=7, records=raw)
        trends_1d = gi.growth_trends(days=1, records=raw)
        self.assertEqual(gravity["trends_7d"]["new"], trends_7d["new_stars"])
        self.assertEqual(gravity["trends_7d"]["new_by_domain"], trends_7d["new_by_domain"])
        self.assertEqual(gravity["trends_24h"]["new"], trends_1d["new_stars"])

    async def test_top_stars_reflect_recorded_domains(self):
        from services.core.routes.dashboard import dashboard_observatory

        result = await dashboard_observatory()
        domains_seen = {s["domain"] for s in result["gravity"]["top_stars"]}
        self.assertIn("market", domains_seen)
        self.assertIn("cognitive", domains_seen)


class TestDashboardOperatorLightweightUniverse(_TempGravityIndexMixin, unittest.IsolatedAsyncioTestCase):
    """dashboard_operator()'s Universe mini-card must use targeted,
    lightweight reads instead of the full observe_universe() collection."""

    async def test_observe_universe_is_never_called(self):
        from services.core.routes import dashboard as dash_mod

        with patch("core.self_observation.universe_observer.observe_universe") as mock_observe:
            result = await dash_mod.dashboard_operator()

        mock_observe.assert_not_called()
        self.assertIn("universe", result)

    async def test_universe_fields_sourced_from_census_and_state(self):
        from services.core.routes import dashboard as dash_mod

        fake_census = SimpleNamespace(
            knowledge=11, users=22, mass_total=3.5, patterns=7, convergences=9,
        )
        fake_state = SimpleNamespace(deep_memory_count=5, recent_error_count_24h=2)

        with patch("core.universe_census.get_census", return_value=fake_census), \
             patch("vectrax.core_nucleus.get_core_info", return_value={"core_star_count": 42}), \
             patch("core.self_observation.state_collector.collect_state", return_value=fake_state):
            result = await dash_mod.dashboard_operator()

        u = result["universe"]
        self.assertEqual(u["knowledge_stars"], 11)
        self.assertEqual(u["user_stars"], 22)
        self.assertEqual(u["total_mass"], 3.5)
        self.assertEqual(u["pattern_count"], 7)
        self.assertEqual(u["convergences"], 9)
        self.assertEqual(u["core_stars"], 42)
        self.assertEqual(u["deep_memory"], 5)
        self.assertEqual(u["errors_24h"], 2)

    async def test_universe_section_degrades_gracefully_on_error(self):
        """A failure in one lightweight source must not blow up the whole
        endpoint or the other sources' fields (defensive, matches the
        established try/except-per-source pattern in this file)."""
        from services.core.routes import dashboard as dash_mod

        with patch("core.universe_census.get_census", side_effect=RuntimeError("boom")):
            result = await dash_mod.dashboard_operator()

        self.assertIn("census_error", result["universe"])
        # Other sections must still be present.
        self.assertIn("runtime", result)
        self.assertIn("governor", result)


class TestRuntimeAndGovernorSnapshotSharedHelper(unittest.IsolatedAsyncioTestCase):
    """/dashboard/operator and /system/monitor must both source their
    "runtime"/"governor" sections from the same shared helper
    (core.operator.system_monitor.runtime_and_governor_snapshot), so the
    two views can never independently drift in what fields they expose."""

    async def test_dashboard_operator_uses_shared_helper(self):
        from services.core.routes import dashboard as dash_mod

        fake_snapshot = {"runtime": {"status": "healthy"}, "governor": {"mode": "act"}}
        with patch(
            "core.operator.system_monitor.runtime_and_governor_snapshot",
            return_value=fake_snapshot,
        ) as mock_helper:
            result = await dash_mod.dashboard_operator()

        mock_helper.assert_called_once()
        self.assertEqual(result["runtime"], {"status": "healthy"})
        self.assertEqual(result["governor"], {"mode": "act"})

    async def test_system_monitor_uses_shared_helper(self):
        from services.core.routes import monitor as monitor_mod

        fake_snapshot = {"runtime": {"status": "degraded"}, "governor": {"mode": "recover"}}
        with patch(
            "core.operator.system_monitor.runtime_and_governor_snapshot",
            return_value=fake_snapshot,
        ) as mock_helper:
            # system_monitor()'s ctx param is a FastAPI Depends() default,
            # unused in the function body — safe to pass None when calling
            # the underlying function directly, bypassing FastAPI's DI.
            result = await monitor_mod.system_monitor(ctx=None)

        mock_helper.assert_called_once()
        self.assertEqual(result["runtime"], {"status": "degraded"})
        self.assertEqual(result["governor"], {"mode": "recover"})


if __name__ == "__main__":
    unittest.main()
