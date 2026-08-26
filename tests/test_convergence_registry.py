"""Tests for core.learn.convergence_registry.

Covers:
    1. A<->B identity symmetry
    2. Repeat detection increments confirmation, not row count
    3. Dissolution + reappearance preserves convergence_id
    4. A known alternate representation resolves to the same entity
    5. Ambiguous/corrupted identities are reported, never merged
    6. Global scan covers multiple domain pairs (no market anchor)
    7. Census / Observer report the identical canonical total
    8. Legacy convergence_events ledger is left untouched
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.learn.convergence_registry import (  # noqa: E402
    compute_convergence_id,
    connect,
    count_canonical_convergences,
    get_confirmation_total,
    normalize_candidate,
    normalize_entity_id,
    record_convergence_snapshot,
)
from core.learn.gravity_engine import GravityIndex  # noqa: E402


class _TempRegistryMixin:
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="vectrax_test_convreg_")
        self.db_path = os.path.join(self.tmpdir, "convergence_history.db")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestNormalizeEntityId(unittest.TestCase):
    def test_exact_live_match(self):
        live = {"market:AAPL": "market"}
        entity = normalize_entity_id("market:AAPL", live_fingerprints=live)
        self.assertEqual(entity.canonical_id, "market:AAPL")
        self.assertEqual(entity.domain, "market")
        self.assertEqual(entity.resolution, "exact_live_match")

    def test_evidence_reconstructed_alternate_representation(self):
        """Bare 'AAPL' with a recorded domain hint of 'market' resolves to
        the live 'market:AAPL' fingerprint — real evidence, not a guess."""
        live = {"market:AAPL": "market"}
        entity = normalize_entity_id(
            "AAPL", known_domain="market", live_fingerprints=live,
        )
        self.assertEqual(entity.canonical_id, "market:AAPL")
        self.assertEqual(entity.resolution, "evidence_reconstructed")

    def test_no_reconstruction_without_domain_hint(self):
        """Without a recorded domain hint, a bare id is never guessed into
        a prefixed form — it stays its own distinct identity."""
        live = {"market:AAPL": "market"}
        entity = normalize_entity_id("AAPL", live_fingerprints=live)
        self.assertEqual(entity.canonical_id, "AAPL")
        self.assertEqual(entity.resolution, "legacy_verbatim")

    def test_no_reconstruction_when_target_not_live(self):
        """A domain hint that doesn't resolve to a real live fingerprint
        must not be silently merged into anything."""
        entity = normalize_entity_id("AAPL", known_domain="market", live_fingerprints={})
        self.assertEqual(entity.canonical_id, "AAPL")
        self.assertNotEqual(entity.resolution, "evidence_reconstructed")

    def test_ambiguous_corrupted_fragment_reported(self):
        context = {"freight_logistics:load_booking:region=Pacific|carrier=BudgetMove"}
        entity = normalize_entity_id(
            "region=Pacific|carrier=BudgetMove", context_ids=context,
        )
        self.assertEqual(entity.resolution, "ambiguous_corrupted_fragment")

    def test_bare_hash_not_falsely_flagged_ambiguous(self):
        """A legitimate no-colon 'unknown' domain fingerprint must not be
        mistaken for a corrupted fragment."""
        context = {"4779af39f0f0616e", "market:AAPL"}
        entity = normalize_entity_id("4779af39f0f0616e", context_ids=context)
        self.assertNotEqual(entity.resolution, "ambiguous_corrupted_fragment")


class TestComputeConvergenceId(unittest.TestCase):
    def test_order_independence(self):
        live = {"market:AAPL": "market", "unknown:xyz": "unknown"}
        a = normalize_entity_id("market:AAPL", live_fingerprints=live)
        b = normalize_entity_id("unknown:xyz", live_fingerprints=live)
        self.assertEqual(compute_convergence_id(a, b), compute_convergence_id(b, a))

    def test_different_pairs_different_ids(self):
        live = {"market:AAPL": "market", "market:BTC": "market", "unknown:xyz": "unknown"}
        a = normalize_entity_id("market:AAPL", live_fingerprints=live)
        b = normalize_entity_id("market:BTC", live_fingerprints=live)
        c = normalize_entity_id("unknown:xyz", live_fingerprints=live)
        self.assertNotEqual(compute_convergence_id(a, b), compute_convergence_id(a, c))


class TestRecordConvergenceSnapshot(_TempRegistryMixin, unittest.TestCase):
    def _candidate(self, star_a="market:AAPL", star_b="unknown:xyz",
                    domains=("market", "unknown"), cc=0.5, hits=3):
        return {
            "type": "intent_overlap", "star_a": star_a, "star_b": star_b,
            "combined_cc": cc, "combined_hits": hits, "domains": list(domains),
        }

    def test_creates_new_convergence(self):
        live = {"market:AAPL": "market", "unknown:xyz": "unknown"}
        result = record_convergence_snapshot([self._candidate()], live, self.db_path)
        self.assertEqual(result["created"], 1)
        self.assertEqual(count_canonical_convergences(db_path=self.db_path), 1)
        self.assertEqual(count_canonical_convergences(status="active", db_path=self.db_path), 1)

    def test_repeat_increments_confirmation_not_row_count(self):
        live = {"market:AAPL": "market", "unknown:xyz": "unknown"}
        candidate = self._candidate()
        record_convergence_snapshot([candidate], live, self.db_path)
        result2 = record_convergence_snapshot([candidate], live, self.db_path)
        self.assertEqual(result2["created"], 0)
        self.assertEqual(result2["confirmed"], 1)
        self.assertEqual(count_canonical_convergences(db_path=self.db_path), 1)
        self.assertEqual(get_confirmation_total(db_path=self.db_path), 2)

    def test_dissolution_then_reappearance_preserves_id(self):
        live = {"market:AAPL": "market", "unknown:xyz": "unknown"}
        candidate = self._candidate()
        a = normalize_entity_id(candidate["star_a"], "star", "market", live)
        b = normalize_entity_id(candidate["star_b"], "star", "unknown", live)
        expected_id = compute_convergence_id(a, b)

        record_convergence_snapshot([candidate], live, self.db_path)
        dissolve_result = record_convergence_snapshot([], live, self.db_path)
        self.assertEqual(dissolve_result["dissolved"], 1)
        self.assertEqual(
            count_canonical_convergences(status="active", db_path=self.db_path), 0
        )

        reappear_result = record_convergence_snapshot([candidate], live, self.db_path)
        self.assertEqual(reappear_result["reappeared"], 1)
        self.assertEqual(reappear_result["created"], 0)
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT convergence_id, status, confirmation_count FROM convergences"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["convergence_id"], expected_id)
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["confirmation_count"], 2)

    def test_alternate_representation_confirms_same_convergence(self):
        """The explicit required test: a known alternate raw representation
        of the same live entity must resolve to the same convergence_id and
        increment confirmation_count, never create a second convergence."""
        live = {"market:AAPL": "market", "unknown:xyz": "unknown"}
        canonical_candidate = self._candidate(star_a="market:AAPL", domains=("market", "unknown"))
        alternate_candidate = self._candidate(star_a="AAPL", domains=("market", "unknown"))

        first = record_convergence_snapshot([canonical_candidate], live, self.db_path)
        self.assertEqual(first["created"], 1)

        second = record_convergence_snapshot([alternate_candidate], live, self.db_path)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["confirmed"], 1)
        self.assertEqual(count_canonical_convergences(db_path=self.db_path), 1)
        self.assertEqual(get_confirmation_total(db_path=self.db_path), 2)

    def test_ambiguous_candidate_reported_not_merged(self):
        live = {"market:AAPL": "market"}
        context_candidate = self._candidate(
            star_a="freight_logistics:load_booking:region=Pacific|carrier=BudgetMove",
            star_b="region=Pacific|carrier=BudgetMove",
        )
        result = record_convergence_snapshot([context_candidate], live, self.db_path)
        self.assertEqual(result["created"], 0)
        self.assertEqual(len(result["ambiguous"]), 1)
        self.assertEqual(count_canonical_convergences(db_path=self.db_path), 0)

    def test_multiple_domain_pairs_no_market_anchor(self):
        """Global scan candidates spanning several domain pairs, none of
        them 'market', must all be recorded as distinct convergences."""
        live = {
            "freight_logistics:a": "freight_logistics",
            "florida_real_estate:b": "florida_real_estate",
            "unknown:c": "unknown",
        }
        candidates = [
            self._candidate("freight_logistics:a", "florida_real_estate:b",
                             ("freight_logistics", "florida_real_estate")),
            self._candidate("florida_real_estate:b", "unknown:c",
                             ("florida_real_estate", "unknown")),
        ]
        result = record_convergence_snapshot(candidates, live, self.db_path)
        self.assertEqual(result["created"], 2)
        self.assertEqual(count_canonical_convergences(db_path=self.db_path), 2)


class TestCensusObserverConsistency(unittest.TestCase):
    """Census and Observer must report the identical canonical total for
    the same underlying registry state."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="vectrax_test_convreg_ssot_")
        self.db_path = os.path.join(self.tmpdir, "convergence_history.db")
        live = {"market:AAPL": "market", "unknown:xyz": "unknown"}
        record_convergence_snapshot(
            [{
                "type": "intent_overlap", "star_a": "market:AAPL",
                "star_b": "unknown:xyz", "combined_cc": 0.5, "combined_hits": 2,
                "domains": ["market", "unknown"],
            }],
            live, self.db_path,
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_census_and_registry_report_same_total(self):
        import core.learn.convergence_registry as registry_mod

        orig_db_path = registry_mod.DB_PATH
        registry_mod.DB_PATH = self.db_path
        try:
            total_direct = count_canonical_convergences()
            self.assertEqual(total_direct, 1)

            import core.universe_census as census_mod
            c = census_mod._build_census()
            self.assertEqual(c.convergences, 1)
            self.assertEqual(c.convergences_active, 1)
            self.assertEqual(c.convergence_confirmations_total, 1)
        finally:
            registry_mod.DB_PATH = orig_db_path


class TestGlobalCrossDomainScan(unittest.TestCase):
    """cross_domain_convergences() with no domain given must cover every
    domain pair, not anchor to 'market'."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="vectrax_test_gravity_global_")
        self.index_path = os.path.join(self.tmpdir, "gravity_index.json")
        self.idx = GravityIndex(path=self.index_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_global_scan_finds_non_market_pair(self):
        self.idx.record_event(
            "freight_logistics:a", domain="freight_logistics", intent="SHARED_INTENT",
            cc_score=0.6,
        )
        self.idx.record_event(
            "florida_real_estate:b", domain="florida_real_estate", intent="SHARED_INTENT",
            cc_score=0.6,
        )
        convergences = self.idx.cross_domain_convergences()
        pairs = {tuple(sorted(c["domains"])) for c in convergences}
        self.assertIn(("florida_real_estate", "freight_logistics"), pairs)

    def test_explicit_domain_pair_still_exact(self):
        self.idx.record_event("market:AAPL", domain="market", intent="AAPL", cc_score=0.6)
        self.idx.record_event("freight_logistics:a", domain="freight_logistics",
                               intent="AAPL", cc_score=0.6)
        self.idx.record_event("unknown:c", domain="unknown", intent="AAPL", cc_score=0.6)
        targeted = self.idx.cross_domain_convergences(domain_a="market", domain_b="freight_logistics")
        self.assertTrue(all(
            set(c["domains"]) <= {"market", "freight_logistics"} for c in targeted
        ))
        self.assertEqual(len(targeted), 1)


class TestBackfillLeavesLegacyLedgerIntact(_TempRegistryMixin, unittest.TestCase):
    def test_legacy_table_untouched(self):
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        import backfill_canonical_convergences as backfill_mod

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """CREATE TABLE convergence_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, timestamp REAL,
                event TEXT, star_a TEXT, star_b TEXT, type TEXT, intent TEXT,
                domains TEXT DEFAULT '[]', combined_cc REAL, combined_hits INTEGER,
                status TEXT, dissolved_at REAL DEFAULT 0, extra TEXT DEFAULT '{}'
            )"""
        )
        rows = [
            ("k1", 1.0, "birth", "market:AAPL", "unknown:xyz", "intent_overlap",
             "AAPL", '["market", "unknown"]', 0.5, 2, "active"),
            ("k1", 2.0, "dissolution", "market:AAPL", "unknown:xyz", None,
             None, "[]", 0, 0, "dissolved"),
        ]
        for row in rows:
            conn.execute(
                """INSERT INTO convergence_events
                (key, timestamp, event, star_a, star_b, type, intent, domains,
                 combined_cc, combined_hits, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                row,
            )
        conn.commit()
        conn.close()

        before = sqlite3.connect(self.db_path).execute(
            "SELECT * FROM convergence_events ORDER BY id"
        ).fetchall()

        groups, ambiguous, event_map = backfill_mod.backfill(
            self.db_path, live_fingerprints={"market:AAPL": "market", "unknown:xyz": "unknown"},
        )
        backfill_mod.apply_backfill(groups, event_map, self.db_path)

        after = sqlite3.connect(self.db_path).execute(
            "SELECT * FROM convergence_events ORDER BY id"
        ).fetchall()
        self.assertEqual(before, after)
        self.assertEqual(len(groups), 1)
        self.assertEqual(list(groups.values())[0].status, "dissolved")


if __name__ == "__main__":
    unittest.main()
