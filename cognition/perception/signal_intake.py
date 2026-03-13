"""
Signal Intake — ingests raw data from UCE connectors.

Every piece of external data passes through NormalizationEngine before
becoming a CognitiveSignal.  No raw content is stored.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from cognition.errors import SignalIntakeError
from cognition.types import CognitiveSignal, SignalSource

logger = logging.getLogger("vectrax.cognition.perception.intake")

# Map connector family → SignalSource
_FAMILY_MAP: Dict[str, SignalSource] = {
    "filesystem": SignalSource.FILESYSTEM,
    "shell": SignalSource.SHELL,
    "database": SignalSource.DATABASE,
    "rest_api": SignalSource.API,
    "webhook": SignalSource.WEBHOOK,
    "cloud_storage": SignalSource.CLOUD,
    "google_workspace": SignalSource.CLOUD,
    "icloud": SignalSource.CLOUD,
}


class SignalIntake:
    """
    Ingests signals from the Universal Connection Engine.

    Two modes:
      - ``poll_connectors()`` — reads via ConnectionEngine.observe()
      - ``ingest_raw()`` — accepts pre-built dicts (for internal events)
    """

    def __init__(self) -> None:
        self._normalizer = None  # lazy import to avoid import cycles

    def _get_normalizer(self):
        if self._normalizer is None:
            from connectors.normalization.engine import NormalizationEngine
            self._normalizer = NormalizationEngine()
        return self._normalizer

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def poll_connectors(
        self,
        connector_names: Optional[List[str]] = None,
    ) -> List[CognitiveSignal]:
        """
        Poll one or more UCE connectors for observable data.

        If *connector_names* is None, polls all registered connectors
        that support the ``observe`` capability.
        """
        try:
            from connectors.engine import get_connection_engine
        except ImportError:
            logger.warning("UCE not available — skipping connector poll")
            return []

        signals: List[CognitiveSignal] = []
        engine = get_connection_engine()

        names = connector_names or engine.list_connectors()
        for name in names:
            try:
                connector = engine.get_connector(name)
                if connector is None:
                    continue
                # Only poll connectors that support observe
                info = connector.info()
                if "observe" not in info.get("capabilities", []):
                    continue

                raw = connector.observe({})
                if raw is None:
                    continue

                # Normalise
                normalizer = self._get_normalizer()
                event = normalizer.to_event(raw, name)

                # Convert to CognitiveSignal (abstract only)
                contract = engine.get_contract(name)
                family = contract.family.value if contract else "internal"
                source_type = _FAMILY_MAP.get(family, SignalSource.INTERNAL)

                signal = CognitiveSignal(
                    source=name,
                    source_type=source_type,
                    summary=event.description[:200] if event.description else f"event from {name}",
                    metadata={
                        "event_type": event.event_type,
                        "sensitivity": event.sensitivity.value,
                        "severity": event.severity.value,
                    },
                )
                signals.append(signal)

            except Exception as exc:
                logger.debug("Poll %s failed: %s", name, exc)
                continue

        logger.info("Signal intake: %d signals from %d connectors", len(signals), len(names))
        return signals

    def ingest_raw(
        self,
        raw_data: Dict[str, Any],
        source: str = "internal",
        source_type: SignalSource = SignalSource.INTERNAL,
    ) -> CognitiveSignal:
        """
        Create a CognitiveSignal from a raw dict (internal events).

        No raw content is preserved — only abstract summary.
        """
        summary = raw_data.get("summary", raw_data.get("description", ""))
        if not summary:
            # Create abstract summary from keys only
            summary = f"internal event with keys: {', '.join(raw_data.keys())}"

        return CognitiveSignal(
            source=source,
            source_type=source_type,
            summary=summary[:200],
            intent=raw_data.get("intent", ""),
            metadata={
                k: v for k, v in raw_data.items()
                if k in ("event_type", "severity", "category", "impact")
            },
        )
