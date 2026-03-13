"""
Vectrax Interface Layer — Human Confirmation
===============================================
Final gate in the interface pipeline. If the RiskEvaluator determines
that risk is HIGH, this module blocks execution until a human explicitly
confirms (or rejects) the action.

LOW and MEDIUM risk actions pass through automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from core.audit_ledger import record as ledger_record

from interface_layer.risk_evaluator import RiskVerdict, SimpleRiskLevel
from interface_layer.simulation_sandbox import SimulationReport


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class ConfirmationStatus(str, Enum):
    AUTO_APPROVED = "auto_approved"   # risk was LOW or MEDIUM
    CONFIRMED     = "confirmed"       # human approved HIGH-risk action
    REJECTED      = "rejected"        # human rejected HIGH-risk action
    PENDING       = "pending"         # awaiting human response
    TIMED_OUT     = "timed_out"       # no response within deadline


@dataclass
class ConfirmationResult:
    """Outcome of the human confirmation gate."""
    status: ConfirmationStatus
    verdict: RiskVerdict
    report: SimulationReport
    may_execute: bool
    message: str = ""


# ---------------------------------------------------------------------------
# Default confirmation callback (interactive stdin prompt)
# ---------------------------------------------------------------------------

def _default_prompt(verdict: RiskVerdict, report: SimulationReport) -> bool:
    """
    Prompt the user on stdin and return True if they confirm.

    This is the default callback; callers can inject their own
    (e.g. a web UI or API handler).
    """
    print("\n" + "=" * 60)
    print("  ⚠  HIGH-RISK ACTION — Confirmation Required")
    print("=" * 60)
    print(f"  Risk level : {verdict.level.value}")
    print(f"  Risk score : {verdict.score:.2f}")
    print(f"  Governor   : {verdict.governor_mode}")
    print(f"  Diffs      : {len(report.diffs)}")
    if report.summary:
        print(f"  Summary    : {report.summary[:120]}")
    print("-" * 60)

    try:
        answer = input("  Proceed? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"

    return answer in ("y", "yes", "si", "sí")


# ---------------------------------------------------------------------------
# HumanConfirmation
# ---------------------------------------------------------------------------

class HumanConfirmation:
    """
    Gates action execution behind human approval when risk is HIGH.

    Usage::

        gate = HumanConfirmation()
        result = gate.check(verdict, report)
        if result.may_execute:
            # safe to execute
            ...

    You can supply a custom *confirm_fn* (e.g. for a web UI) that
    receives (RiskVerdict, SimulationReport) and returns bool.
    """

    def __init__(
        self,
        confirm_fn: Optional[Callable[[RiskVerdict, SimulationReport], bool]] = None,
    ) -> None:
        self._confirm_fn = confirm_fn or _default_prompt

    def check(
        self,
        verdict: RiskVerdict,
        report: SimulationReport,
    ) -> ConfirmationResult:
        """
        If *verdict* requires confirmation, invoke the confirmation
        callback. Otherwise auto-approve.
        """
        if not verdict.requires_confirmation:
            result = ConfirmationResult(
                status=ConfirmationStatus.AUTO_APPROVED,
                verdict=verdict,
                report=report,
                may_execute=True,
                message=f"Auto-approved (risk {verdict.level.value}).",
            )
            self._log(result)
            return result

        # HIGH risk — ask the human
        confirmed = self._confirm_fn(verdict, report)

        if confirmed:
            result = ConfirmationResult(
                status=ConfirmationStatus.CONFIRMED,
                verdict=verdict,
                report=report,
                may_execute=True,
                message="Human confirmed HIGH-risk action.",
            )
        else:
            result = ConfirmationResult(
                status=ConfirmationStatus.REJECTED,
                verdict=verdict,
                report=report,
                may_execute=False,
                message="Human rejected HIGH-risk action.",
            )

        self._log(result)
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _log(result: ConfirmationResult) -> None:
        ledger_record(
            action=f"human_confirmation:{result.status.value}",
            actor="human" if result.status != ConfirmationStatus.AUTO_APPROVED else "interface_layer",
            decision=result.status.value,
            reason=result.message,
            diff_hash=result.report.diff_hash,
            metadata={
                "risk_level": result.verdict.level.value,
                "risk_score": round(result.verdict.score, 4),
                "may_execute": result.may_execute,
            },
        )
