"""
Vectrax Core — Module Integrator
====================================
Integra módulos validados al proyecto de forma controlada.

Pipeline de integración:
  1. Validar en sandbox (SandboxValidator)
  2. Verificar que no existe conflicto (archivo ya existe)
  3. Evaluar riesgo via ApprovalGate
  4. Si aprobado → escribir archivo
  5. Registrar en nucleus (capa + módulo)
  6. Registrar en ledger

ZONA AMARILLA: Este módulo ESCRIBE archivos al disco.
Requiere autorización del creador en modo GUIDED.

Principio P-004: "Ninguna acción crítica sin autorización."
Principio P-005: "Antes de crear, revisar. Antes de integrar, validar."

Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from core.operator.builder.module_generator import GeneratedModule
from core.operator.builder.sandbox_validator import SandboxValidator, ValidationResult

logger = logging.getLogger("vectrax.operator.builder.integrator")

BASE_DIR = os.path.expanduser("~/Vectrax")


# ---------------------------------------------------------------------------
# Resultado de integración
# ---------------------------------------------------------------------------

class IntegrationStatus(str, Enum):
    SUCCESS = "success"
    VALIDATION_FAILED = "validation_failed"
    CONFLICT = "conflict"
    APPROVAL_REQUIRED = "approval_required"
    WRITE_ERROR = "write_error"


@dataclass
class IntegrationResult:
    """Resultado de un intento de integración."""
    status: IntegrationStatus
    file_path: str
    validation: Optional[ValidationResult] = None
    message: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "file_path": self.file_path,
            "validation_passed": self.validation.passed if self.validation else None,
            "message": self.message,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Module Integrator
# ---------------------------------------------------------------------------

class ModuleIntegrator:
    """
    Integra módulos generados al proyecto tras validación.

    Usage::

        integrator = ModuleIntegrator()

        # Solo validar (dry run)
        result = integrator.validate_only(generated_module)

        # Integrar (escribir a disco) — requiere approve=True
        result = integrator.integrate(generated_module, approve=True)
    """

    def __init__(
        self,
        base_dir: str = BASE_DIR,
        validator: Optional[SandboxValidator] = None,
    ) -> None:
        self._base_dir = base_dir
        self._validator = validator or SandboxValidator(base_dir)

    # ------------------------------------------------------------------
    # Validación
    # ------------------------------------------------------------------

    def validate_only(self, module: GeneratedModule) -> ValidationResult:
        """Ejecuta validación sin escribir nada."""
        return self._validator.validate(module.source_code, module.file_path)

    # ------------------------------------------------------------------
    # Integración
    # ------------------------------------------------------------------

    def integrate(
        self,
        module: GeneratedModule,
        *,
        approve: bool = False,
        overwrite: bool = False,
        layer_id: int = 0,
    ) -> IntegrationResult:
        """
        Integra un módulo generado al proyecto.

        Args:
            module: Módulo generado por ModuleGenerator.
            approve: Debe ser True para autorizar la escritura.
            overwrite: Si True, permite sobrescribir archivos existentes.
            layer_id: Capa del operador donde registrar el módulo.

        Returns:
            IntegrationResult con el estado de la integración.
        """
        abs_path = os.path.join(self._base_dir, module.file_path)

        # 1. Validar
        validation = self._validator.validate(module.source_code, module.file_path)
        if not validation.passed:
            return IntegrationResult(
                status=IntegrationStatus.VALIDATION_FAILED,
                file_path=module.file_path,
                validation=validation,
                message=f"Validation failed: {len(validation.failures)} issues",
            )

        # 2. Verificar conflicto
        if os.path.exists(abs_path) and not overwrite:
            return IntegrationResult(
                status=IntegrationStatus.CONFLICT,
                file_path=module.file_path,
                validation=validation,
                message=f"File already exists: {module.file_path}",
            )

        # 3. Verificar aprobación
        if not approve:
            return IntegrationResult(
                status=IntegrationStatus.APPROVAL_REQUIRED,
                file_path=module.file_path,
                validation=validation,
                message="Integration requires explicit approval (approve=True)",
            )

        # 4. Escribir
        try:
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(module.source_code)
        except OSError as exc:
            return IntegrationResult(
                status=IntegrationStatus.WRITE_ERROR,
                file_path=module.file_path,
                validation=validation,
                message=f"Write error: {exc}",
            )

        # 5. Registrar en nucleus
        if layer_id > 0:
            try:
                from core.operator.nucleus import get_nucleus
                nucleus = get_nucleus()
                module_path = module.file_path.replace("/", ".").replace(".py", "")
                nucleus.register_module(layer_id, module_path)
            except Exception as exc:
                logger.debug("Nucleus registration failed: %s", exc)

        # 6. Registrar en ledger
        try:
            from core.operator.ledger_bridge import record_module_created
            record_module_created(
                module_path=module.file_path,
                layer_id=layer_id or module.spec.layer_id,
                purpose=module.spec.purpose,
            )
        except Exception as exc:
            logger.debug("Ledger registration failed: %s", exc)

        logger.info("Module integrated: %s", module.file_path)

        return IntegrationResult(
            status=IntegrationStatus.SUCCESS,
            file_path=module.file_path,
            validation=validation,
            message=f"Successfully integrated: {module.file_path}",
        )

    # ------------------------------------------------------------------
    # Batch
    # ------------------------------------------------------------------

    def integrate_batch(
        self,
        modules: List[GeneratedModule],
        *,
        approve: bool = False,
        layer_id: int = 0,
    ) -> List[IntegrationResult]:
        """Integra múltiples módulos. Se detiene en el primer fallo."""
        results: List[IntegrationResult] = []
        for mod in modules:
            result = self.integrate(mod, approve=approve, layer_id=layer_id)
            results.append(result)
            if result.status != IntegrationStatus.SUCCESS:
                break
        return results
