"""
Vectrax — /evolution_ledger
=============================
Memoria evolutiva. Registro inmutable de:
  - heridas técnicas (anomalías observadas)
  - órganos nacidos de cada herida (motores instalados)
  - especies de fallo absorbidas (clases cerradas para siempre)

Diferente del ledger operativo (core/recovery/ledger.py) que guarda
eventos en tiempo real. Este solo registra transformaciones evolutivas:
cada línea es un paso en la historia del organismo.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List

from core.recovery.anomaly import Anomaly
from core.recovery.classifier import ErrorSignature
from core.recovery.motor_factory import CandidateMotor

logger = logging.getLogger("vectrax.recovery.evolution_ledger")

_LEDGER_PATH = os.environ.get(
    "VECTRAX_EVOLUTION_LEDGER",
    "/var/log/vectrax/evolution_ledger.jsonl",
)


def _write(kind: str, payload: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(_LEDGER_PATH), exist_ok=True)
        entry = {"ts": time.time(), "kind": kind, **payload}
        with open(_LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug("evolution_ledger write failed: %s", exc)


def record(
    anomaly: Anomaly,
    signature: ErrorSignature,
    candidate: CandidateMotor,
) -> None:
    """Cada herida queda inscrita junto al órgano que nació de ella."""
    _write("wound_to_organ", {
        "anomaly_signature": anomaly.signature,
        "species": signature.species,
        "mechanism": signature.mechanism,
        "severity": signature.severity,
        "motor_mechanisms": candidate.spec.mechanisms,
        "files_created": candidate.spec.files_to_create,
        "rationale": candidate.spec.rationale,
        "recurrence_before_absorption": anomaly.recurrence_count,
    })


def record_installation(
    candidate: CandidateMotor, files_written: List[str],
) -> None:
    _write("motor_installed", {
        "error_class": candidate.spec.error_class,
        "mechanisms": candidate.spec.mechanisms,
        "files_written": files_written,
    })


def record_class_absorbed(signature: ErrorSignature) -> None:
    _write("class_absorbed", {
        "species": signature.species,
        "mechanism": signature.mechanism,
    })


def record_escalation(
    anomaly: Anomaly, signature: ErrorSignature, failures: List[str],
) -> None:
    _write("escalation", {
        "anomaly_signature": anomaly.signature,
        "species": signature.species,
        "mechanism": signature.mechanism,
        "failures": failures,
    })


def tail(n: int = 20) -> List[Dict[str, Any]]:
    """Lee las últimas n entradas — para dashboard de evolución."""
    if not os.path.exists(_LEDGER_PATH):
        return []
    try:
        with open(_LEDGER_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        return [json.loads(l) for l in lines if l.strip()]
    except Exception as exc:
        logger.debug("evolution_ledger tail failed: %s", exc)
        return []
