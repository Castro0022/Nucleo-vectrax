"""
Gravity Kernel — Ritmo (rhythm_score)
=======================================
"¿El sistema debe avanzar, responder corto, pausar o pedir calibración?"

Evalúa el ESTADO DEL FLUJO, no si una idea es buena. Penaliza distorsión
(correcciones, frustración, misread), no intensidad ni longitud. Todos los
pesos y umbrales viven en config/gravity_kernel.yaml.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from core.gravity_kernel.loader import get_config
from core.gravity_kernel.signals import RhythmCtx


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def calculate_rhythm_score(ctx: RhythmCtx, cfg: Optional[Dict[str, Any]] = None) -> float:
    """Devuelve rhythm_score en [0.0, 1.0]."""
    cfg = cfg or get_config()
    w = cfg["rhythm_weights"]

    score = w["base"]
    score -= min(ctx.user_corrections_recent * w["correction_unit"], w["correction_cap"])
    score -= min(ctx.frustration_markers * w["frustration_unit"], w["frustration_cap"])
    score -= min(ctx.topic_switch_count * w["topic_switch_unit"], w["topic_switch_cap"])
    score -= min(ctx.system_misread_count * w["misread_unit"], w["misread_cap"])
    score += min(ctx.same_axis_repetition * w["same_axis_unit"], w["same_axis_cap"])
    if ctx.unanswered_confusion:
        score -= w["unanswered_confusion_penalty"]
    return _clamp(score)


def decide(score: float, cfg: Optional[Dict[str, Any]] = None) -> str:
    """Mapea el score a una acción de banda (límite inferior inclusivo)."""
    cfg = cfg or get_config()
    b = cfg["rhythm_bands"]
    if score >= b["advance"]:
        return "advance"
    if score >= b["respond_short_and_anchor"]:
        return "respond_short_and_anchor"
    if score >= b["pause_and_calibrate"]:
        return "pause_and_calibrate"
    return "stop_generation_and_ask_axis"
