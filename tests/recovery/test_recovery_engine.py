"""
Recovery engine unit tests (Iteration 1).

Covers:
  - HealthOrchestrator dispatches only BROKEN events to executor.
  - RecoveryExecutor throttle: 3 attempts/window → 4th escalates.
  - classA_revert_webhook: dry-run produces expected action sequence
    WITHOUT touching any real file or running any subprocess.

All tests use unittest (stdlib, no pytest dep). Self-contained; no
network, no disk writes outside tmp.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root on path
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.recovery.actions import Action, NotifyHuman
from core.recovery.events import ExecutionResult, HealthEvent, HealthState
from core.recovery.executor import RecoveryExecutor
from core.recovery.orchestrator import HealthOrchestrator
from core.recovery.strategies import RecoveryStrategy


# --- Regression: ExecShell must NOT be reintroduced ---

def test_execshell_removed_from_api():
    """Guard against accidental reintroduction of the shell escape hatch."""
    import core.recovery as recovery_pkg
    assert not hasattr(recovery_pkg, "ExecShell"), (
        "ExecShell was intentionally removed in v1. If a new recovery "
        "needs a capability, add a typed Action — do NOT reintroduce shell "
        "execution."
    )


# --- Test doubles --------------------------------------------------------


class _MockAction(Action):
    """Action that records calls and can be configured to succeed or fail."""

    def __init__(self, name: str, should_succeed: bool = True) -> None:
        self.name = name
        self.should_succeed = should_succeed
        self.execute_calls = 0
        self.dry_run_calls = 0

    def execute(self) -> bool:
        self.execute_calls += 1
        return self.should_succeed

    def dry_run(self) -> None:
        self.dry_run_calls += 1

    def describe(self) -> str:
        return f"MockAction({self.name})"


def _always_match(evidence):
    return True


# --- Tests ---------------------------------------------------------------


class TestOrchestratorDispatch(unittest.TestCase):
    """HealthOrchestrator should route only BROKEN events to the executor."""

    def setUp(self):
        # Isolate ledger path to avoid polluting vault
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["RECOVERY_LEDGER_PATH"] = os.path.join(self._tmpdir.name, "ledger.jsonl")
        # Reload ledger module to pick up env var
        import importlib
        from core.recovery import ledger
        importlib.reload(ledger)

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("RECOVERY_LEDGER_PATH", None)

    def test_broken_event_dispatched(self):
        calls = []

        def callback(event):
            calls.append(event)

        orch = HealthOrchestrator()
        orch.set_executor(callback)
        orch._dispatch(HealthEvent(
            detector_id="test_det",
            state=HealthState.BROKEN,
            evidence={"reason": "x"},
        ))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].state, HealthState.BROKEN)

    def test_all_events_dispatched_to_executor(self):
        """New contract: orchestrator forwards ALL events (OK + DEGRADED + BROKEN)
        to the executor so it can reset consecutive-BROKEN counters.
        The executor — not the orchestrator — decides what to act on.
        """
        calls = []
        orch = HealthOrchestrator()
        orch.set_executor(lambda e: calls.append(e.state))
        for state in (HealthState.OK, HealthState.DEGRADED, HealthState.BROKEN):
            orch._dispatch(HealthEvent(detector_id="test_det", state=state))
        self.assertEqual(calls, [HealthState.OK, HealthState.DEGRADED, HealthState.BROKEN])

    def test_cannot_register_after_start(self):
        orch = HealthOrchestrator()
        # Register one detector so there's something to start
        orch.register_detector("d1", lambda: HealthEvent("d1", HealthState.OK), 5)
        orch.start()
        try:
            with self.assertRaises(RuntimeError):
                orch.register_detector("d2", lambda: HealthEvent("d2", HealthState.OK), 5)
        finally:
            orch.stop()


class TestExecutorThrottle(unittest.TestCase):
    """RecoveryExecutor should throttle repeated failures."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["RECOVERY_LEDGER_PATH"] = os.path.join(self._tmpdir.name, "ledger.jsonl")
        import importlib
        from core.recovery import ledger
        importlib.reload(ledger)

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("RECOVERY_LEDGER_PATH", None)

    def _broken_event(self):
        return HealthEvent(detector_id="test_det", state=HealthState.BROKEN)

    def _ok_event(self):
        return HealthEvent(detector_id="test_det", state=HealthState.OK)

    def test_double_probe_gate_holds_on_first_broken(self):
        """Single BROKEN event must NOT trigger; need 2 consecutive."""
        action = _MockAction("a")
        strategy = RecoveryStrategy(
            id="dp_test",
            trigger_events=["test_det"],
            action_plan=[action],
            precondition=_always_match,
            max_attempts_per_window=5,
            cooldown_s=60,
        )
        ex = RecoveryExecutor(strategies=[strategy])
        ex.on_event(self._broken_event())
        self.assertEqual(action.execute_calls, 0, "1st BROKEN must not trigger")
        ex.on_event(self._broken_event())
        self.assertEqual(action.execute_calls, 1, "2nd consecutive BROKEN must trigger")

    def test_ok_in_between_resets_counter(self):
        """BROKEN, then OK, then BROKEN — should NOT trigger."""
        action = _MockAction("a")
        strategy = RecoveryStrategy(
            id="dp_reset_test",
            trigger_events=["test_det"],
            action_plan=[action],
            precondition=_always_match,
            max_attempts_per_window=5,
            cooldown_s=60,
        )
        ex = RecoveryExecutor(strategies=[strategy])
        ex.on_event(self._broken_event())    # seen=1
        ex.on_event(self._ok_event())        # reset
        ex.on_event(self._broken_event())    # seen=1 again
        self.assertEqual(action.execute_calls, 0, "OK should have reset the counter")
        ex.on_event(self._broken_event())    # seen=2 → triggers
        self.assertEqual(action.execute_calls, 1)

    def test_throttle_two_attempts_third_escalates(self):
        """Throttle 2/60: after 2 triggers, 3rd escalates (classA config)."""
        action = _MockAction("a")
        strategy = RecoveryStrategy(
            id="thr_test",
            trigger_events=["test_det"],
            action_plan=[action],
            precondition=_always_match,
            max_attempts_per_window=2,
            cooldown_s=3600,
            escalation_target="file",
        )
        ex = RecoveryExecutor(strategies=[strategy])
        # Each trigger needs 2 BROKEN events due to double-probe gate.
        # Trigger #1
        ex.on_event(self._broken_event())
        ex.on_event(self._broken_event())
        # Trigger #2
        ex.on_event(self._broken_event())
        ex.on_event(self._broken_event())
        self.assertEqual(action.execute_calls, 2, "2 triggers, both ran")

        # Trigger #3 → throttled
        with patch("core.recovery.executor.NotifyHuman") as mock_notify_cls:
            mock_notify_instance = MagicMock()
            mock_notify_instance.execute.return_value = True
            mock_notify_cls.return_value = mock_notify_instance
            ex.on_event(self._broken_event())
            ex.on_event(self._broken_event())
            self.assertEqual(action.execute_calls, 2, "3rd trigger must be throttled")
            mock_notify_cls.assert_called_once()
            call_kwargs = mock_notify_cls.call_args.kwargs
            self.assertIn("throttled", call_kwargs.get("message", "").lower())

    def test_rollback_runs_on_action_failure(self):
        failing = _MockAction("fails", should_succeed=False)
        rollback = _MockAction("rollback")
        strategy = RecoveryStrategy(
            id="test_strat_rollback",
            trigger_events=["test_det"],
            action_plan=[failing],
            rollback_plan=[rollback],
            precondition=_always_match,
            max_attempts_per_window=5,
            cooldown_s=60,
        )
        ex = RecoveryExecutor(strategies=[strategy])
        # Double-probe gate needs 2 BROKENs.
        ex.on_event(HealthEvent(detector_id="test_det", state=HealthState.BROKEN))
        ex.on_event(HealthEvent(detector_id="test_det", state=HealthState.BROKEN))
        self.assertEqual(failing.execute_calls, 1)
        self.assertEqual(rollback.execute_calls, 1, "rollback should have run")


class TestClassADryRun(unittest.TestCase):
    """classA_revert_webhook: dry-run produces the expected sequence WITHOUT side effects."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["RECOVERY_LEDGER_PATH"] = os.path.join(self._tmpdir.name, "ledger.jsonl")
        import importlib
        from core.recovery import ledger
        importlib.reload(ledger)
        # Write a fake .env to a temp path
        self._env_path = os.path.join(self._tmpdir.name, "test.env")
        with open(self._env_path, "w") as f:
            f.write("USE_WEBHOOK=1\nTELEGRAM_BOT_TOKEN=FAKE\n")

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("RECOVERY_LEDGER_PATH", None)

    def test_dry_run_does_not_touch_env(self):
        from core.recovery.strategies import classA_revert_webhook
        strategy = classA_revert_webhook.build(
            env_path=self._env_path,
            compose_dir="/nowhere",
        )
        ex = RecoveryExecutor(strategies=[strategy], dry_run_global=True)

        # Read file contents before
        with open(self._env_path) as f:
            before = f.read()

        event = HealthEvent(
            detector_id="gateway_silent",
            state=HealthState.BROKEN,
            evidence={
                "mode": "webhook",
                "webhook_url_present": False,
                "reason": "USE_WEBHOOK=1 but Telegram reports no webhook URL registered",
            },
        )
        # Double-probe gate requires 2 consecutive BROKENs
        ex.on_event(event)
        ex.on_event(event)

        # Read after
        with open(self._env_path) as f:
            after = f.read()
        self.assertEqual(before, after, ".env must be unchanged in dry-run")

    def test_precondition_rejects_partial_match(self):
        """AND precondition: need ALL 3 indicators."""
        from core.recovery.strategies import classA_revert_webhook
        strategy = classA_revert_webhook.build(
            env_path=self._env_path,
            compose_dir="/nowhere",
        )
        # mode=webhook + webhook_url_present=False, but reason does NOT match
        partial = {
            "mode": "webhook",
            "webhook_url_present": False,
            "reason": "some other problem",
        }
        self.assertFalse(strategy.precondition(partial))

        # mode=webhook + reason match, but webhook_url_present is not explicitly False
        partial2 = {
            "mode": "webhook",
            # webhook_url_present missing
            "reason": "USE_WEBHOOK=1 but Telegram reports no webhook URL registered",
        }
        self.assertFalse(strategy.precondition(partial2))

    def test_precondition_rejects_unrelated_event(self):
        """Event from gateway_silent but with OK evidence should NOT match classA."""
        from core.recovery.strategies import classA_revert_webhook
        strategy = classA_revert_webhook.build(
            env_path=self._env_path,
            compose_dir="/nowhere",
        )
        # Evidence that says "mode=webhook AND webhook IS registered" → classA should skip
        bad_event_evidence = {
            "mode": "webhook",
            "webhook_url_present": True,  # webhook IS registered; no bug
        }
        self.assertFalse(strategy.precondition(bad_event_evidence))

    def test_precondition_accepts_target_bug(self):
        """The specific 10h bug signature should match."""
        from core.recovery.strategies import classA_revert_webhook
        strategy = classA_revert_webhook.build(
            env_path=self._env_path,
            compose_dir="/nowhere",
        )
        good_event_evidence = {
            "mode": "webhook",
            "webhook_url_present": False,
            "reason": "USE_WEBHOOK=1 but Telegram reports no webhook URL registered",
        }
        self.assertTrue(strategy.precondition(good_event_evidence))

    def test_precondition_rejects_long_poll_mode(self):
        """If mode is long-poll, classA should not match (classB territory)."""
        from core.recovery.strategies import classA_revert_webhook
        strategy = classA_revert_webhook.build(
            env_path=self._env_path,
            compose_dir="/nowhere",
        )
        self.assertFalse(strategy.precondition({"mode": "long-poll"}))


class TestGatewaySilentDetector(unittest.TestCase):
    """Detector sanity checks — using mocks to avoid real HTTP / disk."""

    def setUp(self):
        from core.recovery.detectors import gateway_silent
        gateway_silent.reset_state_for_tests()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._env_path = os.path.join(self._tmpdir.name, "test.env")
        os.environ["RECOVERY_ENV_PATH"] = self._env_path

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("RECOVERY_ENV_PATH", None)

    def test_healthy_webhook_returns_OK(self):
        from core.recovery.detectors import gateway_silent as gs
        with open(self._env_path, "w") as f:
            f.write("USE_WEBHOOK=1\nTELEGRAM_BOT_TOKEN=FAKE\n")
        gs.ENV_PATH = self._env_path  # override module-level var

        with patch.object(gs, "_check_webhook_registered") as mock_check:
            mock_check.return_value = (True, {"webhook_url_present": True})
            event = gs.probe()
            self.assertEqual(event.state, HealthState.OK)

    def test_bug_signature_emits_BROKEN_after_3_failures(self):
        from core.recovery.detectors import gateway_silent as gs
        with open(self._env_path, "w") as f:
            f.write("USE_WEBHOOK=1\nTELEGRAM_BOT_TOKEN=FAKE\n")
        gs.ENV_PATH = self._env_path
        gs.reset_state_for_tests()

        failing_ev = {
            "webhook_url_present": False,
            "reason": "USE_WEBHOOK=1 but Telegram reports no webhook URL registered",
        }
        with patch.object(gs, "_check_webhook_registered") as mock_check:
            mock_check.return_value = (False, failing_ev)
            # First 2 failures → DEGRADED
            self.assertEqual(gs.probe().state, HealthState.DEGRADED)
            self.assertEqual(gs.probe().state, HealthState.DEGRADED)
            # 3rd failure → BROKEN
            event = gs.probe()
            self.assertEqual(event.state, HealthState.BROKEN)
            self.assertEqual(event.evidence["consecutive_failures"], 3)
            self.assertFalse(event.evidence["webhook_url_present"])

    def test_recovery_resets_counter(self):
        from core.recovery.detectors import gateway_silent as gs
        with open(self._env_path, "w") as f:
            f.write("USE_WEBHOOK=1\nTELEGRAM_BOT_TOKEN=FAKE\n")
        gs.ENV_PATH = self._env_path
        gs.reset_state_for_tests()

        # Fail twice
        with patch.object(gs, "_check_webhook_registered") as mock_check:
            mock_check.return_value = (False, {"webhook_url_present": False})
            gs.probe()
            gs.probe()

        # Then OK — counter should reset
        with patch.object(gs, "_check_webhook_registered") as mock_check:
            mock_check.return_value = (True, {"webhook_url_present": True})
            event = gs.probe()
            self.assertEqual(event.state, HealthState.OK)
            self.assertEqual(event.evidence["consecutive_failures"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
