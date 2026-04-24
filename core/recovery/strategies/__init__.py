"""
Recovery strategies package.

Each module in this package defines one strategy. The registry
(core/recovery/registry.py) scans this package to discover them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.recovery.actions import Action


@dataclass(frozen=True)
class RecoveryStrategy:
    """Declarative recovery strategy for ONE class of error.

    A strategy does not execute anything by itself — it describes WHAT
    should happen when a matching event arrives.  Execution is the
    RecoveryExecutor's job.

    Fields:
      id:               stable identifier, e.g. "classA_gateway_silent".
                        Used in throttle tracking and ledger entries.
      trigger_events:   list of detector_ids that activate this strategy.
                        If any of them emits BROKEN, this strategy is
                        considered (subject to precondition + throttle).
      precondition:     callable(event.evidence) -> bool.  Decides whether
                        THIS event matches THIS strategy.  Used to
                        disambiguate when multiple strategies share a
                        detector.  Default: always-true.
      action_plan:      ordered list of Actions to run.  Stop at first
                        failure; trigger rollback_plan.
      rollback_plan:    actions to run if action_plan fails partway.
                        Best-effort; failures are logged but not cascaded.
      max_attempts_per_window: throttle budget (see cooldown_s).
      cooldown_s:       time window for max_attempts_per_window.  If a
                        strategy hits the cap within this window, it is
                        escalated instead of retried.
      escalation_target: channel for NotifyHuman when throttle exhausted.
                        Currently only "file" works in v1.
      require_human_approval: if True, the executor will ONLY run this
                        strategy in dry-run mode and emit an escalation.
                        Used for actions that modify persistent state
                        which the operator wants to review.
    """

    id: str
    trigger_events: List[str]
    action_plan: List[Action]
    rollback_plan: List[Action] = field(default_factory=list)
    precondition: Callable[[Dict[str, Any]], bool] = lambda evidence: True
    max_attempts_per_window: int = 3
    cooldown_s: int = 1800  # 30 min
    escalation_target: str = "file"
    require_human_approval: bool = False
    force_dry_run: bool = False  # if True, this strategy ALWAYS runs in
    # dry-run regardless of the executor's global flag. Used to keep a
    # strategy in observation while others are promoted to real mode.
    description: str = ""

    def matches(self, detector_id: str, evidence: Dict[str, Any]) -> bool:
        """Return True if this strategy should be considered for this event."""
        if detector_id not in self.trigger_events:
            return False
        try:
            return bool(self.precondition(evidence))
        except Exception:
            return False


__all__ = ["RecoveryStrategy"]
