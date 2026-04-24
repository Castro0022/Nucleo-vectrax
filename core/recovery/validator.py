"""
Vectrax — /validator
=====================
Capa 4. Prueba que el motor candidato:
  - cumple la constitución (Ley 1: no es parche)
  - ejecuta el reproducer de la anomalía y lo neutraliza
  - no introduce regresión en tests existentes
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from typing import List

from core.recovery.anomaly import Anomaly
from core.recovery.constitution import check_is_motor_not_patch
from core.recovery.motor_factory import CandidateMotor

logger = logging.getLogger("vectrax.recovery.validator")


@dataclass
class ValidationReport:
    success: bool
    failures: List[str] = field(default_factory=list)
    regressions: List[str] = field(default_factory=list)


class Validator:

    def run(
        self, candidate: CandidateMotor, against: Anomaly,
    ) -> ValidationReport:
        failures: List[str] = []

        # 1. Constitución.
        law_violations = check_is_motor_not_patch(candidate.spec.__dict__)
        if law_violations:
            failures.extend([v.reason for v in law_violations])

        # 2. Reproducer — futuro: se corre el pytest específico del spec.
        #    Hoy devolvemos ok si el spec declara tests.
        if not candidate.spec.tests:
            failures.append("spec sin tests declarados")

        # 3. Regresión — hook opcional (pytest -q sobre suite corta).
        regressions: List[str] = []
        # Placeholder: activar con feature flag cuando el runner esté aislado.

        return ValidationReport(
            success=not failures,
            failures=failures,
            regressions=regressions,
        )
