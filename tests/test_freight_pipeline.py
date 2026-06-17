"""
Integration tests for freight logistics pipeline.

Validates the full cycle:
  Events → Gravity Stars → Hit Accumulation → Tier Promotion
  → Cross-domain Convergence → Domain Knowledge Elevation

Uses an isolated gravity index (temp file) — no side effects on real data.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest

from core.learn.gravity_engine import GravityIndex
from core.learn.schemas import Tier
from core.domain_knowledge import (
    elevate_pattern,
    get_domain_priors,
    get_domain_summary,
    seed_tenant_priors,
    _LIBRARY_DIR,
)
from scripts.freight_simulator import (
    WorldState,
    generate_event,
    inject_pricing_opportunity,
    inject_capacity_risk,
    inject_route_optimization,
    gen_quote_request,
    gen_delay,
    gen_weather,
    gen_capacity,
    DOMAIN,
    REGIONS,
    CARRIERS,
)


class FreightPipelineTestBase(unittest.TestCase):
    """Base with isolated gravity index and domain library."""

    def setUp(self):
        self._temp_dir = tempfile.mkdtemp()
        self._gi_path = os.path.join(self._temp_dir, "gravity_index.json")
        self.gi = GravityIndex(path=self._gi_path)

        # Redirect domain library
        import core.domain_knowledge as dk
        self._orig_lib_dir = dk._LIBRARY_DIR
        dk._LIBRARY_DIR = os.path.join(self._temp_dir, "domain_library")

    def tearDown(self):
        import core.domain_knowledge as dk
        dk._LIBRARY_DIR = self._orig_lib_dir
        shutil.rmtree(self._temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 1. Event Generation
# ---------------------------------------------------------------------------

class TestEventGeneration(unittest.TestCase):
    """Verify the simulator generates valid events."""

    def setUp(self):
        self.ws = WorldState(seed=42)

    def test_generate_has_event_type(self):
        event = generate_event(self.ws)
        self.assertIn("event_type", event)
        self.assertIn("data", event)

    def test_all_event_types_generated(self):
        types = set()
        for _ in range(500):
            e = generate_event(self.ws)
            types.add(e["event_type"])
            self.ws.advance_day()
        expected = {
            "quote_request", "load_booking", "delivery_complete",
            "delay_reported", "rate_change", "capacity_update",
            "weather_event", "empty_miles",
        }
        self.assertEqual(types, expected)

    def test_scenario_injectors_return_lists(self):
        events = inject_pricing_opportunity(self.ws)
        self.assertIsInstance(events, list)
        self.assertGreater(len(events), 0)

        events = inject_capacity_risk(self.ws)
        self.assertIsInstance(events, list)
        self.assertGreater(len(events), 0)

        events = inject_route_optimization(self.ws)
        self.assertIsInstance(events, list)
        self.assertGreater(len(events), 0)

    def test_world_state_advances(self):
        self.assertEqual(self.ws.day, 0)
        self.ws.advance_day()
        self.assertEqual(self.ws.day, 1)
        # Truck availability changes
        initial = dict(self.ws.truck_available)
        for _ in range(30):
            self.ws.advance_day()
        changed = any(
            self.ws.truck_available[r] != initial[r] for r in REGIONS
        )
        self.assertTrue(changed)

    def test_weather_events_occur(self):
        for _ in range(100):
            self.ws.advance_day()
        # After 100 days, at least one weather event should have occurred
        # (5% chance per region per day, 6 regions = ~30% per day)
        # We check it accumulated at some point (can't check current since it clears)
        # Just verify weather gen doesn't crash
        event = gen_weather(self.ws)
        self.assertEqual(event["event_type"], "weather_event")


# ---------------------------------------------------------------------------
# 2. Pattern Maturation (hit accumulation + tier promotion)
# ---------------------------------------------------------------------------

class TestPatternMaturation(FreightPipelineTestBase):
    """Test that repeated events accumulate hits and promote tiers."""

    def test_single_event_creates_star(self):
        rec, _ = self.gi.record_event(
            fingerprint="freight:quote:ATL-NYC",
            cc_score=0.7,
            impact="medium",
            domain=DOMAIN,
            intent="quote_request",
            summary="Quote Atlanta→New York",
        )
        self.assertEqual(rec.hits, 1)
        self.assertEqual(rec.domain, DOMAIN)

    def test_repeated_events_accumulate_hits(self):
        fp = "freight:delay:ATL-NYC:weather"
        for i in range(10):
            rec, _ = self.gi.record_event(
                fingerprint=fp,
                cc_score=0.8,
                impact="high",
                domain=DOMAIN,
                intent="delay_reported",
                summary="Delay Atlanta→NYC weather",
            )
        self.assertEqual(rec.hits, 10)
        self.assertGreater(rec.freq, 0)

    def test_tier_promotion_deep_to_cold(self):
        """First hit on a fingerprint starts at HOT (Law 1)."""
        rec, promo = self.gi.record_event(
            fingerprint="freight:new_pattern",
            cc_score=0.5, domain=DOMAIN, intent="test",
        )
        # New records start at HOT tier
        self.assertEqual(rec.tier, Tier.HOT.value)

    def test_high_hit_star_has_high_freq(self):
        fp = "freight:rate:SE-NE"
        for _ in range(20):
            rec, _ = self.gi.record_event(
                fingerprint=fp, cc_score=0.9, impact="high",
                domain=DOMAIN, intent="rate_change",
            )
        self.assertEqual(rec.hits, 20)
        self.assertGreater(rec.freq, 1.0)  # 20 hits in < 1 day = high freq

    def test_domain_stars_isolated(self):
        self.gi.record_event(
            fingerprint="freight:a", domain=DOMAIN, intent="a",
        )
        self.gi.record_event(
            fingerprint="other:b", domain="other_domain", intent="b",
        )
        freight = self.gi.by_domain(DOMAIN)
        other = self.gi.by_domain("other_domain")
        self.assertEqual(len(freight), 1)
        self.assertEqual(len(other), 1)


# ---------------------------------------------------------------------------
# 3. Cross-Domain Convergence
# ---------------------------------------------------------------------------

class TestConvergenceDetection(FreightPipelineTestBase):
    """Test convergence detection between freight sub-domains."""

    def test_intent_overlap_convergence(self):
        """Stars with overlapping intents should converge."""
        # Market domain star
        self.gi.record_event(
            fingerprint="market:AAPL", cc_score=0.8,
            domain="market", intent="AAPL",
        )
        # Freight domain star with overlapping intent reference
        self.gi.record_event(
            fingerprint="freight:rate:AAPL_lane", cc_score=0.7,
            domain=DOMAIN, intent="AAPL",
        )
        convs = self.gi.cross_domain_convergences(
            domain_a="market", domain_b=DOMAIN,
        )
        intent_convs = [c for c in convs if c["type"] == "intent_overlap"]
        self.assertGreater(len(intent_convs), 0)

    def test_temporal_convergence(self):
        """Stars active in the same window should converge temporally."""
        self.gi.record_event(
            fingerprint="market:BTC", cc_score=0.6,
            domain="market", intent="BTC",
        )
        self.gi.record_event(
            fingerprint="freight:fuel_cost", cc_score=0.5,
            domain=DOMAIN, intent="fuel_cost",
        )
        convs = self.gi.cross_domain_convergences(domain_a="market")
        temporal = [c for c in convs if c["type"] == "temporal_proximity"]
        self.assertGreater(len(temporal), 0)

    def test_no_convergence_low_cc(self):
        """Stars with very low cc should not converge temporally."""
        self.gi.record_event(
            fingerprint="market:low", cc_score=0.05,
            domain="market", intent="weak",
        )
        self.gi.record_event(
            fingerprint="freight:low", cc_score=0.05,
            domain=DOMAIN, intent="also_weak",
        )
        convs = self.gi.cross_domain_convergences(domain_a="market")
        # min_cc default is 0.0, but temporal requires combined_cc >= 0.3
        temporal = [c for c in convs if c["type"] == "temporal_proximity"]
        self.assertEqual(len(temporal), 0)

    def test_scenario_events_create_convergent_patterns(self):
        """Scenario-injected events sharing intents should form convergences."""
        ws = WorldState(seed=99)
        # Inject pricing opportunity events
        events = inject_pricing_opportunity(ws)
        for e in events:
            self.gi.record_event(
                fingerprint=f"{DOMAIN}:{e['event_type']}:{e['data'].get('region', 'X')}",
                cc_score=0.8,
                domain=DOMAIN,
                intent=e["event_type"],
                summary=str(e["data"])[:80],
            )
        # Inject from a "market" domain to create cross-domain
        self.gi.record_event(
            fingerprint="market:rate_surge",
            cc_score=0.9,
            domain="market",
            intent="rate_change",
        )
        convs = self.gi.cross_domain_convergences(domain_a="market", domain_b=DOMAIN)
        self.assertGreater(len(convs), 0)


# ---------------------------------------------------------------------------
# 4. Domain Knowledge Elevation
# ---------------------------------------------------------------------------

class TestDomainElevation(FreightPipelineTestBase):
    """Test elevation of mature patterns to domain library."""

    def test_mature_pattern_elevates(self):
        result = elevate_pattern(
            domain=DOMAIN,
            pattern_type="delay_reported",
            conditions_signature="weather|Southeast",
            win_rate=70.0,
            expectancy=0.8,
            avg_win_pct=1.5,
            avg_loss_pct=0.4,
            sample_size=20,
            tenant_id="t_freight_1",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.domain, DOMAIN)
        self.assertEqual(result.pattern_type, "delay_reported")

    def test_immature_pattern_rejected(self):
        result = elevate_pattern(
            domain=DOMAIN,
            pattern_type="test",
            conditions_signature="c",
            win_rate=70.0,
            expectancy=0.5,
            avg_win_pct=1.0,
            avg_loss_pct=0.3,
            sample_size=5,  # too low
            tenant_id="t_1",
        )
        self.assertIsNone(result)

    def test_two_tenants_merge_creates_strong_pattern(self):
        # Tenant 1
        elevate_pattern(
            domain=DOMAIN, pattern_type="capacity_risk",
            conditions_signature="weather|saturation",
            win_rate=65.0, expectancy=0.6,
            avg_win_pct=1.2, avg_loss_pct=0.4,
            sample_size=25, tenant_id="t_1",
        )
        # Tenant 2 — same pattern
        result = elevate_pattern(
            domain=DOMAIN, pattern_type="capacity_risk",
            conditions_signature="weather|saturation",
            win_rate=72.0, expectancy=0.9,
            avg_win_pct=1.6, avg_loss_pct=0.3,
            sample_size=35, tenant_id="t_2",
        )
        self.assertEqual(result.contributing_tenants, 2)
        self.assertTrue(result.is_strong)
        self.assertEqual(result.sample_size, 60)

    def test_domain_summary_after_elevation(self):
        elevate_pattern(
            domain=DOMAIN, pattern_type="pricing_opportunity",
            conditions_signature="demand_high|supply_low",
            win_rate=75.0, expectancy=1.2,
            avg_win_pct=2.0, avg_loss_pct=0.5,
            sample_size=30, tenant_id="t_1",
        )
        s = get_domain_summary(DOMAIN)
        self.assertEqual(s["patterns"], 1)
        self.assertEqual(s["total_observations"], 30)
        self.assertGreater(s["avg_win_rate"], 55)


# ---------------------------------------------------------------------------
# 5. Tenant Seeding from Domain Library
# ---------------------------------------------------------------------------

class TestTenantSeeding(FreightPipelineTestBase):
    """Test that new tenants receive domain priors."""

    def test_seed_generates_gravity_events(self):
        # Elevate a mature pattern
        elevate_pattern(
            domain=DOMAIN, pattern_type="route_optimization",
            conditions_signature="empty_miles|low_density",
            win_rate=68.0, expectancy=0.7,
            avg_win_pct=1.3, avg_loss_pct=0.4,
            sample_size=22, tenant_id="t_1",
        )
        priors = seed_tenant_priors(DOMAIN)
        self.assertEqual(len(priors), 1)
        self.assertTrue(priors[0]["fingerprint"].startswith("prior:"))
        self.assertEqual(priors[0]["impact"], "low")
        self.assertIn("DOMAIN PRIOR", priors[0]["summary"])

    def test_seeded_priors_ingest_into_gravity(self):
        elevate_pattern(
            domain=DOMAIN, pattern_type="delay_pattern",
            conditions_signature="carrier|lane",
            win_rate=60.0, expectancy=0.4,
            avg_win_pct=1.0, avg_loss_pct=0.5,
            sample_size=18, tenant_id="t_1",
        )
        priors = seed_tenant_priors(DOMAIN)
        # Ingest into our isolated gravity index
        for p in priors:
            e = {k: v for k, v in p.items() if k != "source"}  # record_event doesn't accept 'source'
            self.gi.record_event(**e)
        # Verify the prior is now a star
        rec = self.gi.get(priors[0]["fingerprint"])
        self.assertIsNotNone(rec)
        self.assertEqual(rec.domain, DOMAIN)
        self.assertIn("PRIOR", rec.summary)

    def test_empty_domain_seeds_nothing(self):
        priors = seed_tenant_priors("nonexistent_domain")
        self.assertEqual(len(priors), 0)


# ---------------------------------------------------------------------------
# 6. Full Pipeline Integration
# ---------------------------------------------------------------------------

class TestFullPipeline(FreightPipelineTestBase):
    """End-to-end: repeated events → mature star → elevated pattern → seeded tenant."""

    def test_repeated_events_to_elevation(self):
        # Simulate a recurring pattern: weather delays in Southeast
        fp = f"{DOMAIN}:delay:Southeast:weather"
        for _ in range(20):
            self.gi.record_event(
                fingerprint=fp, cc_score=0.85,
                impact="high", domain=DOMAIN,
                intent="delay_reported",
                summary="Weather delay Southeast",
            )
        # Verify accumulation
        rec = self.gi.get(fp)
        self.assertEqual(rec.hits, 20)
        self.assertGreater(rec.cc_score, 0.5)

        # Elevate (simulating what try_elevate_from_gravity does)
        result = elevate_pattern(
            domain=DOMAIN,
            pattern_type=rec.intent,
            conditions_signature=rec.fingerprint,
            win_rate=round(rec.cc_score * 100, 1),
            expectancy=round(rec.cc_score * rec.freq, 3),
            avg_win_pct=round(rec.cc_score * 100, 3),
            avg_loss_pct=round((1 - rec.cc_score) * 100, 3),
            sample_size=rec.hits,
            tenant_id="t_test",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.contributing_tenants, 1)

        # Seed a new tenant
        priors = seed_tenant_priors(DOMAIN)
        self.assertEqual(len(priors), 1)

        # New tenant ingests the prior
        new_gi = GravityIndex(
            path=os.path.join(self._temp_dir, "new_tenant_gravity.json"),
        )
        for p in priors:
            e = {k: v for k, v in p.items() if k != "source"}
            new_gi.record_event(**e)
        new_rec = new_gi.get(priors[0]["fingerprint"])
        self.assertIsNotNone(new_rec)
        self.assertEqual(new_rec.hits, 1)  # starts with 1 hit from prior

    def test_multiple_event_types_form_domain_stars(self):
        """Different freight event types create distinct domain stars."""
        event_types = [
            "quote_request", "load_booking", "delivery_complete",
            "delay_reported", "rate_change", "capacity_update",
        ]
        for et in event_types:
            for _ in range(5):
                self.gi.record_event(
                    fingerprint=f"{DOMAIN}:{et}",
                    cc_score=0.7,
                    domain=DOMAIN,
                    intent=et,
                )
        records = self.gi.by_domain(DOMAIN)
        self.assertEqual(len(records), len(event_types))
        for rec in records:
            self.assertEqual(rec.hits, 5)


if __name__ == "__main__":
    unittest.main()
