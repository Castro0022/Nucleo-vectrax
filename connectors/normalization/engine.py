"""
Vectrax UCE — Normalization Engine.

Transforms raw connector responses into the canonical Vectrax format.
Each connector may provide its own ``normalize_response()`` hook; the
engine guarantees every output conforms to the standard schema.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict

from connectors.core.errors import NormalizationError
from connectors.core.types import OperationType, Sensitivity, Severity
from connectors.normalization.models import (
    NormalizedAction,
    NormalizedEntity,
    NormalizedEvent,
    NormalizedResult,
)

logger = logging.getLogger("vectrax.uce.normalization")


class NormalizationEngine:
    """
    Stateless engine that converts raw connector data to normalized types.

    Usage::

        engine = NormalizationEngine()
        result = engine.normalize(raw_dict, connector_name, operation)
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def normalize(
        self,
        raw: Dict[str, Any],
        connector_name: str,
        operation: OperationType,
    ) -> NormalizedResult:
        """
        Main entry point.  Takes the raw response from a connector and
        returns a ``NormalizedResult``.
        """
        try:
            sensitivity = self._infer_sensitivity(raw)
            severity = self._infer_severity(raw, operation)

            return NormalizedResult(
                source_connector=connector_name,
                timestamp=raw.get("timestamp", time.time()),
                sensitivity=sensitivity,
                severity=severity,
                constellation_hint=raw.get("constellation_hint"),
                raw_data=raw,
                operation=operation.value,
                success=raw.get("success", True),
                error=raw.get("error"),
                data=raw.get("data", raw),
            )
        except Exception as exc:
            logger.error("Normalization failed for %s: %s", connector_name, exc)
            raise NormalizationError(
                f"Failed to normalize response from '{connector_name}': {exc}"
            ) from exc

    def to_event(
        self,
        raw: Dict[str, Any],
        connector_name: str,
    ) -> NormalizedEvent:
        """Convert raw data to a NormalizedEvent."""
        return NormalizedEvent(
            source_connector=connector_name,
            sensitivity=self._infer_sensitivity(raw),
            severity=self._infer_severity(raw, OperationType.OBSERVE),
            event_type=raw.get("event_type", raw.get("type", "generic")),
            description=raw.get("description", raw.get("message", "")),
            raw_data=raw,
        )

    def to_entity(
        self,
        raw: Dict[str, Any],
        connector_name: str,
    ) -> NormalizedEntity:
        """Convert raw data to a NormalizedEntity."""
        return NormalizedEntity(
            source_connector=connector_name,
            sensitivity=self._infer_sensitivity(raw),
            entity_type=raw.get("entity_type", raw.get("type", "generic")),
            name=raw.get("name", raw.get("title", "")),
            content=raw.get("content", raw.get("body", "")),
            raw_data=raw,
        )

    def to_action(
        self,
        raw: Dict[str, Any],
        connector_name: str,
    ) -> NormalizedAction:
        """Convert raw data to a NormalizedAction."""
        return NormalizedAction(
            source_connector=connector_name,
            sensitivity=self._infer_sensitivity(raw),
            action_type=raw.get("action_type", raw.get("type", "generic")),
            target=raw.get("target", raw.get("path", "")),
            success=raw.get("success", False),
            raw_data=raw,
        )

    # ------------------------------------------------------------------
    # Inference helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_sensitivity(raw: Dict[str, Any]) -> Sensitivity:
        """Best-effort sensitivity classification from raw data."""
        explicit = raw.get("sensitivity")
        if explicit:
            try:
                return Sensitivity(explicit)
            except ValueError:
                pass

        # Heuristics: look for keywords suggesting high sensitivity
        text = str(raw).lower()
        if any(kw in text for kw in ("password", "secret", "token", "credential")):
            return Sensitivity.CRITICAL
        if any(kw in text for kw in ("private", "personal", "ssn", "credit")):
            return Sensitivity.HIGH
        return Sensitivity.LOW

    @staticmethod
    def _infer_severity(raw: Dict[str, Any], operation: OperationType) -> Severity:
        """Best-effort severity classification."""
        explicit = raw.get("severity")
        if explicit:
            try:
                return Severity(explicit)
            except ValueError:
                pass

        if raw.get("error"):
            return Severity.ERROR
        if operation == OperationType.WRITE:
            return Severity.WARNING
        return Severity.INFO
