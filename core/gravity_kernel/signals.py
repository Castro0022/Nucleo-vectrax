"""
Gravity Kernel — Signal Extraction
====================================
Convierte entradas observables (texto del usuario, historial reciente, source
de la respuesta ya producida) en los contextos que consumen Ritmo y
Causa/Efecto.

Principio no negociable: toda señal de "fallo" (correcciones, misread) se
infiere de la REACCIÓN del usuario, nunca de autoevaluación del LLM.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.gravity_kernel.loader import get_config


# ── Contextos ──────────────────────────────────────────────────────────────

@dataclass
class RhythmCtx:
    user_corrections_recent: int = 0
    frustration_markers: int = 0
    topic_switch_count: int = 0
    same_axis_repetition: int = 0
    system_misread_count: int = 0
    unanswered_confusion: bool = False


@dataclass
class CauseEffectCtx:
    action_type: str = "conversation_reply"
    cost_of_error: float = 0.20
    domain_risk_level: float = 0.20
    reversibility_score: float = 0.90
    win_rate: float = 0.50          # señal de éxito unificada (win_rate ∪ histórico)
    expectancy: float = 0.0
    confidence_score: float = 0.50
    user_correction_history: int = 0
    pattern_stats_present: bool = False  # honestidad: ¿hubo datos reales?


# ── Helpers de texto ─────────────────────────────────────────────────────────

def _count_markers(text: str, markers: List[str]) -> int:
    """Cuenta cuántos marcadores (substring, case-insensitive) aparecen."""
    if not text:
        return 0
    low = text.lower()
    return sum(1 for m in markers if m and m.lower() in low)


def _join_recent(content: str, recent: Optional[List[str]]) -> str:
    parts = [content or ""]
    if recent:
        parts.extend(recent)
    return "\n".join(parts)


# ── Extracción Ritmo ─────────────────────────────────────────────────────────

def build_rhythm_ctx(
    content: str,
    recent_user_messages: Optional[List[str]] = None,
    *,
    had_previous_response: bool = False,
    cfg: Optional[Dict[str, Any]] = None,
) -> RhythmCtx:
    """
    Construye el contexto de Ritmo a partir de señales de texto verificables.

    ``system_misread_count`` se ancla en la reacción del usuario: cuenta los
    marcadores de corrección presentes SOLO si hubo una respuesta previa del
    sistema a la que el usuario podría estar reaccionando. Nunca proviene de un
    juicio del propio LLM.
    """
    cfg = cfg or get_config()
    corrections = _count_markers(content, cfg["correction_markers"])
    corrections_recent = _count_markers(
        _join_recent(content, recent_user_messages), cfg["correction_markers"]
    )
    frustration = _count_markers(
        _join_recent(content, recent_user_messages), cfg["frustration_markers"]
    )

    # Misread solo si el usuario reacciona a una respuesta previa real.
    misread = corrections if had_previous_response else 0
    # Confusión sin resolver: corrección + pregunta abierta en el mismo turno.
    unanswered = bool(corrections and "?" in (content or ""))

    return RhythmCtx(
        user_corrections_recent=corrections_recent,
        frustration_markers=frustration,
        topic_switch_count=0,        # neutro en Fase 1 (requiere semántica)
        same_axis_repetition=0,      # neutro en Fase 1
        system_misread_count=misread,
        unanswered_confusion=unanswered,
    )


# ── Extracción Causa/Efecto ──────────────────────────────────────────────────

def classify_action(result_source: Optional[str], cfg: Optional[Dict[str, Any]] = None) -> str:
    """Mapea ``result.source`` a una clave de domain_risk_table."""
    cfg = cfg or get_config()
    mapping = cfg.get("source_to_action", {})
    default = cfg.get("default_action", "conversation_reply")
    if not result_source:
        return default
    return mapping.get(result_source, default)


def build_cause_effect_ctx(
    result_source: Optional[str],
    *,
    user_correction_history: int = 0,
    pattern_stats: Optional[Dict[str, float]] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> CauseEffectCtx:
    """
    Construye el contexto de Causa/Efecto.

    ``pattern_stats`` (win_rate/expectancy/confidence) es opcional: si no hay
    datos reales del gravity engine, se usan defaults neutros y se marca
    ``pattern_stats_present=False`` para no fingir evidencia.
    """
    cfg = cfg or get_config()
    action = classify_action(result_source, cfg)
    risk = cfg["domain_risk_table"].get(
        action, cfg["domain_risk_table"].get("conversation_reply", {})
    )
    neutral = cfg["neutral_defaults"]

    present = bool(pattern_stats)
    stats = pattern_stats or {}
    return CauseEffectCtx(
        action_type=action,
        cost_of_error=float(risk.get("cost_of_error", 0.20)),
        domain_risk_level=float(risk.get("domain_risk_level", 0.20)),
        reversibility_score=float(risk.get("reversibility", 0.90)),
        win_rate=float(stats.get("win_rate", neutral["win_rate"])),
        expectancy=float(stats.get("expectancy", neutral["expectancy"])),
        confidence_score=float(stats.get("confidence", neutral["confidence"])),
        user_correction_history=int(user_correction_history),
        pattern_stats_present=present,
    )


def fetch_pattern_stats(fingerprint: str) -> Optional[Dict[str, float]]:
    """
    Best-effort: deriva win_rate/expectancy desde el outcome_history del gravity
    engine para un fingerprint dado. Devuelve None si no hay registro o historia.
    Defensivo: nunca lanza.
    """
    try:
        from core.learn.gravity_engine import GravityIndex
        rec = GravityIndex().get(fingerprint)
        if rec is None:
            return None
        history = list(getattr(rec, "outcome_history", []) or [])
        if not history:
            return None
        wins = sum(1 for o in history if str(o).lower() in ("win", "success", "ok"))
        losses = sum(1 for o in history if str(o).lower() in ("loss", "fail", "error"))
        graded = wins + losses
        if graded == 0:
            return None
        win_rate = wins / graded
        return {
            "win_rate": win_rate,
            "expectancy": win_rate - (losses / graded),
            "confidence": min(1.0, graded / 20.0),  # más historia → más confianza
        }
    except Exception:
        return None
