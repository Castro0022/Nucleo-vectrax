"""Vectrax - Paso 5: Simulación de impacto."""

from __future__ import annotations

from models.schemas import (
    StructuralAnalysis,
    ConvergenceResult,
    ScenarioResult,
    SimulationResult,
)
from utils.scoring import clamp, weighted_average
import config


def simulate_impact(
    analysis: StructuralAnalysis,
    convergence: ConvergenceResult,
) -> SimulationResult:
    """Simula el impacto en tres escenarios: optimista, base y pesimista.

    El impacto se estima a partir del score estructural y la convergencia.
    """
    base_score = analysis.overall_score
    convergence_factor = convergence.score if convergence.converged else convergence.score * 0.5

    # Escenario optimista: todo sale bien
    optimistic = ScenarioResult(
        name="optimista",
        probability=0.25,
        impact_score=clamp(base_score * 1.3 + convergence_factor * 0.2, -1.0, 1.0),
        description="Adopción favorable, sin obstáculos significativos.",
    )

    # Escenario base: resultado esperado
    base = ScenarioResult(
        name="base",
        probability=0.50,
        impact_score=clamp(base_score * 0.9 + convergence_factor * 0.1, -1.0, 1.0),
        description="Implementación estándar con desafíos normales.",
    )

    # Escenario pesimista: condiciones adversas
    pessimistic = ScenarioResult(
        name="pesimista",
        probability=0.25,
        impact_score=clamp(base_score * 0.4 - (1 - convergence_factor) * 0.3, -1.0, 1.0),
        description="Resistencia alta, condiciones desfavorables.",
    )

    scenarios = [optimistic, base, pessimistic]

    # Impacto esperado ponderado
    expected = weighted_average(
        values=[s.impact_score for s in scenarios],
        weights=[config.SCENARIO_WEIGHTS[s.name] for s in scenarios],
    )

    # Confianza basada en cantidad de señales y convergencia
    confidence = clamp(
        convergence.score * 0.6 + min(convergence.signal_count / 20, 1.0) * 0.4
    )

    return SimulationResult(
        scenarios=scenarios,
        expected_impact=round(expected, 4),
        confidence=round(confidence, 4),
    )
