"""
Vectrax Recovery Engine — self-healing operational layer.

Three-plane architecture (see Spec v1):
  - detectors/   → read-only probes that emit HealthEvent
  - strategies/  → declarative RecoveryStrategy per error class
  - executor.py  → the single place that performs side-effects

Not to be confused with core/resilience/ which contains code-level
resilience utilities (retry, rate limit, error classes). This package
operates at the INFRASTRUCTURE plane: processes, flags, containers,
external services — not code exceptions.

Entry points:
  from core.recovery.orchestrator import HealthOrchestrator
  from core.recovery.executor import RecoveryExecutor
  from core.recovery.registry import load_strategies, load_detectors

Feature-flagged behind env var RESILIENCE_ENABLED=1 (default off).
"""

from core.recovery.events import HealthEvent, HealthState, ExecutionResult
from core.recovery.actions import (
    Action,
    RestartContainer,
    SetEnv,
    CallHttp,
    NotifyHuman,
)
from core.recovery.orchestrator import HealthOrchestrator
from core.recovery.executor import RecoveryExecutor
from core.recovery.strategies import RecoveryStrategy

# Actions intentionally minimal in v1: no ExecShell escape hatch.
# If a strategy needs something new, add a typed Action (e.g. ReloadCaddy,
# RenewCert, FlushPool) — do NOT reintroduce shell execution.

__all__ = [
    "HealthEvent",
    "HealthState",
    "ExecutionResult",
    "Action",
    "RestartContainer",
    "SetEnv",
    "CallHttp",
    "NotifyHuman",
    "HealthOrchestrator",
    "RecoveryExecutor",
    "RecoveryStrategy",
]
