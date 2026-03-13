"""Vectrax - Paso 4: Validación contra la Constitución Inmutable."""

from __future__ import annotations

from models.schemas import (
    StructuralAnalysis,
    RiskAssessment,
    SustainabilityScore,
    ValidationResult,
    ValidationViolation,
    RiskLevel,
)
from constitution import IMMUTABLE_PRINCIPLES


def validate_against_constitution(
    analysis: StructuralAnalysis,
    risk: RiskAssessment,
    sustainability: SustainabilityScore,
) -> ValidationResult:
    """Valida los resultados del análisis contra la Constitución Inmutable.

    Cada principio constitucional tiene una función de verificación.
    Si algún principio es violado, la propuesta se marca como inválida.
    """
    violations: list[ValidationViolation] = []
    checked = 0

    for principle in IMMUTABLE_PRINCIPLES:
        checked += 1
        is_ok, message = principle.check(analysis, risk, sustainability)

        if not is_ok:
            # Determinar severidad basada en el principio
            severity = _determine_severity(principle.id)
            violations.append(ValidationViolation(
                principle=f"[{principle.id}] {principle.name}",
                description=message,
                severity=severity,
            ))

    is_valid = len(violations) == 0

    details_parts = [f"Principios verificados: {checked}/{len(IMMUTABLE_PRINCIPLES)}"]
    if violations:
        details_parts.append(f"Violaciones encontradas: {len(violations)}")
        for v in violations:
            details_parts.append(f"  - {v.principle}: {v.description} [{v.severity.value}]")
    else:
        details_parts.append("Sin violaciones constitucionales.")

    return ValidationResult(
        is_valid=is_valid,
        violations=violations,
        checked_principles=checked,
        details="\n".join(details_parts),
    )


def _determine_severity(principle_id: str) -> RiskLevel:
    """Determina la severidad de una violación según el principio."""
    severity_map = {
        "CF-001": RiskLevel.CRITICO,   # Derechos fundamentales
        "CF-002": RiskLevel.ALTO,      # Transparencia
        "CF-003": RiskLevel.ALTO,      # Sostenibilidad
        "CF-004": RiskLevel.CRITICO,   # Riesgo sistémico
    }
    return severity_map.get(principle_id, RiskLevel.MODERADO)
