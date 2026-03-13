"""Vectrax - Paso 2: Detección de convergencia real."""

from __future__ import annotations

from models.schemas import Signal, SignalCategory, ConvergenceResult
from utils.scoring import clamp
import config


def detect_convergence(signals: list[Signal]) -> ConvergenceResult:
    """Detecta si las señales convergen hacia una dirección común.

    La convergencia se mide analizando:
    - Concentración de señales en categorías similares
    - Peso acumulado de las señales dominantes
    - Número mínimo de señales requeridas
    """
    if len(signals) < config.MIN_SIGNALS_FOR_CONVERGENCE:
        return ConvergenceResult(
            converged=False,
            score=0.0,
            cluster_count=0,
            dominant_category=None,
            signal_count=len(signals),
            details=f"Insuficientes señales ({len(signals)}/{config.MIN_SIGNALS_FOR_CONVERGENCE})",
        )

    # Agrupar por categoría
    clusters: dict[SignalCategory, list[Signal]] = {}
    for s in signals:
        clusters.setdefault(s.category, []).append(s)

    cluster_count = len(clusters)

    # Encontrar categoría dominante (mayor peso acumulado)
    category_weights: dict[SignalCategory, float] = {}
    for cat, sigs in clusters.items():
        category_weights[cat] = sum(s.weight for s in sigs)

    total_weight = sum(category_weights.values())
    dominant_category = max(category_weights, key=category_weights.get)  # type: ignore[arg-type]
    dominant_weight = category_weights[dominant_category]

    # Score de convergencia: proporción del peso dominante vs total
    convergence_score = clamp(dominant_weight / total_weight) if total_weight > 0 else 0.0

    # Bonus por múltiples señales en la misma dirección
    dominant_count = len(clusters[dominant_category])
    density_bonus = clamp(dominant_count / len(signals)) * 0.2
    convergence_score = clamp(convergence_score + density_bonus)

    converged = convergence_score >= config.CONVERGENCE_THRESHOLD

    return ConvergenceResult(
        converged=converged,
        score=round(convergence_score, 4),
        cluster_count=cluster_count,
        dominant_category=dominant_category,
        signal_count=len(signals),
        details=(
            f"Convergencia {'detectada' if converged else 'no detectada'}. "
            f"Categoría dominante: {dominant_category.value} "
            f"({dominant_count}/{len(signals)} señales, "
            f"peso: {dominant_weight:.1f}/{total_weight:.1f})"
        ),
    )
