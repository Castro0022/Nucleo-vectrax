"""Vectrax - Funciones auxiliares de puntuación."""

from __future__ import annotations


def clamp(value: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    """Restringe un valor dentro de un rango."""
    return max(min_val, min(value, max_val))


def weighted_average(values: list[float], weights: list[float]) -> float:
    """Calcula promedio ponderado."""
    if not values or not weights or len(values) != len(weights):
        return 0.0
    total_weight = sum(weights)
    if total_weight == 0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights)) / total_weight


def normalize(value: float, min_val: float, max_val: float) -> float:
    """Normaliza un valor al rango [0, 1]."""
    if max_val == min_val:
        return 0.0
    return clamp((value - min_val) / (max_val - min_val))


def risk_score(probability: float, impact: float) -> float:
    """Calcula score de riesgo = probabilidad × impacto."""
    return clamp(probability) * clamp(impact)


def category_frequency(items: list[str]) -> dict[str, int]:
    """Cuenta frecuencia de cada categoría."""
    freq: dict[str, int] = {}
    for item in items:
        freq[item] = freq.get(item, 0) + 1
    return freq
