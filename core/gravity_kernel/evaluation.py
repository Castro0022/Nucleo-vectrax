"""
Gravity Kernel — Objeto de Evaluación Unificado
=================================================
Contrato de datos que produce el kernel. En Fase 1 solo Ritmo y Causa/Efecto
están implementados; los otros 5 ejes quedan ``None`` (presentes en el contrato
pero explícitamente no calculados todavía).

``final_action`` combina las dos bandas tomando la MÁS conservadora. Esto es
solo la acción HIPOTÉTICA del kernel: en shadow mode no se ejecuta.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# Severidad de cada acción de banda: mayor = más restrictivo. Permite elegir la
# acción combinada más conservadora entre Ritmo y Causa/Efecto.
_SEVERITY: Dict[str, int] = {
    # Ritmo
    "advance": 0,
    "respond_short_and_anchor": 1,
    "pause_and_calibrate": 3,
    "stop_generation_and_ask_axis": 4,
    # Causa/Efecto
    "execute": 0,
    "execute_with_audit": 1,
    "simulate_or_ask_confirmation": 2,
    "hold_and_collect_more_evidence": 3,
    "block": 4,
}


def combine_final_action(rhythm_action: str, cause_effect_action: str) -> str:
    """Devuelve la acción más conservadora (mayor severidad) entre ambas."""
    ra = (rhythm_action, _SEVERITY.get(rhythm_action, 0))
    ca = (cause_effect_action, _SEVERITY.get(cause_effect_action, 0))
    return ra[0] if ra[1] >= ca[1] else ca[0]


def build_evaluation(
    *,
    rhythm_score: float,
    rhythm_action: str,
    cause_effect_score: float,
    cause_effect_action: str,
    reason: str = "",
) -> Dict[str, Any]:
    """
    Construye el objeto de evaluación unificado (contrato del documento).

    Los ejes no implementados en Fase 1 quedan en ``None`` a propósito.
    ``audit_required`` es True por defecto salvo en modos CONSERVE/WAIT (no
    implementados aún), por lo que en Fase 1 siempre es True.
    """
    final_action = combine_final_action(rhythm_action, cause_effect_action)
    generation_mode: Optional[str] = None  # eje 7 no implementado en Fase 1
    audit_required = generation_mode not in ("CONSERVE", "WAIT")

    return {
        "mentalism_score": None,
        "correspondence_score": None,
        "vibration_score": None,
        "polarity_score": None,
        "rhythm_score": round(float(rhythm_score), 4),
        "cause_effect_score": round(float(cause_effect_score), 4),
        "generation_mode": generation_mode,
        "rhythm_action": rhythm_action,
        "cause_effect_action": cause_effect_action,
        "final_action": final_action,
        "reason": reason,
        "audit_required": audit_required,
        "phase": 1,
    }
