"""Vectrax - Paso 6: Evaluación de riesgo sistémico."""

from __future__ import annotations

from models.schemas import (
    StructuralAnalysis,
    ConvergenceResult,
    SimulationResult,
    RiskAssessment,
    RiskLevel,
)
from utils.scoring import clamp, risk_score
import config


def evaluate_risk(
    analysis: StructuralAnalysis,
    convergence: ConvergenceResult,
    simulation: SimulationResult,
) -> RiskAssessment:
    """Evalúa el riesgo sistémico de la propuesta.

    Riesgo = Probabilidad de fallo × Impacto del fallo.
    """
    # Probabilidad de fallo: inversa del score estructural y convergencia
    structural_risk = 1.0 - analysis.overall_score
    convergence_risk = 1.0 - convergence.score if convergence.converged else 0.8

    probability = clamp(structural_risk * 0.6 + convergence_risk * 0.4)

    # Impacto: basado en escenario pesimista y dimensiones débiles
    pessimistic = next(
        (s for s in simulation.scenarios if s.name == "pesimista"), None
    )
    pessimistic_impact = abs(pessimistic.impact_score) if pessimistic else 0.5

    weak_dimensions = sum(
        1 for d in analysis.dimensions if d.score < config.MIN_STRUCTURAL_SCORE
    )
    dimension_penalty = weak_dimensions / max(len(analysis.dimensions), 1)

    impact = clamp(pessimistic_impact * 0.7 + dimension_penalty * 0.3)

    # Score de riesgo
    score = risk_score(probability, impact)

    # Clasificar nivel de riesgo
    risk_level = _classify_risk(score)

    # Generar mitigaciones
    mitigations = _suggest_mitigations(analysis, convergence, score)

    return RiskAssessment(
        risk_level=risk_level,
        probability=round(probability, 4),
        impact=round(impact, 4),
        risk_score=round(score, 4),
        mitigations=mitigations,
        details=(
            f"Riesgo: {risk_level.value} (score: {score:.4f}). "
            f"P(fallo)={probability:.2f}, Impacto={impact:.2f}. "
            f"Dimensiones débiles: {weak_dimensions}/{len(analysis.dimensions)}."
        ),
    )


def _classify_risk(score: float) -> RiskLevel:
    """Clasifica el nivel de riesgo según el score."""
    if score >= 0.75:
        return RiskLevel.CRITICO
    elif score >= 0.50:
        return RiskLevel.ALTO
    elif score >= 0.25:
        return RiskLevel.MODERADO
    return RiskLevel.BAJO


def _suggest_mitigations(
    analysis: StructuralAnalysis,
    convergence: ConvergenceResult,
    score: float,
) -> list[str]:
    """Sugiere mitigaciones basadas en el análisis."""
    mitigations: list[str] = []

    weak = [d for d in analysis.dimensions if d.score < config.MIN_STRUCTURAL_SCORE]
    for d in weak:
        mitigations.append(f"Reforzar dimensión '{d.name}' (score actual: {d.score:.2f})")

    if not convergence.converged:
        mitigations.append("Recopilar más señales para alcanzar convergencia")

    if score > config.MAX_ACCEPTABLE_RISK:
        mitigations.append("Riesgo excede umbral aceptable: considerar rediseño de la propuesta")

    if not mitigations:
        mitigations.append("Sin mitigaciones críticas necesarias")

    return mitigations
