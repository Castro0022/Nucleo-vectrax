"""
Tests for UPL-Observer integration.

Verifies:
  - Observer behaves identically with UPL_OBSERVER_INTEGRATION=false
  - Observer continues normally if UPL throws an exception
  - UPL does NOT generate proposals
  - UPL does NOT modify gravity_score, confidence, or expectancy
  - Audit logging records hypothesis boosts with required fields
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch, MagicMock, call

from core.universal_pattern_library import MetaPattern


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hypothesis(domains=None):
    return MetaPattern(
        meta_id="MP-test_hyp",
        structure="test structural pattern",
        source_domains=domains or ["freight_logistics", "market"],
        contributing_domain_count=len(domains or ["freight_logistics", "market"]),
        total_observations=50,
        confidence="HYPOTHESIS",
    )


def _make_fake_snapshot():
    """Minimal snapshot dict for observer comparison."""
    return {
        "worker_alive": True,
        "queue_pending": 0,
        "recent_error_count_24h": 0,
        "signals": [],
        "star_count": 5,
        "gravity_total": 10,
        "gravity_convergences": [],
    }


def _make_fake_gravity():
    return {"total": 10, "stars": {}, "domains": {}}


# ---------------------------------------------------------------------------
# 1. Env toggle: UPL_OBSERVER_INTEGRATION=false
# ---------------------------------------------------------------------------

class TestEnvToggle(unittest.TestCase):

    @patch.dict(os.environ, {"UPL_OBSERVER_INTEGRATION": "false"})
    @patch("core.self_observation.autonomous_observer._collect_gravity_detail")
    @patch("core.self_observation.universe_observer.observe_universe")
    def test_observer_skips_upl_when_disabled(self, mock_snap, mock_grav):
        """With UPL disabled, get_usable_hypotheses should never be called."""
        import core.self_observation.autonomous_observer as obs
        obs._prev_snapshot = _make_fake_snapshot()
        obs._prev_gravity = _make_fake_gravity()

        mock_universe = MagicMock()
        mock_universe.to_dict.return_value = _make_fake_snapshot()
        mock_snap.return_value = mock_universe
        mock_grav.return_value = _make_fake_gravity()

        with patch("core.self_observation.observation_ledger.init_ledger"), \
             patch("core.self_observation.observation_ledger.record") as mock_record:
            obs.observe_and_record()

        # Verify no UPL-related audit log
        for c in mock_record.call_args_list:
            self.assertNotEqual(c[0][0], "upl",
                "UPL audit log found when integration is disabled")


# ---------------------------------------------------------------------------
# 2. Exception safety
# ---------------------------------------------------------------------------

class TestExceptionSafety(unittest.TestCase):

    @patch.dict(os.environ, {"UPL_OBSERVER_INTEGRATION": "true"})
    @patch("core.universal_pattern_library.get_usable_hypotheses",
           side_effect=RuntimeError("UPL crashed"))
    @patch("core.self_observation.autonomous_observer._collect_gravity_detail")
    @patch("core.self_observation.universe_observer.observe_universe")
    def test_observer_continues_if_upl_crashes(self, mock_snap, mock_grav, mock_upl):
        """If UPL throws, observer should continue all detectors normally."""
        import core.self_observation.autonomous_observer as obs
        obs._prev_snapshot = _make_fake_snapshot()
        obs._prev_gravity = _make_fake_gravity()

        mock_universe = MagicMock()
        mock_universe.to_dict.return_value = _make_fake_snapshot()
        mock_snap.return_value = mock_universe
        mock_grav.return_value = _make_fake_gravity()

        with patch("core.self_observation.observation_ledger.init_ledger"), \
             patch("core.self_observation.observation_ledger.record"):
            # Should NOT raise
            result = obs.observe_and_record()

        self.assertIsInstance(result, int)


# ---------------------------------------------------------------------------
# 3. No proposals from UPL
# ---------------------------------------------------------------------------

class TestNoProposals(unittest.TestCase):

    def test_upl_hypotheses_have_no_proposal_field(self):
        h = _make_hypothesis()
        d = h.to_dict()
        self.assertNotIn("proposal", d)
        self.assertNotIn("action", d)
        self.assertNotIn("execute", d)
        self.assertEqual(h.confidence, "HYPOTHESIS")

    def test_observer_code_does_not_call_proposal_engine(self):
        """The UPL integration block should never call any proposal function."""
        import inspect
        import core.self_observation.autonomous_observer as obs
        source = inspect.getsource(obs)
        # Find the UPL block specifically
        upl_start = source.find("UPL: observation priority hints")
        upl_end = source.find("Compare and detect changes", upl_start)
        upl_block = source[upl_start:upl_end]
        self.assertNotIn("generate_proposal", upl_block)
        self.assertNotIn("auto_execute", upl_block)
        self.assertNotIn("execute_proposal", upl_block)
        self.assertNotIn("_tg_notify", upl_block)


# ---------------------------------------------------------------------------
# 4. No mutation of gravity_score/confidence/expectancy
# ---------------------------------------------------------------------------

class TestNoMutation(unittest.TestCase):

    def test_upl_block_does_not_modify_gravity(self):
        """The UPL code statements (not comments) must not touch gravity internals."""
        import core.self_observation.autonomous_observer as obs
        source = open(obs.__file__).read()
        upl_start = source.find("_upl_boosted_domains")
        upl_end = source.find("# Compare and detect changes", upl_start)
        upl_code = source[upl_start:upl_end]
        # Strip comments to check only executable code
        code_lines = [l for l in upl_code.split("\n") if l.strip() and not l.strip().startswith("#")]
        code_only = "\n".join(code_lines)

        # Must NOT modify gravity scores in executable code
        self.assertNotIn(".gravity_score", code_only)
        self.assertNotIn(".cc_score", code_only)
        self.assertNotIn(".expectancy", code_only)
        self.assertNotIn("record_event(", code_only)  # no gravity recording
        self.assertNotIn("_save(", code_only)  # no persistence writes

    def test_bias_weights_only_additive(self):
        """UPL boost should add 0.5, never replace existing weight."""
        import core.self_observation.autonomous_observer as obs
        source = open(obs.__file__).read()
        # Find the bias weight modification line
        self.assertIn("_bias_weights.get(domain, 1.0) + 0.5", source)


# ---------------------------------------------------------------------------
# 5. Audit logging
# ---------------------------------------------------------------------------

class TestAuditLogging(unittest.TestCase):

    @patch.dict(os.environ, {"UPL_OBSERVER_INTEGRATION": "true"})
    @patch("core.universal_pattern_library.get_usable_hypotheses")
    @patch("core.self_observation.autonomous_observer._collect_gravity_detail")
    @patch("core.self_observation.universe_observer.observe_universe")
    def test_audit_log_includes_required_fields(self, mock_snap, mock_grav, mock_upl):
        """When UPL boosts a domain, audit log must include all required fields."""
        mock_upl.return_value = [_make_hypothesis(["freight_logistics", "market"])]

        import core.self_observation.autonomous_observer as obs
        obs._prev_snapshot = _make_fake_snapshot()
        obs._prev_gravity = _make_fake_gravity()

        mock_universe = MagicMock()
        mock_universe.to_dict.return_value = _make_fake_snapshot()
        mock_snap.return_value = mock_universe
        # Gravity snapshot WITH a star in a boosted domain so affected_star_ids
        # can be verified as populated.
        mock_grav.return_value = {
            "total": 1,
            "stars": {
                "freight_logistics:abc": {
                    "domain": "freight_logistics", "hits": 3,
                    "tier": "mid", "intent": "quote", "summary": "x",
                },
            },
            "domains": {},
        }

        with patch("core.self_observation.observation_ledger.init_ledger"), \
             patch("core.self_observation.observation_ledger.record") as mock_record:
            obs.observe_and_record()

        # Find the UPL audit log calls
        upl_calls = [c for c in mock_record.call_args_list
                     if len(c[0]) >= 2 and c[0][0] == "upl"]
        self.assertGreater(len(upl_calls), 0, "No UPL audit log found")

        # Every UPL audit record must carry ALL required fields
        required = {"domain", "hypothesis_id", "matched_structure",
                    "affected_star_ids", "priority_reason"}
        for c in upl_calls:
            evidence = c[1].get("evidence", {}) if c[1] else {}
            missing = required - set(evidence.keys())
            self.assertEqual(missing, set(), f"audit log missing fields: {missing}")

        # Verify the freight_logistics record content
        freight = [c for c in upl_calls
                   if c[1].get("evidence", {}).get("domain") == "freight_logistics"]
        self.assertEqual(len(freight), 1, "expected one record for freight_logistics")
        ev = freight[0][1]["evidence"]
        self.assertEqual(ev["hypothesis_id"], "MP-test_hyp")
        self.assertEqual(ev["matched_structure"], "test structural pattern")
        self.assertIn("freight_logistics:abc", ev["affected_star_ids"])
        self.assertEqual(ev["priority_reason"], "cross-domain structural similarity")


if __name__ == "__main__":
    unittest.main()
