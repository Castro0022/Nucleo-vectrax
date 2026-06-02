"""
Cognitive Tracer — records each step of the cognitive loop.

Provides complete audit trail persisted in vault/cognition_trace.jsonl.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from cognition.types import TraceEntry

logger = logging.getLogger("vectrax.cognition.orchestrator.tracer")

VAULT_DIR = os.environ.get("VECTRAX_VAULT_DIR", os.path.join(os.path.expanduser("~"), "Vectrax", "vault"))
TRACE_PATH = os.path.join(VAULT_DIR, "cognition_trace.jsonl")


class CognitiveTracer:
    """Records and persists cognitive loop traces."""

    def __init__(self) -> None:
        self._current_cycle: List[TraceEntry] = []
        self._cycle_id: str = ""

    def begin_cycle(self, cycle_id: str) -> None:
        """Start tracing a new cognitive cycle."""
        self._cycle_id = cycle_id
        self._current_cycle = []

    def record(
        self,
        step: str,
        input_summary: str = "",
        output_summary: str = "",
        duration_ms: float = 0.0,
        success: bool = True,
        error: Optional[str] = None,
    ) -> TraceEntry:
        """Record one step in the current cycle."""
        entry = TraceEntry(
            step=step,
            input_summary=input_summary[:200],
            output_summary=output_summary[:200],
            duration_ms=round(duration_ms, 2),
            success=success,
            error=error[:200] if error else None,
        )
        self._current_cycle.append(entry)
        return entry

    def end_cycle(self) -> List[TraceEntry]:
        """Finalise and persist the current cycle trace."""
        self._persist()
        entries = self._current_cycle
        self._current_cycle = []
        return entries

    @property
    def current_entries(self) -> List[TraceEntry]:
        return list(self._current_cycle)

    # ------------------------------------------------------------------
    # Read traces
    # ------------------------------------------------------------------

    def recent(self, n: int = 20) -> List[Dict[str, Any]]:
        """Read the most recent trace entries from disk."""
        if not os.path.isfile(TRACE_PATH):
            return []
        try:
            with open(TRACE_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
            records = []
            for line in lines:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            return list(reversed(records[-n:]))
        except OSError:
            return []

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self) -> None:
        if not self._current_cycle:
            return
        try:
            os.makedirs(VAULT_DIR, exist_ok=True)
            with open(TRACE_PATH, "a", encoding="utf-8") as f:
                for entry in self._current_cycle:
                    record = entry.to_dict()
                    record["cycle_id"] = self._cycle_id
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.debug("Trace persist failed: %s", exc)
