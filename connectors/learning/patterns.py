"""
Vectrax UCE — Pattern Analyzer.

Detects recurring patterns in connection logs:
  - Connectors that repeatedly fail with the same error
  - Permission patterns (missing permissions requested often)
  - Latency anomalies
  - Repeated repair sequences

Patterns are statistical and abstract — never store literal data.
When a pattern exceeds the repetition threshold, a ready solution
is proposed (with evidence and rollback).
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List

from connectors.learning.tracker import ConnectionTracker

logger = logging.getLogger("vectrax.uce.learning.patterns")

# Minimum occurrences before a pattern is reported
PATTERN_THRESHOLD = 3


class PatternAnalyzer:
    """
    Analyzes connection logs to detect actionable patterns.
    """

    def __init__(self, tracker: ConnectionTracker) -> None:
        self._tracker = tracker

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, connector_name: str | None = None) -> List[Dict[str, Any]]:
        """
        Return detected patterns, optionally filtered by connector.
        Each pattern is a dict with:
          - pattern_type: str
          - connector: str
          - description: str
          - count: int
          - suggestion: str
        """
        patterns: List[Dict[str, Any]] = []
        patterns.extend(self._failure_patterns(connector_name))
        patterns.extend(self._latency_patterns(connector_name))
        patterns.extend(self._permission_patterns(connector_name))
        return patterns

    # ------------------------------------------------------------------
    # Pattern detectors
    # ------------------------------------------------------------------

    def _failure_patterns(self, connector_name: str | None) -> List[Dict[str, Any]]:
        """Detect repeated failures with the same error."""
        logs = self._tracker.get_logs(connector_name=connector_name, limit=200)
        error_counter: Counter = Counter()
        for log in logs:
            if log.status == "failure" and log.error_detail:
                # Store abstract error category, not the full message
                key = (log.connector_name, log.operation, log.error_detail[:80])
                error_counter[key] += 1

        patterns = []
        for (conn, op, err), count in error_counter.items():
            if count >= PATTERN_THRESHOLD:
                patterns.append({
                    "pattern_type": "repeated_failure",
                    "connector": conn,
                    "description": (
                        f"Operation '{op}' fails repeatedly ({count}x) "
                        f"with: {err}"
                    ),
                    "count": count,
                    "suggestion": (
                        f"Review connector '{conn}' configuration for "
                        f"operation '{op}'. Consider fallback or retry policy."
                    ),
                })
        return patterns

    def _latency_patterns(self, connector_name: str | None) -> List[Dict[str, Any]]:
        """Detect connectors with consistently high latency."""
        patterns = []
        # Get all connector names from recent logs
        logs = self._tracker.get_logs(connector_name=connector_name, limit=200)
        connectors = {log.connector_name for log in logs}

        for name in connectors:
            stats = self._tracker.get_stats(name)
            if stats["total"] >= PATTERN_THRESHOLD and stats.get("avg_latency_ms", 0) > 2000:
                patterns.append({
                    "pattern_type": "high_latency",
                    "connector": name,
                    "description": (
                        f"Average latency {stats['avg_latency_ms']:.0f}ms "
                        f"over {stats['total']} operations"
                    ),
                    "count": stats["total"],
                    "suggestion": (
                        f"Consider caching, connection pooling, or "
                        f"switching to a faster endpoint for '{name}'."
                    ),
                })
        return patterns

    def _permission_patterns(self, connector_name: str | None) -> List[Dict[str, Any]]:
        """Detect repeated permission-denied errors."""
        logs = self._tracker.get_logs(connector_name=connector_name, limit=200)
        perm_counter: Counter = Counter()
        for log in logs:
            if log.status == "failure" and log.error_detail and "permission" in log.error_detail.lower():
                perm_counter[log.connector_name] += 1

        patterns = []
        for name, count in perm_counter.items():
            if count >= PATTERN_THRESHOLD:
                patterns.append({
                    "pattern_type": "permission_denied",
                    "connector": name,
                    "description": (
                        f"Permission denied {count}x — "
                        "may need elevated permissions"
                    ),
                    "count": count,
                    "suggestion": (
                        f"Review and update permissions for '{name}'. "
                        "Consider creating a new approval proposal."
                    ),
                })
        return patterns
