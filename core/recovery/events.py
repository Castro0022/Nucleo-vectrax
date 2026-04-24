"""
Typed events for the recovery engine.

HealthEvent   — emitted by detectors, consumed by the executor.
ExecutionResult — emitted by the executor after running a strategy.

All fields are frozen dataclasses to prevent accidental mutation after
emission.  Serialization: see .to_dict() methods for ledger writes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class HealthState(str, Enum):
    """Three-state health: OK, DEGRADED (soft fail), BROKEN (hard fail)."""

    OK = "OK"
    DEGRADED = "DEGRADED"
    BROKEN = "BROKEN"


@dataclass(frozen=True)
class HealthEvent:
    """Emitted by a detector whenever its probe completes.

    A detector emits events continuously (e.g. once per interval).  It is
    the executor's job to decide when BROKEN warrants action (see
    strategy trigger_events + throttle).

    Fields:
      detector_id: stable identifier of the detector that produced this.
                   Must match a key in HealthOrchestrator registry.
      state:       OK | DEGRADED | BROKEN.  Only BROKEN triggers actions.
      evidence:    free-form dict captured at probe time.  Used for
                   diagnosis and strategy precondition checks.
      ts:          unix timestamp when the probe completed.
      sequence:    monotonic counter local to the detector (for idempotency).
    """

    detector_id: str
    state: HealthState
    evidence: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    sequence: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d

    def is_actionable(self) -> bool:
        """Only BROKEN state triggers strategy execution.

        DEGRADED is observational — logged but ignored by the executor.
        """
        return self.state == HealthState.BROKEN


@dataclass(frozen=True)
class ExecutionResult:
    """Emitted by the executor after running (or dry-running) a strategy.

    Fields:
      strategy_id:   which strategy was attempted.
      event:         the HealthEvent that triggered it.
      attempted_actions: names of Action classes executed, in order.
      succeeded:     True if action_plan completed without rollback.
      rolled_back:   True if rollback_plan ran (action_plan failed).
      escalated:     True if throttle was hit or rollback itself failed.
      duration_ms:   wall-clock time of execute().
      dry_run:       True if this was a simulation (no side-effects).
      ledger_entry_id: opaque id from the ledger write; None if ledger off.
      error:         exception string if execute() itself blew up.
    """

    strategy_id: str
    event: HealthEvent
    attempted_actions: List[str] = field(default_factory=list)
    succeeded: bool = False
    rolled_back: bool = False
    escalated: bool = False
    duration_ms: float = 0.0
    dry_run: bool = False
    ledger_entry_id: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["event"] = self.event.to_dict()
        return d
