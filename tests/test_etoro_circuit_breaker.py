"""
Integration tests for eToro circuit breaker behavior.

Verifies:
  - Circuit opens after per-provider threshold (3 for eToro, 5 for others)
  - Open circuit blocks calls instantly (fail-fast)
  - Half-open allows test calls after recovery timeout
  - Recovery closes circuit after 2 consecutive successes
  - Learning cycle skips entirely when circuit is open
  - Timeout failures are recorded correctly
  - guarded_call returns fallback on circuit open
  - Per-provider recovery timeout is used (45s for eToro, 60s for others)

All external calls are mocked — no real API traffic.
"""
from __future__ import annotations

import time
import unittest
from unittest.mock import patch, MagicMock

from core.external_call_guard import (
    guarded_call,
    get_status,
    reset,
    reset_all,
    _check_circuit,
    _record_failure,
    _record_success,
    CircuitState,
    PROVIDER_OVERRIDES,
    FAILURE_THRESHOLD,
    RECOVERY_TIMEOUT,
)


class TestEtoroCircuitThreshold(unittest.TestCase):
    """eToro circuit opens after 3 failures (not global 5)."""

    def setUp(self):
        reset_all()

    def tearDown(self):
        reset_all()

    def test_etoro_threshold_is_3(self):
        self.assertEqual(PROVIDER_OVERRIDES["etoro"]["failure_threshold"], 3)

    def test_global_threshold_is_5(self):
        self.assertEqual(FAILURE_THRESHOLD, 5)

    def test_circuit_opens_at_3_failures_for_etoro(self):
        """eToro circuit should open after exactly 3 consecutive failures."""
        self.assertTrue(_check_circuit("etoro"))  # closed initially

        _record_failure("etoro", "timeout1", is_timeout=True)
        self.assertTrue(_check_circuit("etoro"))  # 1 fail — still closed

        _record_failure("etoro", "timeout2", is_timeout=True)
        self.assertTrue(_check_circuit("etoro"))  # 2 fails — still closed

        _record_failure("etoro", "timeout3", is_timeout=True)
        self.assertFalse(_check_circuit("etoro"))  # 3 fails — OPEN

    def test_circuit_stays_closed_at_2_failures_for_etoro(self):
        _record_failure("etoro", "err1")
        _record_failure("etoro", "err2")
        self.assertTrue(_check_circuit("etoro"))  # 2 < 3

    def test_other_provider_uses_global_threshold(self):
        """Non-eToro providers use the global threshold of 5."""
        for i in range(4):
            _record_failure("openai", f"err{i}")
        self.assertTrue(_check_circuit("openai"))  # 4 < 5

        _record_failure("openai", "err5")
        self.assertFalse(_check_circuit("openai"))  # 5 = 5 → OPEN


class TestCircuitFailFast(unittest.TestCase):
    """Open circuit blocks calls instantly without executing."""

    def setUp(self):
        reset_all()

    def tearDown(self):
        reset_all()

    def test_guarded_call_returns_fallback_when_open(self):
        """guarded_call should return fallback without calling fn."""
        # Open the circuit
        for _ in range(3):
            _record_failure("etoro", "test")

        fn = MagicMock(return_value={"data": "real"})
        result = guarded_call("etoro", fn, fallback={"error": "circuit_open"})

        fn.assert_not_called()
        self.assertEqual(result, {"error": "circuit_open"})

    def test_blocked_calls_are_counted(self):
        for _ in range(3):
            _record_failure("etoro", "test")

        # Try 5 blocked calls
        fn = MagicMock()
        for _ in range(5):
            guarded_call("etoro", fn, fallback=None)

        status = get_status("etoro")
        self.assertEqual(status["total_blocked"], 5)
        fn.assert_not_called()


class TestCircuitRecovery(unittest.TestCase):
    """Circuit recovers through half-open → closed after successes."""

    def setUp(self):
        reset_all()

    def tearDown(self):
        reset_all()

    def test_half_open_after_recovery_timeout(self):
        """After recovery timeout, circuit should transition to half-open."""
        for _ in range(3):
            _record_failure("etoro", "test")
        self.assertFalse(_check_circuit("etoro"))  # OPEN

        # Simulate time passing beyond recovery timeout
        from core.external_call_guard import _providers, _lock
        with _lock:
            _providers["etoro"].opened_at = time.time() - 50  # 50s > 45s recovery

        self.assertTrue(_check_circuit("etoro"))  # HALF_OPEN (allows test call)
        status = get_status("etoro")
        self.assertEqual(status["circuit"], "half_open")

    def test_two_successes_close_circuit(self):
        """Two consecutive successes in half-open should close the circuit."""
        # Open circuit
        for _ in range(3):
            _record_failure("etoro", "test")

        # Force half-open
        from core.external_call_guard import _providers, _lock
        with _lock:
            _providers["etoro"].opened_at = time.time() - 50
        _check_circuit("etoro")  # triggers HALF_OPEN

        # Two successes
        _record_success("etoro", 100.0)
        status = get_status("etoro")
        self.assertEqual(status["circuit"], "half_open")  # 1 success, not enough

        _record_success("etoro", 80.0)
        status = get_status("etoro")
        self.assertEqual(status["circuit"], "closed")  # 2 successes → CLOSED

    def test_failure_in_half_open_reopens(self):
        """Any failure in half-open should immediately reopen the circuit."""
        for _ in range(3):
            _record_failure("etoro", "test")

        from core.external_call_guard import _providers, _lock
        with _lock:
            _providers["etoro"].opened_at = time.time() - 50
        _check_circuit("etoro")  # HALF_OPEN

        _record_failure("etoro", "still_broken")
        self.assertFalse(_check_circuit("etoro"))  # Re-OPENED

    def test_success_resets_failure_counter(self):
        """A success should reset consecutive failures to 0."""
        _record_failure("etoro", "err1")
        _record_failure("etoro", "err2")
        _record_success("etoro", 50.0)

        # Now 2 more failures should NOT open (counter reset to 0)
        _record_failure("etoro", "err3")
        _record_failure("etoro", "err4")
        self.assertTrue(_check_circuit("etoro"))  # 2 fails after reset < 3


class TestPerProviderRecoveryTimeout(unittest.TestCase):
    """eToro uses 45s recovery, others use 60s."""

    def setUp(self):
        reset_all()

    def tearDown(self):
        reset_all()

    def test_etoro_recovery_is_45s(self):
        self.assertEqual(PROVIDER_OVERRIDES["etoro"]["recovery_timeout"], 45.0)

    def test_global_recovery_is_60s(self):
        self.assertEqual(RECOVERY_TIMEOUT, 60.0)

    def test_etoro_recovers_at_45s_not_60s(self):
        for _ in range(3):
            _record_failure("etoro", "test")

        from core.external_call_guard import _providers, _lock
        with _lock:
            # 48s ago — past eToro's 45s but before global 60s
            _providers["etoro"].opened_at = time.time() - 48

        self.assertTrue(_check_circuit("etoro"))  # Should be HALF_OPEN

    def test_other_provider_does_not_recover_at_45s(self):
        for _ in range(5):
            _record_failure("openai", "test")

        from core.external_call_guard import _providers, _lock
        with _lock:
            _providers["openai"].opened_at = time.time() - 48  # 48s < 60s

        self.assertFalse(_check_circuit("openai"))  # Still OPEN

    def test_status_remaining_uses_per_provider_timeout(self):
        for _ in range(3):
            _record_failure("etoro", "test")

        status = get_status("etoro")
        # remaining should be based on 45s, not 60s
        self.assertLessEqual(status["remaining_s"], 45.0)


class TestTimeoutRecording(unittest.TestCase):
    """Timeouts are recorded separately from general failures."""

    def setUp(self):
        reset_all()

    def tearDown(self):
        reset_all()

    def test_timeout_counted(self):
        _record_failure("etoro", "Timeout after 6s", is_timeout=True)
        status = get_status("etoro")
        self.assertEqual(status["total_timeouts"], 1)
        self.assertEqual(status["total_failures"], 1)

    def test_non_timeout_not_counted_as_timeout(self):
        _record_failure("etoro", "ConnectionError")
        status = get_status("etoro")
        self.assertEqual(status["total_timeouts"], 0)
        self.assertEqual(status["total_failures"], 1)

    def test_guarded_call_records_timeout(self):
        """guarded_call with a slow function should record a timeout."""
        def slow_fn():
            time.sleep(10)  # longer than 6s timeout

        result = guarded_call("etoro", slow_fn, timeout=0.1, fallback="timed_out")
        self.assertEqual(result, "timed_out")

        status = get_status("etoro")
        self.assertEqual(status["total_timeouts"], 1)


class TestLearningCycleCircuitCheck(unittest.TestCase):
    """Learning cycle should skip when eToro circuit is open."""

    def setUp(self):
        reset_all()

    def tearDown(self):
        reset_all()

    @patch("connectors.etoro.learning_engine._observe_and_record")
    @patch("connectors.etoro.learning_engine._resolve_outcomes")
    @patch("connectors.etoro.learning_engine._update_patterns")
    def test_cycle_skips_when_circuit_open(self, mock_update, mock_resolve, mock_observe):
        """run_learning_cycle should return early without calling any step."""
        # Open the circuit
        for _ in range(3):
            _record_failure("etoro", "api_down")

        from connectors.etoro.learning_engine import run_learning_cycle
        result = run_learning_cycle(symbols=["BTC"])

        self.assertTrue(result.get("skipped"))
        self.assertEqual(result["mode"], "circuit_open")
        mock_observe.assert_not_called()
        mock_resolve.assert_not_called()
        mock_update.assert_not_called()

    @patch("connectors.etoro.learning_engine._observe_and_record",
           return_value={"signals_recorded": 0})
    @patch("connectors.etoro.learning_engine._resolve_outcomes",
           return_value={"resolved": 0, "wins": 0, "losses": 0})
    @patch("connectors.etoro.learning_engine._update_patterns",
           return_value={"patterns": 0, "usable": 0})
    @patch("connectors.etoro.learning_engine._generate_proposals", return_value=[])
    @patch("connectors.etoro.learning_engine._feed_gravity", return_value=0)
    @patch("connectors.etoro.learning_engine._check_positions", return_value=0)
    def test_cycle_runs_when_circuit_closed(self, *mocks):
        """run_learning_cycle should execute normally when circuit is closed."""
        from connectors.etoro.learning_engine import run_learning_cycle
        result = run_learning_cycle(symbols=["BTC"])

        self.assertFalse(result.get("skipped", False))
        self.assertIn("elapsed_s", result)


class TestEtoroClientThresholds(unittest.TestCase):
    """Verify the eToro client has correct timeout and retry values."""

    def test_request_timeout(self):
        from connectors.etoro.etoro_client import REQUEST_TIMEOUT
        self.assertEqual(REQUEST_TIMEOUT, 6)

    def test_max_retries(self):
        from connectors.etoro.etoro_client import MAX_RETRIES
        self.assertEqual(MAX_RETRIES, 2)

    def test_guard_default_timeout(self):
        from core.external_call_guard import DEFAULT_TIMEOUTS
        self.assertEqual(DEFAULT_TIMEOUTS["etoro"], 6.0)

    def test_worst_case_within_watchdog(self):
        """Worst case blocking time must be < 60s watchdog."""
        from connectors.etoro.etoro_client import REQUEST_TIMEOUT, MAX_RETRIES
        threshold = PROVIDER_OVERRIDES["etoro"]["failure_threshold"]
        worst_case = REQUEST_TIMEOUT * MAX_RETRIES * threshold
        self.assertLess(worst_case, 60, f"Worst case {worst_case}s exceeds 60s watchdog")


if __name__ == "__main__":
    unittest.main()
