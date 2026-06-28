"""
Gravity Kernel — Shadow Mode
==============================
Punto de entrada de observación pura. Calcula los scores de Fase 1, arma el
objeto de evaluación, COMPARA la acción hipotética del kernel contra la acción
REAL ya tomada por el pipeline, y registra todo en el observation_ledger.

Garantías duras (Fase 1):
  * NUNCA muta el resultado ni el envío. Solo lee.
  * SIEMPRE devuelve None. Es observación, no control.
  * NUNCA lanza: cualquier error se traga y se loguea en debug.
  * Gating: solo corre si VX_GRAVITY_KERNEL_SHADOW=1 (o enabled=True explícito).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.gravity_kernel.shadow")

_LEDGER_DOMAIN = "gravity_kernel"
_LEDGER_OBS_TYPE = "shadow_eval"

# Acciones del kernel que implican "no generar contenido nuevo todavía".
# En shadow NO se aplica; solo sirve para medir divergencia con la acción real.
_KERNEL_NONRESPONSE = {
    "stop_generation_and_ask_axis",
    "pause_and_calibrate",
    "hold_and_collect_more_evidence",
    "block",
}


def is_enabled() -> bool:
    """Shadow mode desactivado por defecto. Se enciende con env=1."""
    return os.environ.get("VX_GRAVITY_KERNEL_SHADOW", "0") == "1"


def observe(
    *,
    content: str,
    user_id: str = "",
    result_source: Optional[str] = None,
    response_sent: bool = False,
    response_len: int = 0,
    recent_user_messages: Optional[List[str]] = None,
    had_previous_response: bool = True,
    pattern_stats: Optional[Dict[str, float]] = None,
    source: str = "unknown",
    enabled: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """
    Observa una interacción ya resuelta y registra la evaluación shadow.

    ``source`` identifica la vía de entrada (telegram_worker | web_chat |
    creator_chat) para poder segmentar el análisis por canal.

    Devuelve el objeto de evaluación (para tests) o None si está desactivado o
    si algo falla. No tiene efectos sobre la respuesta del usuario.
    """
    if enabled is None:
        enabled = is_enabled()
    if not enabled:
        return None

    try:
        from core.gravity_kernel.loader import get_config
        from core.gravity_kernel import signals, rhythm, cause_effect, evaluation

        cfg = get_config()

        r_ctx = signals.build_rhythm_ctx(
            content, recent_user_messages,
            had_previous_response=had_previous_response, cfg=cfg,
        )
        r_score = rhythm.calculate_rhythm_score(r_ctx, cfg)
        r_action = rhythm.decide(r_score, cfg)

        ce_ctx = signals.build_cause_effect_ctx(
            result_source,
            user_correction_history=r_ctx.user_corrections_recent,
            pattern_stats=pattern_stats, cfg=cfg,
        )
        ce_score = cause_effect.calculate_cause_effect_score(ce_ctx, cfg)
        ce_action = cause_effect.decide(ce_score, cfg)

        eval_obj = evaluation.build_evaluation(
            rhythm_score=r_score, rhythm_action=r_action,
            cause_effect_score=ce_score, cause_effect_action=ce_action,
            reason=f"action={ce_ctx.action_type} pattern_stats={ce_ctx.pattern_stats_present}",
        )

        # Acción REAL del pipeline (observada, no decidida por el kernel).
        actual_action = "respond" if (response_sent and response_len > 0) else "silent"
        # Acción HIPOTÉTICA del kernel reducida a respond/non-response.
        would_be = "non_response" if eval_obj["final_action"] in _KERNEL_NONRESPONSE else "respond"
        # ¿Coinciden? (respond real vs respond hipotético)
        agreement = (actual_action == "respond") == (would_be == "respond")

        _record(
            user_id=user_id,
            source=source,
            eval_obj=eval_obj,
            actual_action=actual_action,
            would_be_action=would_be,
            agreement=agreement,
            signals={
                "corrections_recent": r_ctx.user_corrections_recent,
                "frustration": r_ctx.frustration_markers,
                "system_misread": r_ctx.system_misread_count,
                "unanswered_confusion": r_ctx.unanswered_confusion,
                "action_type": ce_ctx.action_type,
                "pattern_stats_present": ce_ctx.pattern_stats_present,
                "result_source": result_source,
            },
        )
        return eval_obj
    except Exception as exc:  # nunca romper el pipeline
        logger.debug("gravity_kernel.shadow.observe skipped: %s", exc)
        return None


def _record(
    *,
    user_id: str,
    source: str,
    eval_obj: Dict[str, Any],
    actual_action: str,
    would_be_action: str,
    agreement: bool,
    signals: Dict[str, Any],
) -> None:
    """Persistir en observation_ledger. Defensivo: nunca lanza."""
    try:
        from core.self_observation import observation_ledger
        summary = (
            f"source={source} "
            f"rhythm={eval_obj['rhythm_score']} "
            f"cause_effect={eval_obj['cause_effect_score']} "
            f"would_be={would_be_action} actual={actual_action} "
            f"agreement={agreement}"
        )
        observation_ledger.record(
            domain=_LEDGER_DOMAIN,
            obs_type=_LEDGER_OBS_TYPE,
            summary=summary,
            star_id=user_id or None,
            evidence={
                "source": source,
                "eval_object": eval_obj,
                "actual_action": actual_action,
                "would_be_action": would_be_action,
                "agreement": agreement,
                "signals": signals,
            },
            severity="info" if agreement else "warning",
        )
    except Exception as exc:
        logger.debug("gravity_kernel.shadow ledger write skipped: %s", exc)
