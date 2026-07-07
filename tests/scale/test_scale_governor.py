"""
tests/scale/test_scale_governor.py — Scale Governor Engine tests.

Cubre los criterios de éxito del spec:
  - "Hola" usa MINIMAL.
  - "Analiza este contrato" usa MISSION_CRITICAL.
  - FREE nunca activa más de 5 motores.
  - Fallo de un motor produce fallback inmediato.
  - Respuestas idénticas pueden salir de cache.
  - Bajo carga alta, el sistema degrada antes de caerse.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.scale import (
    PipelineProfile,
    Tier,
    PolicyConfig,
    ScaleGovernor,
    RateLimiter,
    LoadShedder,
    LoadLevel,
    CostTracker,
    track_engine_cost,
    get_global_tracker,
    get_policy_for,
    MAX_ENGINES_BY_TIER,
)


# ---------------------------------------------------------------------------
# Required cases from spec
# ---------------------------------------------------------------------------

class TestRequiredCases(unittest.TestCase):

    def setUp(self):
        # Fresh governor per test (avoids cache/rate bleed)
        self.gov = ScaleGovernor()
        self.gov.cache_clear()

    def test_hola_uses_minimal_pipeline(self):
        d = self.gov.select_minimal_pipeline(
            user_id="u1", tier=Tier.PRO,
            intent="greeting", message="Hola",
        )
        self.assertEqual(d.profile, PipelineProfile.MINIMAL)
        self.assertEqual(d.policy.allow_reasoning, False)
        self.assertEqual(d.policy.allow_multimodal, False)
        self.assertLessEqual(d.policy.max_engines, 5)

    def test_contract_uses_mission_critical(self):
        d = self.gov.select_minimal_pipeline(
            user_id="u2", tier=Tier.PRO,
            intent="legal", message="Analiza este contrato por favor",
        )
        self.assertEqual(d.profile, PipelineProfile.MISSION_CRITICAL)
        self.assertTrue(d.policy.allow_reasoning)
        self.assertTrue(d.policy.allow_deep_memory)

    def test_contract_via_message_hint(self):
        # Even if intent is generic, message content elevates it.
        d = self.gov.select_minimal_pipeline(
            user_id="u3", tier=Tier.BUSINESS,
            intent="query", message="Necesito que revises este contrato",
        )
        self.assertEqual(d.profile, PipelineProfile.MISSION_CRITICAL)

    def test_free_tier_max_5_engines(self):
        # FREE in any profile must never exceed 5 engines.
        for intent in ("greeting", "query", "image", "legal", "deploy"):
            for msg in ("hola", "contrato urgente", "analiza esto",
                        "explica step by step esta arquitectura"):
                d = self.gov.select_minimal_pipeline(
                    user_id=f"free_{intent}", tier=Tier.FREE,
                    intent=intent, message=msg,
                )
                self.assertLessEqual(
                    d.policy.max_engines, 5,
                    msg=f"FREE/{intent}: got {d.policy.max_engines} > 5 (msg={msg!r})",
                )

    def test_free_mission_critical_gets_upgrade_hint(self):
        d = self.gov.select_minimal_pipeline(
            user_id="free_legal", tier=Tier.FREE,
            intent="legal", message="Mi contrato dice que...",
        )
        # Aún cae en MISSION_CRITICAL como profile (clasificación correcta)
        self.assertEqual(d.profile, PipelineProfile.MISSION_CRITICAL)
        # Pero la policy queda capada y hay upgrade hint
        self.assertLessEqual(d.policy.max_engines, 5)
        self.assertIsNotNone(d.upgrade_hint)
        self.assertIn("PRO", d.upgrade_hint or "")

    def test_prevent_engine_cascade_returns_fallback(self):
        plan = self.gov.prevent_engine_cascade(
            failed_engine="memory_engine",
            pipeline_state={"completed_engines": ["intent_classifier"]},
        )
        self.assertTrue(plan["use_fallback"])
        self.assertEqual(plan["partial_response"], None)
        self.assertEqual(plan["remaining_engines"], [])

    def test_prevent_engine_cascade_uses_partial_when_available(self):
        plan = self.gov.prevent_engine_cascade(
            failed_engine="audit",
            pipeline_state={
                "completed_engines": ["intent_classifier", "memory_engine"],
                "partial_response": "Tengo lo que necesitas, Mario.",
            },
        )
        self.assertFalse(plan["use_fallback"])
        self.assertIn("Mario", plan["partial_response"])

    def test_semantic_cache_returns_identical_response(self):
        # First call: no cache, should compute fresh
        d1 = self.gov.select_minimal_pipeline(
            user_id="u_cache", tier=Tier.PRO,
            intent="query", message="¿Cuál es la capital de Francia?",
        )
        self.assertFalse(d1.cache_hit)

        # Set cache
        self.gov.semantic_cache_set(d1.cache_key, "París", ttl=60)

        # Second call: cache hit
        d2 = self.gov.select_minimal_pipeline(
            user_id="u_cache", tier=Tier.PRO,
            intent="query", message="¿Cuál es la capital de Francia?",
        )
        self.assertTrue(d2.cache_hit)
        self.assertEqual(d2.cached_response, "París")

    def test_rate_limiter_blocks_after_limit(self):
        rl = RateLimiter()
        # FREE: burst=5
        for _ in range(5):
            self.assertTrue(rl.consume("user_x", Tier.FREE))
        # 6th call should fail (refill rate is daily/86400, ~negligible
        # within the test's microseconds)
        self.assertFalse(rl.consume("user_x", Tier.FREE))

    def test_load_shedder_degrades_under_high_load(self):
        shed = LoadShedder()
        base_policy = get_policy_for(Tier.PRO, PipelineProfile.ADVANCED)
        # NORMAL → no degrade
        np_pol = shed.degrade_pipeline(base_policy, LoadLevel.NORMAL)
        self.assertEqual(np_pol, base_policy)
        # HIGH → fewer engines, no learning, no scenarios
        hi_pol = shed.degrade_pipeline(base_policy, LoadLevel.HIGH)
        self.assertLess(hi_pol.max_engines, base_policy.max_engines)
        self.assertFalse(hi_pol.allow_learning)
        self.assertFalse(hi_pol.allow_scenarios)
        # CRITICAL → minimal everything
        cr_pol = shed.degrade_pipeline(base_policy, LoadLevel.CRITICAL)
        self.assertLessEqual(cr_pol.max_engines, 2)
        self.assertEqual(cr_pol.llm_model, "cheap")
        self.assertFalse(cr_pol.allow_reasoning)
        self.assertFalse(cr_pol.allow_multimodal)


# ---------------------------------------------------------------------------
# Rate-limit message (incluye enlace de upgrade)
# ---------------------------------------------------------------------------

class TestRateLimitMessage(unittest.TestCase):
    """El mensaje de rate-limit debe ofrecer una vía de upgrade."""

    def setUp(self):
        # RateLimiter propio (no el singleton) para aislar el burst por test.
        self.gov = ScaleGovernor(rate_limiter=RateLimiter())
        self.gov.cache_clear()

    def _force_rate_limited(self, uid):
        # FREE burst=5 → el 6º mensaje seguido cae en rate_limited.
        d = None
        for _ in range(6):
            d = self.gov.select_minimal_pipeline(
                user_id=uid, tier=Tier.FREE, intent="query", message="hola?",
            )
            if d.rate_limited:
                break
        return d

    def test_rate_limit_message_includes_stripe_link(self):
        with patch(
            "services.billing.stripe_billing.create_checkout_session",
            return_value="https://checkout.stripe.com/c/pay/cs_test_123",
        ):
            d = self._force_rate_limited("rl_link_user")
        self.assertTrue(d.rate_limited)
        self.assertIn("https://checkout.stripe.com", d.rate_limit_message)

    def test_rate_limit_message_falls_back_to_upgrade(self):
        # Si Stripe no da URL, el mensaje aún debe ofrecer /upgrade.
        with patch(
            "services.billing.stripe_billing.create_checkout_session",
            return_value=None,
        ):
            d = self._force_rate_limited("rl_fallback_user")
        self.assertTrue(d.rate_limited)
        self.assertIn("/upgrade", d.rate_limit_message)


# ---------------------------------------------------------------------------
# Pipeline policy tests
# ---------------------------------------------------------------------------

class TestPipelinePolicy(unittest.TestCase):

    def test_max_engines_by_tier_caps(self):
        # FREE / PRO / BUSINESS / INTERNAL caps respected.
        free = get_policy_for(Tier.FREE, PipelineProfile.ADVANCED)
        self.assertLessEqual(free.max_engines, 5)
        pro = get_policy_for(Tier.PRO, PipelineProfile.ADVANCED)
        self.assertLessEqual(pro.max_engines, 8)
        biz = get_policy_for(Tier.BUSINESS, PipelineProfile.ADVANCED)
        self.assertLessEqual(biz.max_engines, 12)
        # INTERNAL gets profile's natural ceiling
        intl = get_policy_for(Tier.INTERNAL, PipelineProfile.ADVANCED)
        self.assertEqual(intl.max_engines, 10)

    def test_minimal_disallows_heavy_capabilities(self):
        m = get_policy_for(Tier.PRO, PipelineProfile.MINIMAL)
        self.assertFalse(m.allow_reasoning)
        self.assertFalse(m.allow_multimodal)
        self.assertFalse(m.allow_scenarios)


# ---------------------------------------------------------------------------
# Cost tracker tests
# ---------------------------------------------------------------------------

class TestCostTracker(unittest.TestCase):

    def setUp(self):
        get_global_tracker().reset()

    def test_decorator_tracks_success(self):
        @track_engine_cost("test_engine_ok")
        def f(x):
            return x * 2

        result = f(5)
        self.assertEqual(result, 10)
        stats = get_global_tracker().get_engine_stats("test_engine_ok")
        self.assertEqual(stats["calls"], 1)
        self.assertEqual(stats["successes"], 1)
        self.assertEqual(stats["failures"], 0)

    def test_decorator_tracks_failure(self):
        @track_engine_cost("test_engine_fail")
        def f():
            raise ValueError("boom")

        with self.assertRaises(ValueError):
            f()
        stats = get_global_tracker().get_engine_stats("test_engine_fail")
        self.assertEqual(stats["calls"], 1)
        self.assertEqual(stats["failures"], 1)
        self.assertEqual(stats["last_error"], "ValueError")

    def test_decorator_does_not_break_when_tracker_fails(self):
        @track_engine_cost("safe_engine")
        def f(x):
            return x + 1

        # Even if the tracker had a bug, the function should still work
        with patch("core.scale.cost_tracker.get_global_tracker",
                   side_effect=Exception("tracker dead")):
            self.assertEqual(f(10), 11)

    def test_global_stats_aggregates(self):
        @track_engine_cost("e1")
        def f1():
            return "ok"

        @track_engine_cost("e2")
        def f2():
            return "ok"

        for _ in range(3):
            f1()
            f2()

        gs = get_global_tracker().get_global_stats()
        self.assertEqual(gs["total_engines"], 2)
        self.assertEqual(gs["total_calls"], 6)


if __name__ == "__main__":
    unittest.main()
