"""
Clase G — unit tests for static scanner, runtime detector, and strategy.

Self-contained (unittest stdlib). No network, no subprocess, no real repo
writes outside tmp dirs.

Structure:
  TestStaticScanner      — AST-based offender detection
  TestRuntimeDetector    — hash-based repetition detection
  TestStrategyG          — strategy.precondition + proposal rendering
  TestRealRepoIsClean    — integration: scanner finds 0 offenders in live repo
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Ensure project root on path
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.recovery.detectors import hardcoded_handler_runtime as rt
from core.recovery.events import HealthState
from core.recovery.static import hardcoded_handler_scan as scan
from core.recovery.strategies import classG_hardcoded_handler as cg


# ---------------------------------------------------------------------------
# Static scanner
# ---------------------------------------------------------------------------
class TestStaticScanner(unittest.TestCase):
    """AST-based detection of hardcoded identity handlers."""

    def _write(self, tmpdir: str, relpath: str, content: str) -> str:
        full = os.path.join(tmpdir, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return full

    def test_get_product_identity_call_outside_layer_is_detected(self):
        src = (
            "from vectrax.core_identity import get_product_identity\n"
            "def handle_text(text):\n"
            "    return get_product_identity('es')\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            fp = self._write(tmp, "vectrax/bad_gateway.py", src)
            offs = scan.scan_file(fp)
            self.assertEqual(len(offs), 1)
            self.assertEqual(offs[0].rule, "GET_PRODUCT_IDENTITY_OUTSIDE_LAYER")
            self.assertIn("identity_handler", offs[0].suggestion)

    def test_get_product_identity_via_module_attribute(self):
        src = (
            "import vectrax.core_identity as ci\n"
            "def _fast(content):\n"
            "    return ci.get_product_identity('es')\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            fp = self._write(tmp, "foo/bar.py", src)
            offs = scan.scan_file(fp)
            self.assertEqual(len(offs), 1)

    def test_hardcoded_blurb_in_response_function_detected(self):
        blurb = (
            "Vectrax es tu memoria inteligente. Recuerda todo lo que le dices, "
            "aprende cómo piensas, te ayuda a decidir mejor con el tiempo."
        )
        src = (
            "def _fast(text):\n"
            f"    return {blurb!r}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            fp = self._write(tmp, "vectrax/bad_fast.py", src)
            offs = scan.scan_file(fp)
            self.assertEqual(len(offs), 1)
            self.assertEqual(offs[0].rule, "HARDCODED_BLURB_RETURN")

    def test_short_literal_not_flagged(self):
        src = (
            "def handle_text(text):\n"
            "    return 'OK'\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            fp = self._write(tmp, "a/b.py", src)
            self.assertEqual(scan.scan_file(fp), [])

    def test_long_literal_without_signature_not_flagged(self):
        long_text = "A" * 200
        src = (
            "def handle_text(text):\n"
            f"    return {long_text!r}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            fp = self._write(tmp, "a/b.py", src)
            self.assertEqual(scan.scan_file(fp), [])

    def test_signature_literal_in_non_response_function_not_flagged(self):
        """If no function context suggests user-facing response, skip."""
        blurb = "Vectrax es tu memoria inteligente. " * 5
        src = (
            "def build_system_prompt():\n"
            f"    return {blurb!r}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            fp = self._write(tmp, "a/b.py", src)
            self.assertEqual(scan.scan_file(fp), [])

    def test_whitelisted_file_never_offends(self):
        """core_identity.py is allowed to contain the canonical blurb."""
        blurb = "Vectrax es tu memoria inteligente. " * 5
        src = (
            "def _fast(text):\n"
            f"    return {blurb!r}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            fp = self._write(tmp, "vectrax/core_identity.py", src)
            self.assertEqual(scan.scan_file(fp), [])

    def test_concatenated_literals_are_detected(self):
        """String concat via BinOp Add should be reconstructed."""
        src = (
            "def _fast(text):\n"
            "    return 'Vectrax es tu memoria inteligente. ' + "
            "'Recuerda todo lo que le dices, aprende como piensas y te ayuda a decidir mejor.'\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            fp = self._write(tmp, "vectrax/bad_fast.py", src)
            offs = scan.scan_file(fp)
            self.assertEqual(len(offs), 1)
            self.assertEqual(offs[0].rule, "HARDCODED_BLURB_RETURN")

    def test_scan_paths_counts_files_and_skips_dirs(self):
        src_clean = "def f(x):\n    return 'ok'\n"
        src_bad = (
            "def handle_text(text):\n"
            "    return 'Vectrax es tu memoria inteligente. " + "A" * 100 + "'\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "mod_a.py", src_clean)
            self._write(tmp, "sub/mod_b.py", src_bad)
            self._write(tmp, "tests/skipme.py", src_bad)  # tests/ dir → skipped
            report = scan.scan_paths([tmp])
            self.assertGreaterEqual(report.files_scanned, 2)
            self.assertEqual(len(report.offenders), 1)
            self.assertTrue(report.offenders[0].filepath.endswith("sub/mod_b.py"))

    def test_scan_ignores_bak_files(self):
        src = (
            "def handle_text(text):\n"
            "    return 'Vectrax es tu memoria inteligente. " + "A" * 100 + "'\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "mod.bak.py", src)
            report = scan.scan_paths([tmp])
            self.assertEqual(len(report.offenders), 0)


# ---------------------------------------------------------------------------
# Runtime detector
# ---------------------------------------------------------------------------
class TestRuntimeDetector(unittest.TestCase):
    """Hash-based detection of repeated user-facing responses."""

    def setUp(self):
        rt.reset_state_for_tests()
        # Keep defaults small for test speed
        self._original = (rt.DUPLICATE_THRESHOLD, rt.WINDOW_S, rt.UNIQUE_USERS_THRESHOLD)
        rt.DUPLICATE_THRESHOLD = 3
        rt.UNIQUE_USERS_THRESHOLD = 2
        rt.WINDOW_S = 3600

    def tearDown(self):
        rt.DUPLICATE_THRESHOLD, rt.WINDOW_S, rt.UNIQUE_USERS_THRESHOLD = self._original
        rt.reset_state_for_tests()

    def test_no_registrations_probe_ok(self):
        ev = rt.probe()
        self.assertEqual(ev.state, HealthState.OK)
        self.assertEqual(ev.evidence["tracked_hashes_total"], 0)

    def test_single_registration_ok(self):
        rt.register_response("user1", "A" * 100, lang="es")
        self.assertEqual(rt.probe().state, HealthState.OK)

    def test_short_response_not_tracked(self):
        rt.register_response("user1", "short", lang="es")
        rt.register_response("user2", "short", lang="es")
        rt.register_response("user3", "short", lang="es")
        self.assertEqual(rt.probe().state, HealthState.OK)
        self.assertEqual(rt.probe().evidence["tracked_hashes_total"], 0)

    def test_same_user_repeated_not_broken(self):
        """Single user repeating the same long response doesn't escalate."""
        blob = "Vectrax blurb — " + "A" * 100
        for _ in range(10):
            rt.register_response("user1", blob, lang="es")
        # unique_users=1 < threshold=2 → OK, even though count high
        self.assertEqual(rt.probe().state, HealthState.OK)

    def test_multiple_users_same_response_broken(self):
        blob = "Vectrax blurb — " + "B" * 100
        rt.register_response("user1", blob, lang="es")
        rt.register_response("user2", blob, lang="es")
        rt.register_response("user3", blob, lang="en")
        ev = rt.probe()
        self.assertEqual(ev.state, HealthState.BROKEN)
        self.assertGreaterEqual(ev.evidence["top_count"], 3)
        self.assertGreaterEqual(ev.evidence["top_unique_users"], 2)
        self.assertIn("top_hash", ev.evidence)
        self.assertIn("top_text_prefix", ev.evidence)
        self.assertIn("es", ev.evidence["top_languages"])
        self.assertIn("en", ev.evidence["top_languages"])

    def test_different_responses_not_broken(self):
        for i in range(10):
            rt.register_response(f"user{i}", f"Unique response #{i} " + "C" * 80, lang="es")
        self.assertEqual(rt.probe().state, HealthState.OK)

    def test_window_expiration(self):
        """Old occurrences outside the window drop out on probe."""
        blob = "Vectrax blurb — " + "D" * 100
        # Shrink window to 1 second for this test
        rt.WINDOW_S = 1
        rt.register_response("user1", blob, lang="es")
        rt.register_response("user2", blob, lang="es")
        rt.register_response("user3", blob, lang="es")
        self.assertEqual(rt.probe().state, HealthState.BROKEN)
        time.sleep(1.1)
        self.assertEqual(rt.probe().state, HealthState.OK)

    def test_register_response_never_raises(self):
        """Defensive: bad input should be swallowed, not propagate."""
        rt.register_response(None, None, lang=None)  # type: ignore[arg-type]
        rt.register_response("", "", "")
        rt.register_response("u", 12345, "es")  # type: ignore[arg-type]
        # No exception → pass
        self.assertTrue(True)


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
class TestStrategyG(unittest.TestCase):
    def test_build_returns_strategy_with_expected_fields(self):
        strat = cg.build()
        self.assertEqual(strat.id, cg.STRATEGY_ID)
        self.assertEqual(strat.trigger_events, ["hardcoded_handler_runtime"])
        self.assertEqual(len(strat.action_plan), 1)
        self.assertEqual(strat.rollback_plan, [])
        self.assertEqual(strat.max_attempts_per_window, 1)
        self.assertFalse(strat.require_human_approval)

    def test_precondition_accepts_valid_evidence(self):
        ev = {"top_hash": "abc123def456", "top_count": 7}
        self.assertTrue(cg._precondition(ev))

    def test_precondition_rejects_missing_hash(self):
        self.assertFalse(cg._precondition({"top_count": 7}))

    def test_precondition_rejects_empty_hash(self):
        self.assertFalse(cg._precondition({"top_hash": "", "top_count": 7}))

    def test_precondition_rejects_zero_count(self):
        self.assertFalse(cg._precondition({"top_hash": "abc", "top_count": 0}))

    def test_precondition_rejects_non_int_count(self):
        self.assertFalse(cg._precondition({"top_hash": "abc", "top_count": "7"}))

    def test_matches_routes_via_trigger_event_id(self):
        strat = cg.build()
        ev = {"top_hash": "abc123", "top_count": 7}
        self.assertTrue(strat.matches("hardcoded_handler_runtime", ev))
        self.assertFalse(strat.matches("gateway_silent", ev))

    def test_render_proposal_includes_all_diagnostic_fields(self):
        ev = {
            "top_hash": "deadbeef",
            "top_count": 9,
            "top_unique_users": 4,
            "top_languages": ["es", "en"],
            "top_text_prefix": "Vectrax es tu memoria inteligente...",
            "top_text_length": 253,
            "reason": "hash repeated across users",
        }
        msg = cg.render_proposal_for_evidence(ev)
        self.assertIn("deadbeef", msg)
        self.assertIn("9", msg)
        self.assertIn("4", msg)
        self.assertIn("es, en", msg)
        self.assertIn("253", msg)
        self.assertIn("respond_if_identity", msg)
        self.assertIn("hardcoded_handler_scan", msg)


# ---------------------------------------------------------------------------
# Integration: the live repo must be clean
# ---------------------------------------------------------------------------
class TestRealRepoIsClean(unittest.TestCase):
    """The canonical state of this repo must have ZERO offenders.

    This is the live regression gate: if anyone (re)introduces a hardcoded
    identity handler, this test fails AND the static scanner CLI fails,
    blocking the commit / merge.
    """

    def test_repo_scan_clean(self):
        repo_root = str(_ROOT)
        # Scan the same default paths the CLI uses
        paths = scan._default_paths(repo_root)
        self.assertTrue(paths, "expected at least one scan target dir")
        report = scan.scan_paths(paths)
        if not report.is_clean():
            self.fail("Class G offenders in live repo:\n" + report.format(repo_root))


if __name__ == "__main__":
    unittest.main()
