"""
Vectrax Interface Layer — Intent Gate
=======================================
Interprets human requests and translates them into structured intents.
Rejects requests that don't match allowed patterns, logging the event.

This module is the mandatory entry point for all human interactions.
No action may bypass the IntentGate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from core.audit_ledger import record as ledger_record


# ---------------------------------------------------------------------------
# Intent categories — extend this as new capabilities are added
# ---------------------------------------------------------------------------

class IntentType(str, Enum):
    QUERY       = "query"        # read-only information request
    EXECUTE     = "execute"      # run an action / workflow
    CONFIGURE   = "configure"    # change a non-core setting
    REPORT      = "report"       # generate a report or summary
    UNKNOWN     = "unknown"      # could not classify


# Allowed patterns: regex → IntentType mapping.
# Order matters — first match wins.
ALLOWED_PATTERNS: List[tuple[str, IntentType]] = [
    (r"(?i)\b(status|show|list|get|read|query|check)\b",   IntentType.QUERY),
    (r"(?i)\b(run|execute|start|launch|deploy|apply)\b",   IntentType.EXECUTE),
    (r"(?i)\b(set|config|configure|update setting)\b",     IntentType.CONFIGURE),
    (r"(?i)\b(report|summary|summarize|audit|export)\b",   IntentType.REPORT),
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StructuredIntent:
    """Result of intent classification."""
    raw_request: str
    intent_type: IntentType
    matched_pattern: Optional[str] = None
    parameters: Dict[str, str] = field(default_factory=dict)
    is_valid: bool = True
    rejection_reason: str = ""


# ---------------------------------------------------------------------------
# IntentGate
# ---------------------------------------------------------------------------

class IntentGate:
    """
    Validates incoming human requests against allowed patterns.

    Usage::

        gate = IntentGate()
        intent = gate.process("show current governor status")
        if intent.is_valid:
            # proceed to SimulationSandbox
            ...
    """

    def __init__(
        self,
        patterns: Optional[List[tuple[str, IntentType]]] = None,
    ) -> None:
        self.patterns = patterns or ALLOWED_PATTERNS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(self, request: str) -> StructuredIntent:
        """
        Classify *request* into a StructuredIntent.

        If the request does not match any allowed pattern, the intent is
        marked invalid and the event is recorded in the audit ledger.
        """
        if not request or not request.strip():
            return self._reject(request, "Empty request")

        for pattern, intent_type in self.patterns:
            if re.search(pattern, request):
                intent = StructuredIntent(
                    raw_request=request,
                    intent_type=intent_type,
                    matched_pattern=pattern,
                    is_valid=True,
                )
                self._log(intent)
                return intent

        return self._reject(request, "No matching intent pattern")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _reject(self, request: str, reason: str) -> StructuredIntent:
        intent = StructuredIntent(
            raw_request=request,
            intent_type=IntentType.UNKNOWN,
            is_valid=False,
            rejection_reason=reason,
        )
        self._log(intent)
        return intent

    @staticmethod
    def _log(intent: StructuredIntent) -> None:
        decision = "approved" if intent.is_valid else "rejected"
        ledger_record(
            action=f"intent_gate:{intent.intent_type.value}",
            actor="human",
            decision=decision,
            reason=intent.rejection_reason or f"Matched pattern for {intent.intent_type.value}",
            metadata={
                "raw_request": intent.raw_request,
                "matched_pattern": intent.matched_pattern,
            },
        )
