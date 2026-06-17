"""
Tests for Domain Knowledge Library.

Covers:
  - Pattern elevation (new + merge)
  - Privacy guarantees (no tenant_id in library)
  - Weighted average merging across tenants
  - Domain priors generation for tenant seeding
  - Threshold enforcement (immature patterns rejected)
  - is_strong property
  - Persistence (save + load round-trip)
  - API endpoints

All tests use a temp directory — no side effects on real data.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from core.domain_knowledge import (
    AbstractPattern,
    elevate_pattern,
    get_domain_priors,
    get_domain_summary,
    seed_tenant_priors,
    list_domains,
    try_elevate_from_gravity,
    _load_library,
    _save_library,
    _LIBRARY_DIR,
    MIN_SAMPLE,
    MIN_WIN_RATE,
    MIN_EXPECTANCY,
)


class DomainKnowledgeTestBase(unittest.TestCase):
    """Base class that redirects domain library to a temp dir."""

    def setUp(self):
        self._original_dir = _LIBRARY_DIR
        self._temp_dir = tempfile.mkdtemp()
        # Patch the module-level variable
        import core.domain_knowledge as dk
        dk._LIBRARY_DIR = self._temp_dir

    def tearDown(self):
        import core.domain_knowledge as dk
        dk._LIBRARY_DIR = self._original_dir
        shutil.rmtree(self._temp_dir, ignore_errors=True)


class TestElevatePattern(DomainKnowledgeTestBase):
    """Test pattern elevation to domain library."""

    def test_elevate_new_pattern(self):
        result = elevate_pattern(
            domain="telefonia",
            pattern_type="sale",
            conditions_signature="conditions_A|conditions_B",
            win_rate=65.0,
            expectancy=0.5,
            avg_win_pct=1.2,
            avg_loss_pct=0.4,
            sample_size=20,
            tenant_id="t_abc123",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.domain, "telefonia")
        self.assertEqual(result.contributing_tenants, 1)
        self.assertEqual(result.sample_size, 20)

    def test_elevate_rejects_immature_sample(self):
        result = elevate_pattern(
            domain="telefonia", pattern_type="sale",
            conditions_signature="c", win_rate=70.0,
            expectancy=0.5, avg_win_pct=1.0, avg_loss_pct=0.3,
            sample_size=10,  # < MIN_SAMPLE (15)
            tenant_id="t_1",
        )
        self.assertIsNone(result)

    def test_elevate_rejects_low_win_rate(self):
        result = elevate_pattern(
            domain="telefonia", pattern_type="sale",
            conditions_signature="c", win_rate=40.0,  # < 55%
            expectancy=0.5, avg_win_pct=1.0, avg_loss_pct=0.3,
            sample_size=20, tenant_id="t_1",
        )
        self.assertIsNone(result)

    def test_elevate_rejects_negative_expectancy(self):
        result = elevate_pattern(
            domain="telefonia", pattern_type="sale",
            conditions_signature="c", win_rate=60.0,
            expectancy=-0.1,  # <= 0
            avg_win_pct=1.0, avg_loss_pct=0.3,
            sample_size=20, tenant_id="t_1",
        )
        self.assertIsNone(result)


class TestMergePatterns(DomainKnowledgeTestBase):
    """Test weighted average merging when multiple tenants elevate the same pattern."""

    def test_merge_increases_tenant_count(self):
        # Tenant 1
        elevate_pattern(
            domain="retail", pattern_type="return",
            conditions_signature="c1|c2", win_rate=60.0,
            expectancy=0.4, avg_win_pct=1.0, avg_loss_pct=0.5,
            sample_size=20, tenant_id="t_1",
        )
        # Tenant 2 — same pattern signature
        result = elevate_pattern(
            domain="retail", pattern_type="return",
            conditions_signature="c1|c2", win_rate=70.0,
            expectancy=0.6, avg_win_pct=1.5, avg_loss_pct=0.4,
            sample_size=30, tenant_id="t_2",
        )
        self.assertEqual(result.contributing_tenants, 2)
        self.assertEqual(result.sample_size, 50)  # 20 + 30

    def test_merge_weighted_average(self):
        # Tenant 1: N=20, WR=60%
        elevate_pattern(
            domain="retail", pattern_type="sale",
            conditions_signature="c", win_rate=60.0,
            expectancy=0.3, avg_win_pct=1.0, avg_loss_pct=0.5,
            sample_size=20, tenant_id="t_1",
        )
        # Tenant 2: N=30, WR=70%
        result = elevate_pattern(
            domain="retail", pattern_type="sale",
            conditions_signature="c", win_rate=70.0,
            expectancy=0.7, avg_win_pct=1.5, avg_loss_pct=0.3,
            sample_size=30, tenant_id="t_2",
        )
        # Weighted: (60*20/50 + 70*30/50) = 24 + 42 = 66
        self.assertAlmostEqual(result.win_rate, 66.0, places=0)
        # Expectancy: (0.3*20/50 + 0.7*30/50) = 0.12 + 0.42 = 0.54
        self.assertAlmostEqual(result.expectancy, 0.54, places=1)


class TestPrivacy(DomainKnowledgeTestBase):
    """Verify no tenant-identifying data leaks into the domain library."""

    def test_no_tenant_id_in_file(self):
        elevate_pattern(
            domain="finance", pattern_type="trade",
            conditions_signature="c", win_rate=65.0,
            expectancy=0.5, avg_win_pct=1.2, avg_loss_pct=0.4,
            sample_size=20, tenant_id="t_secret_abc123",
        )
        import core.domain_knowledge as dk
        path = os.path.join(dk._LIBRARY_DIR, "finance.json")
        with open(path) as f:
            content = f.read()
        self.assertNotIn("t_secret_abc123", content)
        self.assertNotIn("tenant_id", content)

    def test_abstract_pattern_has_no_tenant_field(self):
        fields = AbstractPattern.__dataclass_fields__
        self.assertNotIn("tenant_id", fields)
        self.assertNotIn("tenant", fields)


class TestDomainPriors(DomainKnowledgeTestBase):
    """Test prior generation for new tenant seeding."""

    def test_seed_returns_gravity_events(self):
        elevate_pattern(
            domain="telecom", pattern_type="churn",
            conditions_signature="c1|c2", win_rate=70.0,
            expectancy=0.8, avg_win_pct=1.5, avg_loss_pct=0.4,
            sample_size=25, tenant_id="t_1",
        )
        priors = seed_tenant_priors("telecom")
        self.assertEqual(len(priors), 1)
        event = priors[0]
        self.assertIn("fingerprint", event)
        self.assertTrue(event["fingerprint"].startswith("prior:"))
        self.assertEqual(event["impact"], "low")
        self.assertEqual(event["domain"], "telecom")
        self.assertIn("PRIOR", event["outcome"])
        self.assertIn("DOMAIN PRIOR", event["summary"])

    def test_seed_empty_domain_returns_empty(self):
        priors = seed_tenant_priors("nonexistent")
        self.assertEqual(len(priors), 0)

    def test_seed_skips_weak_patterns(self):
        # Directly write a weak pattern to the library
        weak = AbstractPattern(
            signature="weak:c", domain="telecom",
            pattern_type="weak", conditions_signature="c",
            win_rate=40.0, expectancy=-0.1,
            sample_size=5, contributing_tenants=1,
        )
        _save_library("telecom", {"weak:c": weak})
        priors = seed_tenant_priors("telecom")
        self.assertEqual(len(priors), 0)


class TestIsStrong(DomainKnowledgeTestBase):
    """Test the is_strong property."""

    def test_single_tenant_not_strong(self):
        p = AbstractPattern(
            signature="s", domain="d", pattern_type="t",
            conditions_signature="c", win_rate=70.0,
            expectancy=0.5, sample_size=20,
            contributing_tenants=1,
        )
        self.assertFalse(p.is_strong)

    def test_two_tenants_is_strong(self):
        p = AbstractPattern(
            signature="s", domain="d", pattern_type="t",
            conditions_signature="c", win_rate=70.0,
            expectancy=0.5, sample_size=40,
            contributing_tenants=2,
        )
        self.assertTrue(p.is_strong)


class TestPersistence(DomainKnowledgeTestBase):
    """Test save/load round-trip."""

    def test_round_trip(self):
        elevate_pattern(
            domain="test_domain", pattern_type="event",
            conditions_signature="a|b", win_rate=60.0,
            expectancy=0.3, avg_win_pct=1.0, avg_loss_pct=0.5,
            sample_size=18, tenant_id="t_1",
        )
        loaded = get_domain_priors("test_domain")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].pattern_type, "event")
        self.assertEqual(loaded[0].sample_size, 18)

    def test_list_domains(self):
        elevate_pattern(
            domain="domain_a", pattern_type="e",
            conditions_signature="c", win_rate=60.0,
            expectancy=0.3, avg_win_pct=1.0, avg_loss_pct=0.5,
            sample_size=20, tenant_id="t_1",
        )
        elevate_pattern(
            domain="domain_b", pattern_type="e",
            conditions_signature="c", win_rate=65.0,
            expectancy=0.5, avg_win_pct=1.2, avg_loss_pct=0.4,
            sample_size=25, tenant_id="t_2",
        )
        domains = list_domains()
        self.assertIn("domain_a", domains)
        self.assertIn("domain_b", domains)


class TestDomainSummary(DomainKnowledgeTestBase):
    """Test domain summary computation."""

    def test_summary_empty_domain(self):
        s = get_domain_summary("empty")
        self.assertEqual(s["patterns"], 0)

    def test_summary_with_patterns(self):
        elevate_pattern(
            domain="sumtest", pattern_type="a",
            conditions_signature="c1", win_rate=60.0,
            expectancy=0.3, avg_win_pct=1.0, avg_loss_pct=0.5,
            sample_size=20, tenant_id="t_1",
        )
        elevate_pattern(
            domain="sumtest", pattern_type="b",
            conditions_signature="c2", win_rate=70.0,
            expectancy=0.7, avg_win_pct=1.5, avg_loss_pct=0.3,
            sample_size=30, tenant_id="t_2",
        )
        s = get_domain_summary("sumtest")
        self.assertEqual(s["patterns"], 2)
        self.assertEqual(s["total_observations"], 50)
        self.assertAlmostEqual(s["avg_win_rate"], 65.0, places=0)


class TestExistingTestsUnbroken(unittest.TestCase):
    """Verify the hooks don't break existing modules on import."""

    def test_domain_ingester_imports(self):
        from core.domain_ingester import ingest_event, event_to_text
        self.assertTrue(callable(ingest_event))

    def test_tenant_imports(self):
        from core.tenant import create_tenant, validate_key
        self.assertTrue(callable(create_tenant))

    def test_app_imports(self):
        from services.core.routes import domain_knowledge_api
        self.assertTrue(hasattr(domain_knowledge_api, "router"))


if __name__ == "__main__":
    unittest.main()
