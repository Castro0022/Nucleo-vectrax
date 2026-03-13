"""Vectrax - Paso 8: Generación de Propuesta Ejecutiva."""

from __future__ import annotations

import uuid

from models.schemas import (
    ConvergenceResult,
    StructuralAnalysis,
    ValidationResult,
    SimulationResult,
    RiskAssessment,
    SustainabilityScore,
    ExecutiveProposal,
    ProposalStatus,
)
from utils.scoring import weighted_average
import config


def generate_proposal(
    title: str,
    convergence: ConvergenceResult,
    analysis: StructuralAnalysis,
    validation: ValidationResult,
    simulation: SimulationResult,
    risk: RiskAssessment,
    sustainability: SustainabilityScore,
) -> ExecutiveProposal:
    """Genera la Propuesta Ejecutiva consolidada con todos los resultados.

    Determina el estatus de la propuesta basándose en:
    - Validación constitucional (veto absoluto si hay violaciones críticas)
    - Score global ponderado de todos los pasos
    - Umbrales de aprobación/rechazo automático
    """
    # Si hay violaciones constitucionales críticas: rechazo inmediato
    if not validation.is_valid:
        critical = any(
            v.severity.value == "critico" for v in validation.violations
        )
        if critical:
            return ExecutiveProposal(
                id=_generate_id(),
                title=title,
                status=ProposalStatus.RECHAZADA,
                convergence=convergence,
                structural_analysis=analysis,
                validation=validation,
                simulation=simulation,
                risk=risk,
                sustainability=sustainability,
                recommendation=(
                    "RECHAZADA: Violaciones críticas contra la Constitución Inmutable. "
                    "La propuesta no puede proceder en su forma actual."
                ),
            )

    # Calcular score global
    global_score = weighted_average(
        values=[
            convergence.score,
            analysis.overall_score,
            1.0 if validation.is_valid else 0.0,
            max(simulation.expected_impact, 0.0),
            1.0 - risk.risk_score,
            sustainability.overall,
        ],
        weights=[0.15, 0.20, 0.25, 0.10, 0.15, 0.15],
    )

    # Determinar estatus
    if global_score >= config.AUTO_APPROVE_THRESHOLD:
        status = ProposalStatus.APROBADA
        recommendation = (
            f"APROBADA: Score global {global_score:.4f} supera umbral de aprobación "
            f"({config.AUTO_APPROVE_THRESHOLD}). Proceder con implementación."
        )
    elif global_score <= config.AUTO_REJECT_THRESHOLD:
        status = ProposalStatus.RECHAZADA
        recommendation = (
            f"RECHAZADA: Score global {global_score:.4f} por debajo del umbral mínimo "
            f"({config.AUTO_REJECT_THRESHOLD}). Rediseñar la propuesta."
        )
    else:
        status = ProposalStatus.REQUIERE_REVISION
        recommendation = (
            f"REQUIERE REVISIÓN: Score global {global_score:.4f} en zona intermedia "
            f"({config.AUTO_REJECT_THRESHOLD} - {config.AUTO_APPROVE_THRESHOLD}). "
            "Se recomienda revisión humana antes de proceder."
        )
        # Agregar sugerencias específicas
        if not convergence.converged:
            recommendation += "\n  → Aumentar convergencia de señales."
        if risk.risk_score > config.MAX_ACCEPTABLE_RISK:
            recommendation += "\n  → Reducir riesgo sistémico."
        if not sustainability.is_sustainable:
            recommendation += "\n  → Mejorar sostenibilidad a largo plazo."

    return ExecutiveProposal(
        id=_generate_id(),
        title=title,
        status=status,
        convergence=convergence,
        structural_analysis=analysis,
        validation=validation,
        simulation=simulation,
        risk=risk,
        sustainability=sustainability,
        recommendation=recommendation,
    )


def _generate_id() -> str:
    """Genera un ID único para la propuesta."""
    return f"VTX-{uuid.uuid4().hex[:8].upper()}"
