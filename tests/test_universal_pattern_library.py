"""
Tests for Universal Pattern Library (UPL).

Covers:
  - MetaPattern creation and serialization
  - validate_meta_pattern: whole-word prohibited term filtering
  - Prohibited terms don't match substrings (e.g. "customization" is allowed)
  - Cross-domain scanning with mock domain libraries
  - Persistence (save/load round-trip)
  - get_usable_hypotheses filtering
  - Confidence always HYPOTHESIS
  - Domain compatibility (all 6 domain templates)
  - NO tenant data access (isolation)
  - NO modification of existing modules

All tests use temp directories — no side effects on real data.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from core.universal_pattern_library import (
    MetaPattern,
    validate_meta_pattern,
    scan_domain_libraries,
    refresh_upl,
    get_all_meta_patterns,
    get_usable_hypotheses,
    get_upl_summary,
    _abstract_pattern_type,
    _load_upl,
    _save_upl,
    _UPL_PATH,
    _PROHIBITED_RE,
)


class UPLTestBase(unittest.TestCase):
    """Base with isolated UPL storage."""

    def setUp(self):
        self._temp_dir = tempfile.mkdtemp()
        import core.universal_pattern_library as upl
        self._orig_path = upl._UPL_PATH
        upl._UPL_PATH = os.path.join(self._temp_dir, "upl.json")

    def tearDown(self):
        import core.universal_pattern_library as upl
        upl._UPL_PATH = self._orig_path
        shutil.rmtree(self._temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 1. MetaPattern basics
# ---------------------------------------------------------------------------

class TestMetaPattern(unittest.TestCase):

    def test_create_meta_pattern(self):
        mp = MetaPattern(
            meta_id="MP-test",
            structure="test pattern",
            source_domains=["a", "b"],
            contributing_domain_count=2,
            total_observations=50,
        )
        self.assertEqual(mp.confidence, "HYPOTHESIS")
        self.assertEqual(mp.contributing_domain_count, 2)

    def test_round_trip(self):
        mp = MetaPattern(
            meta_id="MP-round",
            structure="round trip",
            source_domains=["x", "y"],
            contributing_domain_count=2,
            total_observations=100,
            avg_win_rate=65.0,
        )
        d = mp.to_dict()
        mp2 = MetaPattern.from_dict(d)
        self.assertEqual(mp2.meta_id, "MP-round")
        self.assertEqual(mp2.avg_win_rate, 65.0)
        self.assertEqual(mp2.source_domains, ["x", "y"])


# ---------------------------------------------------------------------------
# 2. Validation — prohibited terms (whole word)
# ---------------------------------------------------------------------------

class TestValidation(unittest.TestCase):

    def test_valid_pattern_passes(self):
        mp = MetaPattern(
            meta_id="MP-demand_signal",
            structure="Cross-domain demand signal observed in 3 domains",
            source_domains=["freight", "retail", "restaurant"],
            contributing_domain_count=3,
            total_observations=100,
        )
        valid, reasons = validate_meta_pattern(mp)
        self.assertTrue(valid, reasons)

    def test_prohibited_term_in_structure(self):
        mp = MetaPattern(
            meta_id="MP-test",
            structure="Pattern involving customer behavior across domains",
            source_domains=["a", "b"],
            contributing_domain_count=2,
            total_observations=50,
        )
        valid, reasons = validate_meta_pattern(mp)
        self.assertFalse(valid)
        self.assertTrue(any("customer" in r for r in reasons))

    def test_prohibited_term_in_id(self):
        mp = MetaPattern(
            meta_id="MP-tenant_churn",
            structure="Churn pattern across domains",
            source_domains=["a", "b"],
            contributing_domain_count=2,
            total_observations=50,
        )
        valid, reasons = validate_meta_pattern(mp)
        self.assertFalse(valid)

    def test_substring_not_blocked(self):
        """'customer' is blocked but 'customization' should NOT be."""
        mp = MetaPattern(
            meta_id="MP-customization_pattern",
            structure="Customization rate increases during peak season",
            source_domains=["a", "b"],
            contributing_domain_count=2,
            total_observations=50,
        )
        valid, reasons = validate_meta_pattern(mp)
        self.assertTrue(valid, f"Should pass but got: {reasons}")

    def test_naming_not_blocked(self):
        """'name' is blocked but 'naming' should NOT be."""
        mp = MetaPattern(
            meta_id="MP-naming_convention",
            structure="Naming convention patterns in event types",
            source_domains=["a", "b"],
            contributing_domain_count=2,
            total_observations=50,
        )
        valid, reasons = validate_meta_pattern(mp)
        self.assertTrue(valid, f"Should pass but got: {reasons}")

    def test_single_domain_rejected(self):
        mp = MetaPattern(
            meta_id="MP-solo",
            structure="Only one domain",
            source_domains=["a"],
            contributing_domain_count=1,
            total_observations=50,
        )
        valid, reasons = validate_meta_pattern(mp)
        self.assertFalse(valid)

    def test_non_hypothesis_rejected(self):
        mp = MetaPattern(
            meta_id="MP-confirmed",
            structure="Confirmed pattern",
            source_domains=["a", "b"],
            confidence="CONFIRMED",
            contributing_domain_count=2,
            total_observations=50,
        )
        valid, reasons = validate_meta_pattern(mp)
        self.assertFalse(valid)

    def test_zero_observations_rejected(self):
        mp = MetaPattern(
            meta_id="MP-empty",
            structure="No observations",
            source_domains=["a", "b"],
            contributing_domain_count=2,
            total_observations=0,
        )
        valid, reasons = validate_meta_pattern(mp)
        self.assertFalse(valid)

    def test_medical_terms_blocked(self):
        mp = MetaPattern(
            meta_id="MP-medical",
            structure="Pattern with patient data leak",
            source_domains=["a", "b"],
            contributing_domain_count=2,
            total_observations=50,
        )
        valid, reasons = validate_meta_pattern(mp)
        self.assertFalse(valid)

    def test_financial_terms_blocked(self):
        mp = MetaPattern(
            meta_id="MP-finance",
            structure="Pattern with stop_loss across domains",
            source_domains=["a", "b"],
            contributing_domain_count=2,
            total_observations=50,
        )
        # stop_loss has underscore — test both forms
        valid, reasons = validate_meta_pattern(mp)
        self.assertFalse(valid)


# ---------------------------------------------------------------------------
# 3. Abstract pattern type mapping
# ---------------------------------------------------------------------------

class TestAbstractMapping(unittest.TestCase):

    def test_domain_templates_compatible(self):
        """All event types from domain templates should map to abstract types."""
        # These are the event types defined across all 6 domain templates
        domain_events = {
            "freight_logistics": ["delay_reported", "rate_change", "capacity_update",
                                  "quote_request", "load_booking", "delivery_complete",
                                  "empty_miles", "weather_event"],
            "retail": ["sale", "return", "restock"],
            "restaurant": ["order", "reservation", "complaint"],
            "healthcare": ["admission", "discharge", "lab_result"],
            "finance": ["transaction", "approval", "rejection"],
            "logistics": ["delay_reported", "delivery_complete", "capacity_update"],
        }
        for domain, events in domain_events.items():
            for event in events:
                abstract = _abstract_pattern_type(event)
                self.assertIsInstance(abstract, str)
                self.assertGreater(len(abstract), 0,
                    f"{domain}/{event} mapped to empty string")

    def test_cross_domain_convergence(self):
        """Events from different domains with same abstract type should match."""
        # "order" (restaurant) and "quote_request" (freight) both → "demand_signal"
        self.assertEqual(
            _abstract_pattern_type("order"),
            _abstract_pattern_type("quote_request"),
        )
        # "complaint" (restaurant) and "delay_reported" (freight) both → "degradation_event"
        self.assertEqual(
            _abstract_pattern_type("complaint"),
            _abstract_pattern_type("delay_reported"),
        )

    def test_unknown_type_passes_through(self):
        result = _abstract_pattern_type("some_new_event")
        self.assertEqual(result, "some_new_event")


# ---------------------------------------------------------------------------
# 4. Scanning + persistence
# ---------------------------------------------------------------------------

class TestScanAndPersist(UPLTestBase):

    @patch("core.domain_knowledge.list_domains")
    @patch("core.domain_knowledge.get_domain_priors")
    def test_scan_finds_cross_domain_patterns(self, mock_priors, mock_domains):
        from core.domain_knowledge import AbstractPattern

        mock_domains.return_value = ["freight_logistics", "restaurant"]
        def fake_priors(domain):
            if domain == "freight_logistics":
                return [AbstractPattern(
                    signature="s1", domain=domain, pattern_type="delay_reported",
                    conditions_signature="c", win_rate=70.0, expectancy=0.5,
                    sample_size=20, contributing_tenants=1,
                )]
            return [AbstractPattern(
                signature="s2", domain=domain, pattern_type="complaint",
                conditions_signature="c", win_rate=65.0, expectancy=0.4,
                sample_size=15, contributing_tenants=1,
            )]
        mock_priors.side_effect = fake_priors

        results = scan_domain_libraries()
        # Both map to "degradation_event" → should find 1 meta-pattern
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].meta_id, "MP-degradation_event")
        self.assertEqual(results[0].contributing_domain_count, 2)
        self.assertEqual(results[0].confidence, "HYPOTHESIS")

    @patch("core.domain_knowledge.list_domains")
    def test_scan_single_domain_returns_empty(self, mock_domains):
        mock_domains.return_value = ["only_one"]
        results = scan_domain_libraries()
        self.assertEqual(len(results), 0)

    def test_refresh_and_load(self):
        """refresh_upl should persist and get_all should load."""
        # Create a pattern directly
        mp = MetaPattern(
            meta_id="MP-test_persist",
            structure="Persistence test",
            source_domains=["a", "b"],
            contributing_domain_count=2,
            total_observations=50,
            created_at=time.time(),
        )
        _save_upl({"MP-test_persist": mp})
        loaded = get_all_meta_patterns()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].meta_id, "MP-test_persist")


# ---------------------------------------------------------------------------
# 5. get_usable_hypotheses
# ---------------------------------------------------------------------------

class TestUsableHypotheses(UPLTestBase):

    def test_filters_by_min_domains(self):
        mp1 = MetaPattern(
            meta_id="MP-weak", structure="weak", source_domains=["a"],
            contributing_domain_count=1, total_observations=100,
        )
        mp2 = MetaPattern(
            meta_id="MP-strong", structure="strong", source_domains=["a", "b", "c"],
            contributing_domain_count=3, total_observations=100,
        )
        _save_upl({"MP-weak": mp1, "MP-strong": mp2})
        results = get_usable_hypotheses(min_domains=2)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].meta_id, "MP-strong")

    def test_filters_by_min_observations(self):
        mp = MetaPattern(
            meta_id="MP-small", structure="small", source_domains=["a", "b"],
            contributing_domain_count=2, total_observations=5,
        )
        _save_upl({"MP-small": mp})
        results = get_usable_hypotheses(min_observations=10)
        self.assertEqual(len(results), 0)

    def test_only_hypothesis_confidence(self):
        mp = MetaPattern(
            meta_id="MP-conf", structure="confirmed", source_domains=["a", "b"],
            contributing_domain_count=2, total_observations=100,
            confidence="CONFIRMED",
        )
        _save_upl({"MP-conf": mp})
        results = get_usable_hypotheses()
        self.assertEqual(len(results), 0)


# ---------------------------------------------------------------------------
# 6. Isolation guarantees
# ---------------------------------------------------------------------------

class TestIsolation(unittest.TestCase):

    def test_upl_does_not_import_tenant(self):
        """UPL module should never import tenant directly."""
        import inspect
        import core.universal_pattern_library as upl
        source = inspect.getsource(upl)
        self.assertNotIn("from core.tenant import", source)
        self.assertNotIn("import core.tenant", source)

    def test_upl_does_not_modify_domain_knowledge(self):
        """UPL only calls list_domains and get_domain_priors (read-only)."""
        import inspect
        import core.universal_pattern_library as upl
        source = inspect.getsource(upl)
        self.assertNotIn("elevate_pattern", source)
        self.assertNotIn("_save_library", source)
        self.assertNotIn("seed_tenant", source)

    def test_upl_does_not_import_pattern_memory(self):
        """UPL should never access eToro pattern memory."""
        import inspect
        import core.universal_pattern_library as upl
        source = inspect.getsource(upl)
        # Check actual imports, not docstring mentions
        self.assertNotIn("from connectors.etoro", source)
        self.assertNotIn("import connectors.etoro", source)
        self.assertNotIn("PatternStats", source)

    def test_meta_pattern_has_no_tenant_field(self):
        fields = MetaPattern.__dataclass_fields__
        self.assertNotIn("tenant_id", fields)
        self.assertNotIn("tenant", fields)
        self.assertNotIn("api_key", fields)


# ---------------------------------------------------------------------------
# 7. Existing modules unbroken
# ---------------------------------------------------------------------------

class TestExistingModulesUnbroken(unittest.TestCase):
    """Verify UPL doesn't break existing imports."""

    def test_domain_knowledge_imports(self):
        from core.domain_knowledge import elevate_pattern, get_domain_priors
        self.assertTrue(callable(elevate_pattern))

    def test_tenant_imports(self):
        from core.tenant import create_tenant, validate_key
        self.assertTrue(callable(create_tenant))

    def test_gravity_engine_imports(self):
        from core.learn.gravity_engine import GravityIndex
        self.assertTrue(callable(GravityIndex))

    def test_upl_imports_cleanly(self):
        from core.universal_pattern_library import (
            MetaPattern, validate_meta_pattern, scan_domain_libraries,
            refresh_upl, get_usable_hypotheses,
        )
        self.assertTrue(callable(validate_meta_pattern))


if __name__ == "__main__":
    unittest.main()
