"""
Vectrax Interface Layer — Simulation Sandbox
===============================================
Simulates the effect of an action and generates a diff report
**without** executing any real changes on the system.

Integrates with the core state_manager for read-only snapshots and the
audit ledger for traceability.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core import state_manager
from core.audit_ledger import record as ledger_record

from interface_layer.intent_gate import StructuredIntent, IntentType


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DiffEntry:
    """A single change detected between pre- and post-simulation state."""
    path: str           # dot-separated key path (e.g. "governor_mode")
    before: Any
    after: Any

    def describe(self) -> str:
        return f"{self.path}: {self.before!r} → {self.after!r}"


@dataclass
class SimulationReport:
    """Result of a sandbox simulation."""
    intent: StructuredIntent
    diffs: List[DiffEntry] = field(default_factory=list)
    diff_hash: str = ""
    summary: str = ""
    simulated: bool = True

    @property
    def has_changes(self) -> bool:
        return len(self.diffs) > 0


# ---------------------------------------------------------------------------
# Action simulators — one per IntentType
# ---------------------------------------------------------------------------

def _simulate_query(intent: StructuredIntent, snapshot: Dict) -> Dict:
    """Queries are read-only; the state does not change."""
    return copy.deepcopy(snapshot)


def _simulate_execute(intent: StructuredIntent, snapshot: Dict) -> Dict:
    """Simulate an execute action by projecting state changes."""
    projected = copy.deepcopy(snapshot)
    projected.setdefault("pending_actions", [])
    projected["pending_actions"].append({
        "source": "interface_layer",
        "request": intent.raw_request,
        "status": "simulated",
    })
    # Increment hypothetical cycle counter
    projected["cycles"] = projected.get("cycles", 0) + 1
    return projected


def _simulate_configure(intent: StructuredIntent, snapshot: Dict) -> Dict:
    """Simulate a configuration change."""
    projected = copy.deepcopy(snapshot)
    projected.setdefault("config_changes", [])
    projected["config_changes"].append({
        "source": "interface_layer",
        "request": intent.raw_request,
        "status": "simulated",
    })
    return projected


def _simulate_report(intent: StructuredIntent, snapshot: Dict) -> Dict:
    """Reports are read-only; the state does not change."""
    return copy.deepcopy(snapshot)


_SIMULATORS = {
    IntentType.QUERY:     _simulate_query,
    IntentType.EXECUTE:   _simulate_execute,
    IntentType.CONFIGURE: _simulate_configure,
    IntentType.REPORT:    _simulate_report,
}


# ---------------------------------------------------------------------------
# Diff helpers
# ---------------------------------------------------------------------------

def _diff_dicts(
    before: Dict, after: Dict, prefix: str = "",
) -> List[DiffEntry]:
    """Recursively diff two dicts, returning a list of DiffEntry."""
    diffs: List[DiffEntry] = []
    all_keys = set(before.keys()) | set(after.keys())
    for key in sorted(all_keys):
        path = f"{prefix}.{key}" if prefix else key
        val_b = before.get(key)
        val_a = after.get(key)
        if isinstance(val_b, dict) and isinstance(val_a, dict):
            diffs.extend(_diff_dicts(val_b, val_a, prefix=path))
        elif val_b != val_a:
            diffs.append(DiffEntry(path=path, before=val_b, after=val_a))
    return diffs


def _hash_diffs(diffs: List[DiffEntry]) -> str:
    blob = json.dumps([d.describe() for d in diffs], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# SimulationSandbox
# ---------------------------------------------------------------------------

class SimulationSandbox:
    """
    Simulates the effect of a validated intent against a read-only
    snapshot of the current system state.

    Usage::

        sandbox = SimulationSandbox()
        report = sandbox.simulate(intent)
        if report.has_changes:
            # pass to RiskEvaluator
            ...
    """

    def simulate(self, intent: StructuredIntent) -> SimulationReport:
        """
        Run a side-effect-free simulation of *intent*.

        Returns a SimulationReport with diffs describing projected changes.
        """
        if not intent.is_valid:
            report = SimulationReport(
                intent=intent,
                simulated=False,
                summary="Simulation skipped — intent was rejected by IntentGate.",
            )
            self._log(report)
            return report

        # Take a read-only snapshot of current state
        snapshot = state_manager.load()

        # Pick the right simulator
        simulator = _SIMULATORS.get(intent.intent_type, _simulate_query)
        projected = simulator(intent, snapshot)

        # Compute diff
        diffs = _diff_dicts(snapshot, projected)
        diff_hash = _hash_diffs(diffs) if diffs else ""

        summary_parts = [d.describe() for d in diffs]
        summary = "; ".join(summary_parts) if summary_parts else "No state changes projected."

        report = SimulationReport(
            intent=intent,
            diffs=diffs,
            diff_hash=diff_hash,
            summary=summary,
        )
        self._log(report)
        return report

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _log(report: SimulationReport) -> None:
        ledger_record(
            action=f"simulation_sandbox:{report.intent.intent_type.value}",
            actor="interface_layer",
            diff_hash=report.diff_hash,
            decision="simulated" if report.simulated else "skipped",
            reason=report.summary[:200],
            metadata={
                "num_diffs": len(report.diffs),
                "has_changes": report.has_changes,
            },
        )
