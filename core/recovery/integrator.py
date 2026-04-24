"""
Vectrax — /integrator
======================
Capa 5. Instala el motor candidato como órgano permanente del núcleo.

install(candidate) — escribe artifacts, registra en ledger, activa guardianes.
"""
from __future__ import annotations

import logging
import os
from typing import List

from core.recovery.motor_factory import CandidateMotor
from core.recovery import evolution_ledger

logger = logging.getLogger("vectrax.recovery.integrator")


class Integrator:

    def install(self, candidate: CandidateMotor) -> List[str]:
        """
        Materializa los artifacts en el repo. Devuelve lista de paths escritos.
        Toda escritura es append-friendly: si ya existe un archivo con ese
        path, se marca para revisión humana en lugar de sobrescribir.
        """
        written: List[str] = []
        for rel_path, content in candidate.artifacts.items():
            abs_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__)))),
                rel_path,
            )
            if os.path.exists(abs_path):
                logger.warning(
                    "Integrator: ya existe %s — se deja para revisión humana",
                    rel_path,
                )
                continue
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
            if rel_path.endswith(".sh"):
                os.chmod(abs_path, 0o755)
            written.append(rel_path)
            logger.info("Integrator: artifact instalado → %s", rel_path)

        evolution_ledger.record_installation(candidate, written)
        return written
