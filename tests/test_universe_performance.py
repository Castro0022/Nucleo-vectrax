"""
Tests for the 2026-08-26 universe performance fix (sales_trends backfill
regression). See docs/UNIVERSE_PERFORMANCE_2026_08_26.md for the full
before/after measurement against real production data.

Two changes are covered here, both delivery-layer only — no star,
fingerprint, connection, or gravity_index.json data is ever touched:

1. core.learn.gravity_engine.GravityIndex.domain_stats() and
   cross_domain_convergences() now accept an optional ``records`` param
   so a caller that already loaded the index once can reuse it instead
   of triggering another full disk read + JSON parse. Must produce
   IDENTICAL results whether records is passed or not.

2. core.self_observation.universe_observer caches the expensive
   gravity-derived fields (stars/domains/convergences) for a short TTL,
   since the /v1/universe WebSocket recomputes this every 2s regardless
   of whether the data changed. Must serve the same data within the TTL
   window and recompute (reflecting real changes) after it expires.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.learn.gravity_engine import GravityIndex


class TestDomainStatsRecordsParam(unittest.TestCase):
    """domain_stats(records=...) must be equivalent to domain_stats()."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="vectrax_test_universe_perf_")
        self.idx = GravityIndex(path=os.path.join(self.tmpdir, "gravity_index.json"))
        for i in range(5):
            self.idx.record_event(f"market:fp_{i}", domain="market", intent="AAPL", cc_score=0.5)
        for i in range(3):
            self.idx.record_event(f"other:fp_{i}", domain="other_domain", intent="x", cc_score=0.3)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_domain_stats_with_and_without_records_match(self):
        via_disk = self.idx.domain_stats()
        raw = self.idx.load_raw()
        via_records = self.idx.domain_stats(records=raw)
        self.assertEqual(via_disk, via_records)

    def test_domain_stats_records_param_does_not_hit_disk(self):
        """Deleting the file must not affect domain_stats(records=...)."""
        raw = self.idx.load_raw()
        os.remove(self.idx.path)
        via_records = self.idx.domain_stats(records=raw)
        self.assertIn("market", via_records)
        self.assertEqual(via_records["market"]["count"], 5)


class TestCrossDomainConvergencesRecordsParam(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="vectrax_test_universe_perf_")
        self.idx = GravityIndex(path=os.path.join(self.tmpdir, "gravity_index.json"))
        self.idx.record_event("market:AAPL", domain="market", intent="AAPL", cc_score=0.8)
        self.idx.record_event("user_interest:AAPL", domain="user_interest", intent="AAPL", cc_score=0.7)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_cross_domain_convergences_with_and_without_records_match(self):
        via_disk = self.idx.cross_domain_convergences()
        raw = self.idx.load_raw()
        via_records = self.idx.cross_domain_convergences(records=raw)
        self.assertEqual(via_disk, via_records)

    def test_cross_domain_convergences_records_param_does_not_hit_disk(self):
        raw = self.idx.load_raw()
        os.remove(self.idx.path)
        via_records = self.idx.cross_domain_convergences(records=raw)
        self.assertGreaterEqual(len(via_records), 1)


class TestGravitySnapshotCache(unittest.TestCase):
    """core.self_observation.universe_observer's short-TTL gravity cache."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="vectrax_test_universe_perf_")
        import core.learn.gravity_engine as ge
        self._orig_index = ge._index
        ge._index = ge.GravityIndex(path=os.path.join(self.tmpdir, "gravity_index.json"))
        ge._index.record_event("fp_a", domain="sales_trends", intent="sale", cc_score=0.5)

        import core.self_observation.universe_observer as uo
        self._uo = uo
        # Reset module-level cache so tests don't leak into each other.
        uo._gravity_snapshot_cache["data"] = None
        uo._gravity_snapshot_cache["ts"] = 0.0

    def tearDown(self):
        import core.learn.gravity_engine as ge
        ge._index = self._orig_index
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_second_call_within_ttl_returns_cached_data_object(self):
        first = self._uo._get_gravity_snapshot_cached()
        second = self._uo._get_gravity_snapshot_cached()
        self.assertIs(first, second)  # same object -> genuinely served from cache

    def test_cache_never_touches_the_file_on_a_hit(self):
        first = self._uo._get_gravity_snapshot_cached()
        # Delete the file; a cache HIT must not need to read it again.
        import core.learn.gravity_engine as ge
        os.remove(ge._index.path)
        second = self._uo._get_gravity_snapshot_cached()
        self.assertIs(first, second)

    def test_cache_expires_and_reflects_new_data_after_ttl(self):
        self._uo._get_gravity_snapshot_cached()
        # Force expiry without sleeping in the test.
        self._uo._gravity_snapshot_cache["ts"] -= (self._uo._GRAVITY_SNAPSHOT_CACHE_TTL + 1)

        import core.learn.gravity_engine as ge
        ge._index.record_event("fp_b", domain="sales_trends", intent="sale", cc_score=0.5)

        refreshed = self._uo._get_gravity_snapshot_cached()
        self.assertEqual(refreshed["gravity_total"], 2)

    def test_collect_gravity_engine_copies_cached_lists_not_aliases(self):
        """_collect_gravity_engine must not mutate the cached list objects
        (e.g. via the eToro injection step) — verified by checking two
        separate snapshots don't share list identity."""
        from core.self_observation.universe_observer import UniverseSnapshot, _collect_gravity_engine

        snap1 = UniverseSnapshot()
        _collect_gravity_engine(snap1)
        snap2 = UniverseSnapshot()
        _collect_gravity_engine(snap2)

        self.assertIsNot(snap1.gravity_stars, snap2.gravity_stars)
        self.assertEqual(len(snap1.gravity_stars), len(snap2.gravity_stars))


if __name__ == "__main__":
    unittest.main()
