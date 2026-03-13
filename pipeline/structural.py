"""Vectrax - Paso 3: Análisis estructural completo."""

from __future__ import annotations

from models.schemas import (
    Signal,
    SignalCategory,
    ConvergenceResult,
    DimensionScore,
    StructuralAnalysis,
)
from utils.scoring import clamp, weighted_average
import config


# Mapeo de categoría de señal a dimensión estructural
_CATEGORY_TO_DIMENSION: dict[SignalCategory, str] = {
    SignalCategory.ECONOMICA: "economica",
    SignalCategory.SOCIAL: "social",
    SignalCategory.LEGAL: "legal",
    SignalCategory.TECNICA: "tecnica",
    SignalCategory.AMBIENTAL: "ambiental",
}


def analyze_structure(
    signals: list[Signal],
    convergence: ConvergenceResult,
) -> StructuralAnalysis:
    """Ejecuta análisis estructural en 5 dimensiones.

    Cada dimensión recibe una puntuación basada en:
    - Cantidad de señales relacionadas
    - Peso promedio de esas señales
    - Bonus si la dimensión coincide con la categoría dominante
    """
    # Agrupar señales por dimensión
    dim_signals: dict[str, list[Signal]] = {d: [] for d in config.STRUCTURAL_DIMENSIONS}
    for s in signals:
        dim_name = _CATEGORY_TO_DIMENSION.get(s.category)
        if dim_name and dim_name in dim_signals:
            dim_signals[dim_name].append(s)

    total_signals = max(len(signals), 1)
    dimensions: list[DimensionScore] = []

    for dim_name in config.STRUCTURAL_DIMENSIONS:
        sigs = dim_signals[dim_name]
        if not sigs:
            # Dimensión sin señales: puntuación base neutra
            dimensions.append(DimensionScore(
                name=dim_name,
                score=0.5,
                notes="Sin señales directas; puntuación neutra.",
            ))
            continue

        # Proporción de señales en esta dimensión
        coverage = len(sigs) / total_signals
        # Peso promedio normalizado (max peso = 10)
        avg_weight = sum(s.weight for s in sigs) / len(sigs)
        weight_factor = clamp(avg_weight / 10.0)

        score = clamp(coverage * 0.5 + weight_factor * 0.5)

        # Bonus si es la categoría dominante
        if convergence.dominant_category:
            dominant_dim = _CATEGORY_TO_DIMENSION.get(convergence.dominant_category)
            if dominant_dim == dim_name:
                score = clamp(score + 0.15)

        dimensions.append(DimensionScore(
            name=dim_name,
            score=round(score, 4),
            notes=f"{len(sigs)} señales, peso promedio: {avg_weight:.1f}",
        ))

    # Score global: promedio de dimensiones
    overall = round(
        sum(d.score for d in dimensions) / max(len(dimensions), 1), 4
    )

    weak = [d.name for d in dimensions if d.score < config.MIN_STRUCTURAL_SCORE]
    summary = "Análisis estructural completo."
    if weak:
        summary += f" Dimensiones débiles: {', '.join(weak)}."

    return StructuralAnalysis(
        dimensions=dimensions,
        overall_score=overall,
        summary=summary,
    )
