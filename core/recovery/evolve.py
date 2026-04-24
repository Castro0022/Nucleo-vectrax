"""
Vectrax — evolve_from_error
============================
Director del flujo auto-metabolizante. Implementa exactamente el contrato
que Mario Bravo Castro dictó el 2026-04-23:

    anomaly = anomaly_sensor.observe(event)
    signature = anomaly_classifier.infer(anomaly)

    if memory.has_motor_for(signature):
        return memory.route_to_existing_motor(signature)

    spec = motor_factory.synthesize_spec(
        anomaly=anomaly,
        signature=signature,
        historical_context=memory.get_related(anomaly),
        constitutional_rules=constitution.active_rules(),
    )
    candidate = motor_factory.generate_code(spec)
    report = validator.run(candidate, against=anomaly)

    if report.success:
        integrator.install(candidate)
        evolution_ledger.record(anomaly, signature, candidate)
        memory.bind_motor(signature, candidate)
        return "motor_installed"
    else:
        return "needs_deeper_synthesis"

Ley madre:
    Toda anomalía observada debe transformarse en una nueva capacidad
    permanente del sistema.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from core.recovery import constitution
from core.recovery import evolution_ledger
from core.recovery import evolutive_memory as memory
from core.recovery.anomaly import AnomalySensor
from core.recovery.classifier import AnomalyClassifier
from core.recovery.integrator import Integrator
from core.recovery.motor_factory import generate_code, synthesize_spec
from core.recovery.validator import Validator

logger = logging.getLogger("vectrax.recovery.evolve")

_sensor = AnomalySensor()
_classifier = AnomalyClassifier()
_validator = Validator()
_integrator = Integrator()


def evolve_from_error(event: Dict[str, Any]) -> str:
    """Punto único de entrada. Devuelve:
        - "not_anomaly"            : el event no era anomalía
        - "routed_existing:<id>"   : memoria ya tiene órgano
        - "motor_installed"        : nueva capacidad integrada
        - "needs_deeper_synthesis" : rechazado por el validador
    """
    anomaly = _sensor.observe(event)
    if anomaly is None:
        return "not_anomaly"

    signature = _classifier.infer(anomaly)
    memory.record_wound(anomaly, signature)

    if memory.has_motor_for(signature):
        motor_id = memory.route_to_existing_motor(signature) or "unknown"
        # Ley 2: si ya hay órgano y la herida reaparece, el órgano es insuficiente.
        # Queda registrado para expansión futura, pero no se fabrica duplicado.
        if anomaly.recurrence_count > 1:
            evolution_ledger.record_escalation(
                anomaly, signature,
                failures=[f"órgano existente insuficiente: {motor_id}"],
            )
        return f"routed_existing:{motor_id}"

    spec = synthesize_spec(
        anomaly=anomaly,
        signature=signature,
        historical_context=memory.get_related(anomaly),
        constitutional_rules=constitution.active_rules(),
    )
    candidate = generate_code(spec)
    report = _validator.run(candidate, against=anomaly)

    if report.success:
        _integrator.install(candidate)
        evolution_ledger.record(anomaly, signature, candidate)
        memory.bind_motor(signature, candidate)
        evolution_ledger.record_class_absorbed(signature)
        logger.info(
            "evolve: clase absorbida species=%s mechanism=%s",
            signature.species, signature.mechanism,
        )
        return "motor_installed"

    evolution_ledger.record_escalation(anomaly, signature, report.failures)
    return "needs_deeper_synthesis"
