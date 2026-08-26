"""
Tests for scripts/calibrate_sales_trends.py's _BulkGravityIndex.

record_event() normally re-reads/rewrites the whole JSON index on every
call (O(N) per call, O(N^2) for a bulk historical load). _BulkGravityIndex
caches in memory and only persists via flush(). This must NOT change the
result: flushing the bulk index must be byte-for-byte equivalent to
calling record_event() row by row against a normal (disk-backed)
GravityIndex. Uses small synthetic data only — the real Online Retail II
dataset is never touched in CI (see the script's module docstring).
"""
from __future__ import annotations

import os
import random
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.learn.gravity_engine import GravityIndex
from scripts.calibrate_sales_trends import _BulkGravityIndex


def _synthetic_events(n: int, n_fingerprints: int, seed: int):
    """Deterministic synthetic (fingerprint, event_timestamp) pairs."""
    rng = random.Random(seed)
    t0 = datetime(2019, 1, 1, tzinfo=timezone.utc)
    events = []
    for i in range(n):
        fp = f"sales_trends:sale:product=SKU{rng.randint(0, n_fingerprints - 1)}|region=EU"
        ts = (t0 + timedelta(hours=rng.randint(0, 24 * 400))).isoformat()
        events.append((fp, ts))
    return events


class TestBulkIndexParity(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="vectrax_test_bulk_index_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_flush_matches_row_by_row_record_event(self):
        events = _synthetic_events(n=500, n_fingerprints=20, seed=1)

        # Row-by-row against a normal, disk-backed GravityIndex.
        normal_path = os.path.join(self.tmpdir, "normal.json")
        normal_idx = GravityIndex(path=normal_path)
        for fp, ts in events:
            normal_idx.record_event(fp, domain="sales_trends", intent="sale", event_timestamp=ts)

        # Same events through the bulk in-memory variant, flushed once at the end.
        bulk_path = os.path.join(self.tmpdir, "bulk.json")
        bulk_idx = _BulkGravityIndex(path=bulk_path)
        for fp, ts in events:
            bulk_idx.record_event(fp, domain="sales_trends", intent="sale", event_timestamp=ts)
        bulk_idx.flush()

        normal_records = {k: v.to_dict() for k, v in normal_idx.load_raw().items()}
        bulk_records = {k: v.to_dict() for k, v in GravityIndex(path=bulk_path).load_raw().items()}

        self.assertEqual(set(normal_records.keys()), set(bulk_records.keys()))
        self.assertEqual(normal_records, bulk_records)

    def test_flush_persists_to_disk(self):
        bulk_path = os.path.join(self.tmpdir, "bulk.json")
        idx = _BulkGravityIndex(path=bulk_path)
        idx.record_event("fp_a", domain="sales_trends", intent="sale")
        self.assertFalse(os.path.isfile(bulk_path))  # not yet persisted

        idx.flush()
        self.assertTrue(os.path.isfile(bulk_path))
        reloaded = GravityIndex(path=bulk_path).get("fp_a")
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.hits, 1)

    def test_does_not_touch_disk_between_calls(self):
        """The whole point of the bulk variant: no disk I/O until flush()."""
        bulk_path = os.path.join(self.tmpdir, "bulk.json")
        idx = _BulkGravityIndex(path=bulk_path)
        for i in range(50):
            idx.record_event(f"fp_{i}", domain="sales_trends", intent="sale")
        self.assertFalse(os.path.isfile(bulk_path))
        self.assertEqual(len(idx._cache), 50)


if __name__ == "__main__":
    unittest.main()
