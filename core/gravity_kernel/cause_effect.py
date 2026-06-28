"""
Gravity Kernel — Causa/Efecto (cause_effect_score)
====================================================
"¿Qué efecto probable produce esta decisión si la ejecuto ahora?"

Balancea probabilidad de acierto + consecuencia esperada + riesgo de daño +
reversibilidad. Tiene precedente directo en core/learn/gravity_engine.py
(win_rate, expectancy, outcome_history).

Corrección vs el documento original: win_rate e historical_success_rate se
unifican en UNA sola señal de éxito (``ctx.win_rate``) para evitar el doble
conteo de la misma evidencia. Todos los pesos viven en el YAML.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from core.gravity_kernel.loader import get_config
from core.gravity_kernel.signals import CauseEffectCtx


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def calculate_cause_effect_score(
    ctx: CauseEffectCtx, cfg: Optional[Dict[str, Any]] = None
) -> float:
    """Devuelve cause_effect_score en [0.0, 1.0]."""
    cfg = cfg or get_config()
    w = cfg["cause_effect_weights"]

    score = w["base"]
    # Señal de éxito UNIFICADA (no se suma win_rate + histórico por separado).
    score += (ctx.win_rate - 0.50) * w["win_rate_coeff"]
    score += ctx.expectancy * w["expectancy_coeff"]
    score += (ctx.confidence_score - 0.50) * w["confidence_coeff"]
    score -= ctx.cost_of_error * w["cost_of_error_coeff"]
    score -= ctx.domain_risk_level * w["domain_risk_coeff"]
    score += ctx.reversibility_score * w["reversibility_coeff"]
    score -= min(
        ctx.user_correction_history * w["correction_history_unit"],
        w["correction_history_cap"],
    )
    return _clamp(score)


def decide(score: float, cfg: Optional[Dict[str, Any]] = None) -> str:
    """Mapea el score a una acción de banda (límite inferior inclusivo)."""
    cfg = cfg or get_config()
    b = cfg["cause_effect_bands"]
    if score >= b["execute"]:
        return "execute"
    if score >= b["execute_with_audit"]:
        return "execute_with_audit"
    if score >= b["simulate_or_ask_confirmation"]:
        return "simulate_or_ask_confirmation"
    if score >= b["hold_and_collect_more_evidence"]:
        return "hold_and_collect_more_evidence"
    return "block"
