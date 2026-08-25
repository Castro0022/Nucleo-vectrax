"""
Tests for the Vectrax Gravity Memory Engine.

Covers:
    1. Absolute Registration (Law 1)
    2. Promotion / Déjà Vu (Law 3)
    3. Decay (GRAVITY_DECAY_V1)
    4. Constellation compaction
    5. Cognitive Silence (Law 5)
    6. Anti-amnesia (high-impact decay 3× slower)
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# Ensure project root is importable
# ---------------------------------------------------------------------------
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.learn.schemas import GravityRecord, ConstellationRecord, Tier, TIER_ORDER, decimate_history
from core.learn.gravity_engine import GravityIndex, MAX_ACTIVATION_HISTORY
from core.learn.gravity_decay import (
    should_demote,
    demote,
    apply_decay,
    DECAY_THRESHOLDS,
)
from core.learn.constellation import (
    compact,
    load_constellation,
    list_constellations,
    COMPACTION_THRESHOLD,
    CONSTELLATIONS_DIR,
)
from core.learn.resonance import (
    compute_resonance,
    should_speak,
    SILENCE_THRESHOLD,
)


class _TempGravityMixin:
    """Create a temporary vault dir for each test."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="vectrax_test_gravity_")
        self.index_path = os.path.join(self.tmpdir, "gravity_index.json")
        self.idx = GravityIndex(path=self.index_path)
        # Patch constellations dir
        self._orig_cdir = CONSTELLATIONS_DIR
        import core.learn.constellation as cmod
        cmod.CONSTELLATIONS_DIR = os.path.join(self.tmpdir, "constellations")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        import core.learn.constellation as cmod
        cmod.CONSTELLATIONS_DIR = self._orig_cdir


# ===================================================================
# 1. Absolute Registration
# ===================================================================

class TestAbsoluteRegistration(_TempGravityMixin, unittest.TestCase):
    """Law 1: every event enters the gravity index; nothing is deleted."""

    def test_new_event_creates_hot_record(self):
        rec, promo = self.idx.record_event("fp_001", cc_score=0.5, impact="low")
        self.assertEqual(rec.tier, "HOT")
        self.assertEqual(rec.hits, 1)
        self.assertIsNone(promo)

    def test_repeated_event_increments_hits(self):
        self.idx.record_event("fp_002")
        rec, _ = self.idx.record_event("fp_002")
        self.assertEqual(rec.hits, 2)

    def test_record_persisted(self):
        self.idx.record_event("fp_003")
        # Reload from disk
        idx2 = GravityIndex(path=self.index_path)
        rec = idx2.get("fp_003")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.hits, 1)

    def test_multiple_fingerprints(self):
        for i in range(5):
            self.idx.record_event(f"fp_{i:03d}")
        counts = self.idx.tier_counts()
        self.assertEqual(counts["HOT"], 5)

    def test_no_deletion_mechanism(self):
        """There is no delete method on GravityIndex."""
        self.assertFalse(hasattr(self.idx, "delete"))
        self.assertFalse(hasattr(self.idx, "remove"))

    def test_outcome_history_tracked(self):
        self.idx.record_event("fp_oh", outcome="success")
        rec, _ = self.idx.record_event("fp_oh", outcome="failure")
        self.assertEqual(rec.outcome_history, ["success", "failure"])

    def test_summary_truncated(self):
        long_summary = "A" * 500
        rec, _ = self.idx.record_event("fp_trunc", summary=long_summary)
        self.assertLessEqual(len(rec.summary), 200)


# ===================================================================
# 2. Promotion / Déjà Vu
# ===================================================================

class TestPromotion(_TempGravityMixin, unittest.TestCase):
    """Law 3: reappearing patterns promote to hotter tiers."""

    def _force_tier(self, fp: str, tier: str, hits: int = 0,
                    last_seen: str | None = None):
        """Insert a record at a specific tier for testing."""
        records = self.idx.load_raw()
        now = datetime.now(timezone.utc).isoformat()
        rec = GravityRecord(
            fingerprint=fp, tier=tier, hits=hits,
            first_seen=now, last_seen=last_seen or now,
        )
        records[fp] = rec
        self.idx.update_records(records)

    def test_deep_to_cold_on_reappearance(self):
        self._force_tier("fp_deep", "DEEP", hits=0)
        rec, promo = self.idx.record_event("fp_deep")
        self.assertEqual(promo, "COLD")
        self.assertEqual(rec.tier, "COLD")

    def test_cold_to_warm_on_2_hits_within_window(self):
        self._force_tier("fp_cold", "COLD", hits=1)
        rec, promo = self.idx.record_event("fp_cold", cc_score=0.5)
        self.assertEqual(promo, "WARM")
        self.assertEqual(rec.tier, "WARM")

    def test_warm_to_hot_requires_3_hits_and_high_cc(self):
        self._force_tier("fp_warm", "WARM", hits=2)
        rec, promo = self.idx.record_event("fp_warm", cc_score=0.8)
        self.assertEqual(promo, "HOT")
        self.assertEqual(rec.tier, "HOT")

    def test_warm_to_hot_blocked_by_low_cc(self):
        self._force_tier("fp_warm_low", "WARM", hits=2)
        rec, promo = self.idx.record_event("fp_warm_low", cc_score=0.2)
        self.assertIsNone(promo)
        self.assertEqual(rec.tier, "WARM")

    def test_hot_stays_hot(self):
        self._force_tier("fp_hot", "HOT", hits=10)
        rec, promo = self.idx.record_event("fp_hot", cc_score=0.9)
        self.assertIsNone(promo)
        self.assertEqual(rec.tier, "HOT")


# ===================================================================
# 3. Decay
# ===================================================================

class TestDecay(_TempGravityMixin, unittest.TestCase):
    """GRAVITY_DECAY_V1: idle records demote according to thresholds."""

    def _make_old_record(self, fp: str, tier: str, idle_days: int,
                         decay_factor: float = 1.0):
        """Insert a record whose last_seen is `idle_days` ago."""
        records = self.idx.load_raw()
        past = (datetime.now(timezone.utc) - timedelta(days=idle_days)).isoformat()
        rec = GravityRecord(
            fingerprint=fp, tier=tier, hits=5,
            first_seen=past, last_seen=past,
            decay_factor=decay_factor,
        )
        records[fp] = rec
        self.idx.update_records(records)
        return rec

    def test_hot_demotes_after_7_days(self):
        rec = self._make_old_record("fp_h7", "HOT", idle_days=8)
        self.assertTrue(should_demote(rec))
        new = demote(rec)
        self.assertEqual(new, "WARM")

    def test_hot_stays_if_recent(self):
        rec = self._make_old_record("fp_h3", "HOT", idle_days=3)
        self.assertFalse(should_demote(rec))

    def test_warm_demotes_after_45_days(self):
        rec = self._make_old_record("fp_w46", "WARM", idle_days=46)
        new = demote(rec)
        self.assertEqual(new, "COLD")

    def test_cold_demotes_after_180_days(self):
        rec = self._make_old_record("fp_c181", "COLD", idle_days=181)
        new = demote(rec)
        self.assertEqual(new, "DEEP")

    def test_deep_never_demotes(self):
        rec = self._make_old_record("fp_deep_old", "DEEP", idle_days=999)
        self.assertFalse(should_demote(rec))

    def test_apply_decay_bulk(self):
        self._make_old_record("a", "HOT", idle_days=10)
        self._make_old_record("b", "WARM", idle_days=50)
        self._make_old_record("c", "HOT", idle_days=2)  # should NOT demote
        demoted = apply_decay(self.idx)
        self.assertIn("a", demoted)
        self.assertIn("b", demoted)
        self.assertNotIn("c", demoted)


# ===================================================================
# 4. Constellation compaction
# ===================================================================

class TestConstellation(_TempGravityMixin, unittest.TestCase):
    """Compression: ≥10 000 similar events → 1 constellation."""

    def test_below_threshold_no_constellation(self):
        for i in range(100):
            self.idx.record_event(f"fp_{i:05d}", domain="dev", intent="BUG_FIX")
        created = compact(self.idx)
        self.assertEqual(len(created), 0)

    def test_above_threshold_creates_constellation(self):
        # Manually insert 10 000+ records of the same domain:intent
        records = {}
        now = datetime.now(timezone.utc).isoformat()
        for i in range(COMPACTION_THRESHOLD):
            fp = f"fp_{i:06d}"
            records[fp] = GravityRecord(
                fingerprint=fp, tier="COLD", hits=1,
                first_seen=now, last_seen=now,
                domain="dev", intent="REFACTOR",
            )
        self.idx.update_records(records)

        created = compact(self.idx)
        self.assertEqual(len(created), 1)
        c = created[0]
        self.assertEqual(c.total_events, COMPACTION_THRESHOLD)
        self.assertEqual(c.domain, "dev")
        self.assertEqual(c.intent, "REFACTOR")

        # Members should be tagged
        tagged = self.idx.get(f"fp_{0:06d}")
        self.assertIsNotNone(tagged.constellation_id)

    def test_constellation_persisted(self):
        records = {}
        now = datetime.now(timezone.utc).isoformat()
        for i in range(COMPACTION_THRESHOLD):
            fp = f"fp_{i:06d}"
            records[fp] = GravityRecord(
                fingerprint=fp, tier="COLD", hits=1,
                first_seen=now, last_seen=now,
                domain="ops", intent="INFRA",
            )
        self.idx.update_records(records)
        created = compact(self.idx)
        cid = created[0].constellation_id

        loaded = load_constellation(cid)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.total_events, COMPACTION_THRESHOLD)

    def test_list_constellations(self):
        records = {}
        now = datetime.now(timezone.utc).isoformat()
        for i in range(COMPACTION_THRESHOLD):
            fp = f"fp_{i:06d}"
            records[fp] = GravityRecord(
                fingerprint=fp, tier="COLD", hits=1,
                first_seen=now, last_seen=now,
                domain="ml", intent="PERF",
            )
        self.idx.update_records(records)
        compact(self.idx)

        all_c = list_constellations()
        self.assertGreaterEqual(len(all_c), 1)


# ===================================================================
# 5. Cognitive Silence (Law 5)
# ===================================================================

class TestSilence(unittest.TestCase):
    """If resonance is below threshold, the system stays silent."""

    def test_none_record_is_silent(self):
        self.assertFalse(should_speak(None))

    def test_deep_low_cc_is_silent(self):
        rec = GravityRecord(
            fingerprint="fp_silent", tier="DEEP", hits=1,
            first_seen="", last_seen="",
            cc_score=0.0, freq=0.1,
        )
        res = compute_resonance(rec)
        self.assertLess(res, SILENCE_THRESHOLD)
        self.assertFalse(should_speak(rec))

    def test_hot_high_cc_speaks(self):
        rec = GravityRecord(
            fingerprint="fp_loud", tier="HOT", hits=50,
            first_seen="", last_seen="",
            cc_score=0.9, freq=10.0,
        )
        res = compute_resonance(rec)
        self.assertGreaterEqual(res, SILENCE_THRESHOLD)
        self.assertTrue(should_speak(rec))

    def test_resonance_bounded_0_1(self):
        rec = GravityRecord(
            fingerprint="fp_max", tier="HOT", hits=9999,
            first_seen="", last_seen="",
            cc_score=1.0, freq=999.0,
        )
        res = compute_resonance(rec)
        self.assertGreaterEqual(res, 0.0)
        self.assertLessEqual(res, 1.0)


# ===================================================================
# 6. Anti-amnesia
# ===================================================================

class TestAntiAmnesia(_TempGravityMixin, unittest.TestCase):
    """High-impact events decay 3× slower."""

    def test_high_impact_gets_3x_factor(self):
        rec, _ = self.idx.record_event("fp_hi", impact="high")
        self.assertEqual(rec.decay_factor, 3.0)

    def test_low_impact_gets_1x_factor(self):
        rec, _ = self.idx.record_event("fp_lo", impact="low")
        self.assertEqual(rec.decay_factor, 1.0)

    def test_high_impact_survives_normal_decay(self):
        """A HOT high-impact record at 8 days idle should NOT demote
        (8 < 7 × 3 = 21)."""
        records = self.idx.load_raw()
        past = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        rec = GravityRecord(
            fingerprint="fp_survive", tier="HOT", hits=5,
            first_seen=past, last_seen=past,
            impact="high", decay_factor=3.0,
        )
        records["fp_survive"] = rec
        self.idx.update_records(records)

        self.assertFalse(should_demote(rec))

    def test_high_impact_eventually_demotes(self):
        """A HOT high-impact record at 22 days idle SHOULD demote
        (22 >= 7 × 3 = 21)."""
        records = self.idx.load_raw()
        past = (datetime.now(timezone.utc) - timedelta(days=22)).isoformat()
        rec = GravityRecord(
            fingerprint="fp_eventual", tier="HOT", hits=5,
            first_seen=past, last_seen=past,
            impact="high", decay_factor=3.0,
        )
        records["fp_eventual"] = rec
        self.idx.update_records(records)

        self.assertTrue(should_demote(rec))
        new = demote(rec)
        self.assertEqual(new, "WARM")


# ===================================================================
# Schema helpers
# ===================================================================

class TestSchemas(unittest.TestCase):
    """Verify Tier enum and record serialisation."""

    def test_tier_order(self):
        self.assertEqual([t.value for t in TIER_ORDER], ["HOT", "WARM", "COLD", "DEEP"])

    def test_tier_weights(self):
        self.assertEqual(Tier.HOT.weight, 1.0)
        self.assertEqual(Tier.DEEP.weight, 0.2)

    def test_gravity_record_roundtrip(self):
        rec = GravityRecord(
            fingerprint="fp_rt", tier="HOT", hits=3,
            first_seen="2025-01-01T00:00:00+00:00",
            last_seen="2025-06-01T00:00:00+00:00",
            cc_score=0.75, impact="high", domain="dev",
        )
        d = rec.to_dict()
        rec2 = GravityRecord.from_dict(d)
        self.assertEqual(rec.fingerprint, rec2.fingerprint)
        self.assertEqual(rec.tier, rec2.tier)
        self.assertEqual(rec.hits, rec2.hits)
        self.assertEqual(rec.cc_score, rec2.cc_score)

    def test_constellation_record_roundtrip(self):
        c = ConstellationRecord(
            constellation_id="abc123",
            pattern_fingerprints=["a", "b"],
            total_events=2, frequency=1.5,
            variance=0.01, domain="dev", intent="BUG_FIX",
            created_at="2025-06-01T00:00:00+00:00",
        )
        d = c.to_dict()
        c2 = ConstellationRecord.from_dict(d)
        self.assertEqual(c.constellation_id, c2.constellation_id)
        self.assertEqual(c.total_events, c2.total_events)


# ===================================================================
# 7. decimate_history — bounded, span-preserving retention
# ===================================================================

class TestDecimateHistory(unittest.TestCase):
    """Pure helper used to bound GravityRecord.activation_history."""

    def test_no_op_when_within_bound(self):
        h = ["a", "b", "c"]
        self.assertEqual(decimate_history(h, 5), h)

    def test_bounded_after_exceeding(self):
        h = [str(i) for i in range(100)]
        result = decimate_history(h, 10)
        self.assertLessEqual(len(result), 10)

    def test_preserves_first_and_last(self):
        h = [str(i) for i in range(50)]
        result = decimate_history(h, 8)
        self.assertEqual(result[0], h[0])
        self.assertEqual(result[-1], h[-1])

    def test_max_size_zero_returns_empty(self):
        self.assertEqual(decimate_history(["a", "b"], 0), [])

    def test_max_size_one_returns_last(self):
        self.assertEqual(decimate_history(["a", "b", "c"], 1), ["c"])

    def test_max_size_two_returns_first_and_last(self):
        self.assertEqual(decimate_history(["a", "b", "c", "d"], 2), ["a", "d"])

    def test_incremental_append_stays_bounded_and_preserves_span(self):
        """Simulates how record_event uses it: append one at a time."""
        history: list[str] = []
        max_size = 16
        for i in range(500):
            history.append(str(i))
            history = decimate_history(history, max_size)
            self.assertLessEqual(len(history), max_size)
        self.assertEqual(history[0], "0")
        self.assertEqual(history[-1], "499")


# ===================================================================
# 8. Temporal replay — event_timestamp + activation_history
# ===================================================================

class TestTemporalReplay(_TempGravityMixin, unittest.TestCase):
    """Historical replay via event_timestamp must not disturb the default
    (no-timestamp) behaviour, and must feed activation_history correctly."""

    def test_event_timestamp_sets_first_and_last_seen(self):
        rec, _ = self.idx.record_event("fp_ts", event_timestamp="2020-01-01T00:00:00+00:00")
        self.assertEqual(rec.first_seen, "2020-01-01T00:00:00+00:00")
        self.assertEqual(rec.last_seen, "2020-01-01T00:00:00+00:00")
        self.assertIn("2020-01-01T00:00:00+00:00", rec.activation_history)

    def test_out_of_order_replay_expands_bracket(self):
        """A later event recorded first, then an earlier one: first_seen must
        move backward and last_seen must not move backward."""
        self.idx.record_event("fp_replay", event_timestamp="2020-06-01T00:00:00+00:00")
        rec, _ = self.idx.record_event("fp_replay", event_timestamp="2020-01-01T00:00:00+00:00")
        self.assertEqual(rec.first_seen, "2020-01-01T00:00:00+00:00")
        self.assertEqual(rec.last_seen, "2020-06-01T00:00:00+00:00")
        self.assertEqual(len(rec.activation_history), 2)

    def test_omitted_event_timestamp_behaves_like_before(self):
        rec, _ = self.idx.record_event("fp_now")
        self.assertEqual(len(rec.activation_history), 1)
        ts = datetime.fromisoformat(rec.last_seen)
        self.assertLess((datetime.now(timezone.utc) - ts).total_seconds(), 5)

    def test_invalid_event_timestamp_falls_back_to_now(self):
        rec, _ = self.idx.record_event("fp_bad_ts", event_timestamp="not-a-date")
        ts = datetime.fromisoformat(rec.last_seen)
        self.assertLess((datetime.now(timezone.utc) - ts).total_seconds(), 5)

    def test_activation_history_bounded_by_max_constant(self):
        for i in range(MAX_ACTIVATION_HISTORY + 50):
            month = (i % 28) + 1
            self.idx.record_event(
                "fp_bound", event_timestamp=f"2020-01-{month:02d}T00:00:00+00:00",
            )
        rec = self.idx.get("fp_bound")
        self.assertLessEqual(len(rec.activation_history), MAX_ACTIVATION_HISTORY)
        self.assertEqual(rec.hits, MAX_ACTIVATION_HISTORY + 50)


if __name__ == "__main__":
    unittest.main()
