"""
Vectrax — /classifier
======================
Clasificador de clase de error. Capa 2.

Dada una Anomaly, infiere una ErrorSignature: identidad estable de la
ESPECIE del fallo (no del incidente puntual).

Contrato:
    AnomalyClassifier().infer(anomaly: Anomaly) -> ErrorSignature
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from core.recovery.anomaly import Anomaly


@dataclass(frozen=True)
class ErrorSignature:
    species: str       # identity | routing | memory | config | integration |
                       # concurrency | state | interface | drift | unknown
    mechanism: str     # duplication | race | missing_guard | drift |
                       # untyped_interface | timeout | ...
    severity: str      # low | medium | high | critical


# Tabla de especies conocidas — cada entrada refleja un órgano existente.
# Se consulta por evolutive_memory.has_motor_for().
_KNOWN_SPECIES: Dict[str, ErrorSignature] = {
    "gateway_silent": ErrorSignature(
        species="routing", mechanism="missing_guard", severity="critical"),
    "hardcoded_handler": ErrorSignature(
        species="interface", mechanism="duplication", severity="medium"),
    "gateway_stuck_d_state": ErrorSignature(
        species="concurrency", mechanism="race", severity="critical"),
    "db_lock": ErrorSignature(
        species="concurrency", mechanism="race", severity="high"),
    "tls_expired": ErrorSignature(
        species="integration", mechanism="drift", severity="critical"),
    "config_drift": ErrorSignature(
        species="config", mechanism="drift", severity="high"),
}


class AnomalyClassifier:
    """Capa 2 — clasificación."""

    def infer(self, anomaly: Anomaly) -> ErrorSignature:
        # Heurística simple; crecerá con memoria evolutiva.
        ev = anomaly.evidence or {}
        hint = (
            str(ev.get("detector_id", ""))
            + " "
            + str(ev.get("exception", ""))
            + " "
            + anomaly.source
        ).lower()

        for key, sig in _KNOWN_SPECIES.items():
            if key in hint:
                return sig

        # Especie nueva — marca nombre genérico y deja que el forge sintetice.
        if "duplicate" in hint or "hardcoded" in hint:
            return ErrorSignature("interface", "duplication", "medium")
        if "race" in hint or "deadlock" in hint or "lock" in hint:
            return ErrorSignature("concurrency", "race", "high")
        if "timeout" in hint:
            return ErrorSignature("integration", "timeout", "medium")

        return ErrorSignature("unknown", "unknown", "medium")
