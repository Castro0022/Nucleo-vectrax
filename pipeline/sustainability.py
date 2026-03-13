"""Vectrax - Paso 7: Cálculo de sostenibilidad a largo plazo."""

from __future__ import annotations

from models.schemas import (
    StructuralAnalysis,
    ConvergenceResult,
    SimulationResult,
    RiskAssessment,
    SustainabilityScore,
)
from utils.scoring import clamp, weighted_average
import config


def calculate_sustainability(
    analysis: StructuralAnalysis,
    convergence: ConvergenceResult,
    simulation: SimulationResult,
    risk: RiskAssessment,
) -> SustainabilityScore:
    """Calcula la sostenibilidad a corto, mediano y largo plazo.

    Factores considerados:
    - Corto plazo: viabilidad técnica y económica inmediata
    - Mediano plazo: resiliencia social y legal
    - Largo plazo: sostenibilidad ambiental y riesgo sistémico
    """
    # Extraer scores por dimensión
    dim_scores = {d.name: d.score for d in analysis.dimensions}

    # --- Corto plazo (0-12 meses) ---
    tech_score = dim_scores.get("tecnica", 0.5)
    econ_score = dim_scores.get("economica", 0.5)
    short_term = clamp(tech_score * 0.5 + econ_score * 0.3 + convergence.score * 0.2)

    # --- Mediano plazo (1-3 años) ---
    social_score = dim_scores.get("social", 0.5)
    legal_score = dim_scores.get("legal", 0.5)
    medium_term = clamp(
        social_score * 0.35
        + legal_score * 0.35
        + simulation.expected_impact * 0.3
    )

    # --- Largo plazo (3+ años) ---
    env_score = dim_scores.get("ambiental", 0.5)
    risk_factor = 1.0 - risk.risk_score  # Menor riesgo = mayor sostenibilidad
    long_term = clamp(
        env_score * 0.3
        + risk_factor * 0.4
        + analysis.overall_score * 0.3
    )

    # Score global ponderado
    overall = weighted_average(
        values=[short_term, medium_term, long_term],
        weights=[
            config.SUSTAINABILITY_WEIGHTS["short_term"],
            config.SUSTAINABILITY_WEIGHTS["medium_term"],
            config.SUSTAINABILITY_WEIGHTS["long_term"],
        ],
    )

    is_sustainable = overall >= config.MIN_SUSTAINABILITY_SCORE

    return SustainabilityScore(
        short_term=round(short_term, 4),
        medium_term=round(medium_term, 4),
        long_term=round(long_term, 4),
        overall=round(overall, 4),
        is_sustainable=is_sustainable,
        details=(
            f"Sostenibilidad: {'viable' if is_sustainable else 'no viable'} "
            f"(score: {overall:.4f}). "
            f"CP={short_term:.2f}, MP={medium_term:.2f}, LP={long_term:.2f}."
        ),
    )
