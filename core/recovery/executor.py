"""
RecoveryExecutor — the only place that performs side-effects.

Contract:
  - on_event() is called by the orchestrator for every BROKEN event.
  - We look up matching strategies, check throttle, then run action_plan.
  - If any action fails, we run rollback_plan (best-effort).
  - If throttle exceeded, we escalate via NotifyHuman instead of running.
  - Dry-run mode: runs the full control flow but calls dry_run() on each
    Action instead of execute().

Thread safety:
  - One lock per strategy_id prevents concurrent execution of the SAME
    strategy.  Different strategies can run in parallel.
  - Throttle state is per-strategy, thread-safe.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, List, Optional

from core.recovery.actions import Action, NotifyHuman
from core.recovery.events import ExecutionResult, HealthEvent
from core.recovery.strategies import RecoveryStrategy
from core.recovery import ledger as _ledger

logger = logging.getLogger("vectrax.recovery.executor")


class RecoveryExecutor:
    """Executes RecoveryStrategies in response to HealthEvents.

    Instantiate once per process. Register strategies at construction
    (or via register_strategy()). Wire via orchestrator.set_executor(self.on_event).
    """

    # Double-probe gate: how many consecutive BROKEN events must arrive
    # BEFORE the executor runs the strategy. This is on TOP of the
    # detector's own BROKEN_CONFIRMATIONS threshold — belt + suspenders
    # against flapping from a transient glitch.
    CONSECUTIVE_BROKEN_REQUIRED = 2

    def __init__(
        self,
        strategies: Optional[List[RecoveryStrategy]] = None,
        dry_run_global: bool = False,
    ) -> None:
        self._strategies: List[RecoveryStrategy] = list(strategies or [])
        self._per_strategy_locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)
        # Throttle: timestamps of recent attempts per strategy_id.
        self._attempts: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=100))
        self._throttle_lock = threading.Lock()
        self._dry_run_global = dry_run_global
        # Consecutive-BROKEN tracker per strategy (for double-probe gate).
        self._consecutive_broken: Dict[str, int] = defaultdict(int)
        self._consecutive_lock = threading.Lock()

    def register_strategy(self, strategy: RecoveryStrategy) -> None:
        self._strategies.append(strategy)
        logger.info(
            "Strategy registered: %s (triggers=%s)",
            strategy.id, strategy.trigger_events,
        )

    def _matching_strategies(self, event: HealthEvent) -> List[RecoveryStrategy]:
        return [
            s for s in self._strategies
            if s.matches(event.detector_id, event.evidence)
        ]

    def is_within_throttle(self, strategy: RecoveryStrategy) -> bool:
        """Return True if we CAN still execute (haven't exceeded budget)."""
        now = time.time()
        with self._throttle_lock:
            attempts = self._attempts[strategy.id]
            # Drop timestamps outside the cooldown window.
            cutoff = now - strategy.cooldown_s
            while attempts and attempts[0] < cutoff:
                attempts.popleft()
            return len(attempts) < strategy.max_attempts_per_window

    def _record_attempt(self, strategy: RecoveryStrategy) -> None:
        with self._throttle_lock:
            self._attempts[strategy.id].append(time.time())

    def on_event(self, event: HealthEvent) -> None:
        """Called by orchestrator for EVERY event (not only BROKEN).

        OK/DEGRADED events reset the consecutive-BROKEN counter for any
        strategy that would have matched — this is what makes the
        double-probe gate anti-flapping.

        BROKEN events increment the counter; a strategy only runs after
        CONSECUTIVE_BROKEN_REQUIRED consecutive BROKENs from the same
        matching detector.
        """
        if event.is_actionable():
            self._handle_broken(event)
        else:
            self._reset_counters_for(event)

    def _reset_counters_for(self, event: HealthEvent) -> None:
        """Clear consecutive-BROKEN counters for any strategy whose
        trigger_events includes this detector. Called on OK/DEGRADED.
        """
        with self._consecutive_lock:
            for s in self._strategies:
                if event.detector_id in s.trigger_events:
                    if self._consecutive_broken.get(s.id, 0) > 0:
                        logger.debug(
                            "Reset consecutive_broken for %s (got %s)",
                            s.id, event.state.value,
                        )
                    self._consecutive_broken[s.id] = 0

    def _handle_broken(self, event: HealthEvent) -> None:
        """BROKEN event path: increment counter, gate by threshold."""
        matches = self._matching_strategies(event)
        if not matches:
            logger.info(
                "No strategy matches event from %s", event.detector_id,
            )
            return
        for strategy in matches:
            with self._consecutive_lock:
                self._consecutive_broken[strategy.id] += 1
                seen = self._consecutive_broken[strategy.id]
            if seen < self.CONSECUTIVE_BROKEN_REQUIRED:
                logger.info(
                    "Double-probe gate: strategy=%s seen=%d/%d — holding",
                    strategy.id, seen, self.CONSECUTIVE_BROKEN_REQUIRED,
                )
                continue
            logger.info(
                "Double-probe gate satisfied for %s (seen=%d) — proceeding",
                strategy.id, seen,
            )
            self._maybe_execute(strategy, event)
            # Reset counter AFTER a successful trigger so the next BROKEN
            # cycle starts from zero (next trigger also requires 2 BROKENs).
            with self._consecutive_lock:
                self._consecutive_broken[strategy.id] = 0

    def _maybe_execute(self, strategy: RecoveryStrategy, event: HealthEvent) -> None:
        """Check throttle / approval, then execute or escalate."""
        lock = self._per_strategy_locks[strategy.id]
        if not lock.acquire(blocking=False):
            logger.info(
                "Strategy %s already running — skipping duplicate trigger",
                strategy.id,
            )
            return
        try:
            # Human approval gate
            if strategy.require_human_approval:
                logger.warning(
                    "Strategy %s requires human approval — dry-run + escalate",
                    strategy.id,
                )
                result = self.execute(strategy, event, dry_run=True)
                self.escalate(
                    strategy,
                    f"Strategy {strategy.id} matched but require_human_approval=True. "
                    f"Event: {event.detector_id} BROKEN. Dry-run done. Human must review.",
                )
                return

            # Throttle gate
            if not self.is_within_throttle(strategy):
                logger.warning(
                    "Strategy %s exceeded throttle (%d/%ds) — escalating",
                    strategy.id,
                    strategy.max_attempts_per_window,
                    strategy.cooldown_s,
                )
                self.escalate(
                    strategy,
                    f"Strategy {strategy.id} throttled: "
                    f"{strategy.max_attempts_per_window} attempts in "
                    f"{strategy.cooldown_s}s. Human intervention required.",
                )
                return

            # Execute for real (or dry-run if global flag OR this strategy
            # has opted into forced dry-run independent of the global flag).
            effective_dry_run = self._dry_run_global or strategy.force_dry_run
            self.execute(strategy, event, dry_run=effective_dry_run)
        finally:
            lock.release()

    def execute(
        self,
        strategy: RecoveryStrategy,
        event: HealthEvent,
        dry_run: bool = False,
    ) -> ExecutionResult:
        """Run the action_plan.  Public; also callable directly by tests."""
        mode = "DRY-RUN" if dry_run else "EXECUTE"
        logger.info(
            "%s strategy=%s event=%s evidence=%s",
            mode, strategy.id, event.detector_id, event.evidence,
        )

        self._record_attempt(strategy)
        t0 = time.time()
        attempted: List[str] = []
        succeeded = False
        rolled_back = False
        error: Optional[str] = None

        try:
            for action in strategy.action_plan:
                attempted.append(action.describe())
                ok = self._invoke(action, dry_run)
                if not ok:
                    error = f"Action failed: {action.describe()}"
                    logger.error(
                        "Action failed in strategy %s: %s",
                        strategy.id, action.describe(),
                    )
                    # Run rollback_plan (best-effort; log but don't propagate)
                    rolled_back = self._rollback(strategy, dry_run)
                    self.escalate(
                        strategy,
                        f"Strategy {strategy.id} failed at action "
                        f"{action.describe()}. Rollback ran={rolled_back}.",
                    )
                    break
            else:
                succeeded = True
                logger.info("Strategy %s completed OK", strategy.id)
        except Exception as exc:
            error = f"Executor crashed: {exc}"
            logger.exception("Executor crashed on strategy %s", strategy.id)
            rolled_back = self._rollback(strategy, dry_run)
            self.escalate(
                strategy,
                f"Strategy {strategy.id} crashed: {exc}. "
                f"Rollback ran={rolled_back}.",
            )

        duration_ms = (time.time() - t0) * 1000.0
        result = ExecutionResult(
            strategy_id=strategy.id,
            event=event,
            attempted_actions=attempted,
            succeeded=succeeded,
            rolled_back=rolled_back,
            escalated=(error is not None),
            duration_ms=duration_ms,
            dry_run=dry_run,
            error=error,
        )
        entry_id = _ledger.write_entry("execution", result.to_dict())
        if entry_id:
            # Can't mutate frozen dataclass; return a new one with id set.
            result = ExecutionResult(
                strategy_id=result.strategy_id,
                event=result.event,
                attempted_actions=result.attempted_actions,
                succeeded=result.succeeded,
                rolled_back=result.rolled_back,
                escalated=result.escalated,
                duration_ms=result.duration_ms,
                dry_run=result.dry_run,
                ledger_entry_id=entry_id,
                error=result.error,
            )
        return result

    def _invoke(self, action: Action, dry_run: bool) -> bool:
        """Run one action. Never raises; returns bool."""
        try:
            if dry_run:
                action.dry_run()
                return True
            return bool(action.execute())
        except Exception as exc:
            logger.exception("Action raised: %s", action.describe())
            return False

    def _rollback(self, strategy: RecoveryStrategy, dry_run: bool) -> bool:
        """Run rollback_plan. Best-effort. Returns True if all rollback
        actions succeeded (vacuously True if list is empty).
        """
        if not strategy.rollback_plan:
            return True
        logger.warning("Rolling back strategy %s", strategy.id)
        all_ok = True
        for action in strategy.rollback_plan:
            ok = self._invoke(action, dry_run)
            if not ok:
                logger.error(
                    "Rollback action failed in %s: %s",
                    strategy.id, action.describe(),
                )
                all_ok = False
        return all_ok

    def escalate(self, strategy: RecoveryStrategy, reason: str) -> None:
        """Emit an escalation notification.  Always writes to ledger."""
        logger.warning("ESCALATE strategy=%s reason=%s", strategy.id, reason)
        notify = NotifyHuman(message=reason, channel=strategy.escalation_target)
        try:
            notify.execute()
        except Exception as exc:
            logger.exception("Escalation NotifyHuman failed: %s", exc)
        _ledger.write_entry(
            "escalation",
            {"strategy_id": strategy.id, "reason": reason},
        )
