"""
Vectrax — /anomaly
===================
Sensor de anomalía. Capa 1 del organismo.

Observa events crudos (logs, health probes, drift monitors, user reports)
y emite un objeto Anomaly canónico, o None si no hay señal.

Contrato:
    AnomalySensor().observe(event: dict) -> Optional[Anomaly]
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Anomaly:
    source: str
    signature: str                       # hash estable del patrón
    evidence: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    recurrence_count: int = 1


class AnomalySensor:
    """Capa 1 — observación."""

    def observe(self, event: Dict[str, Any]) -> Optional[Anomaly]:
        state = event.get("state")
        if state not in ("BROKEN", "DEGRADED", "ERROR", "EXCEPTION"):
            # También aceptamos eventos sin state pero con exception
            if not event.get("exception") and not event.get("drift"):
                return None

        raw_sig = (
            event.get("detector_id")
            or event.get("signature")
            or event.get("exception", "")[:128]
            or "unknown"
        )
        sig = hashlib.sha1(raw_sig.encode("utf-8", "ignore")).hexdigest()[:12]

        # Preservar señales del evento crudo para que el clasificador las lea.
        evidence = dict(event.get("evidence", {}) or {})
        for key in ("detector_id", "exception", "signature", "source"):
            if key in event and key not in evidence:
                evidence[key] = event[key]

        return Anomaly(
            source=event.get("source", "unknown"),
            signature=sig,
            evidence=evidence,
            recurrence_count=int(event.get("recurrence_count", 1)),
        )
