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


def _gradable_history(rec) -> List[str]:
    """De que lista se deriva el desempeno de una estrella.

    `verified_outcomes` PRIMERO: lo escribe unicamente
    `GravityIndex.record_verified_outcome()`, con el veredicto de un
    `OutcomeAdapter` contra la verdad objetiva del dominio (precio realizado,
    entrega a tiempo...). Es la unica lista cuyo contenido es, por
    construccion, un resultado.

    `outcome_history` como RESPALDO, solo si la primera esta vacia. Esa lista
    es el registro de OBSERVACION que alimenta `record_event()`, y su contenido
    depende del llamador: `core.learn.provider_stars` si escribe ahi veredictos
    graduables ("success"/"error" de una llamada real a un proveedor), y esas
    estrellas de dominio `ai_provider` ya cualificaban patrones antes de que
    existiera `verified_outcomes`. Ignorarla les quitaria un desempeno que ya
    era real. El resto de llamadores escriben texto no graduable (el texto del
    evento, "observed", "queried") que no suma ni resta: `derive_pattern_stats`
    solo cuenta las entradas que reconoce como win o como loss.

    Nunca se mezclan las dos: una estrella con resultados verificados se juzga
    por ellos, no por lo que el propio sistema dijo de si mismo.
    """
    verified = list(getattr(rec, "verified_outcomes", []) or [])
    if verified:
        # LA DECISIÓN DEL NÚCLEO SOBRE QUÉ EVIDENCIA PUEDE ENSEÑAR.
        #
        # Guardar la procedencia no es usarla. Un resultado marcado como
        # SIMULADO se conserva en la estrella —es observable, y su exclusión
        # tiene que poder auditarse— pero NO se gradúa: aprender de un
        # simulador y aplicar ese criterio a decisiones reales es exactamente
        # el error que la procedencia existe para impedir.
        #
        # `unknown` sí gradúa, y se cuenta aparte (ver `derive_pattern_stats`):
        # es un hueco que cerrar, no una contaminación demostrada. Dejar de
        # graduarlo sería un cambio de comportamiento mayor que el que
        # corresponde decidir aquí, y quedaría invisible.
        out = []
        for e in verified:
            if not isinstance(e, dict):
                out.append(str(e))       # forma antigua del campo
                continue
            if str(e.get("origin_kind", "")) == "simulated":
                continue
            out.append(str(e.get("status", "")))
        return out
    return list(getattr(rec, "outcome_history", []) or [])


def provenance_breakdown(rec) -> Dict[str, int]:
    """Cuántos resultados verificados tiene la estrella de cada procedencia.

    Permite responder "¿sobre qué está aprendiendo este patrón?" sin adivinar,
    y hace visible lo que el graduador excluyó en vez de dejarlo en silencio.
    """
    counts: Dict[str, int] = {}
    for e in list(getattr(rec, "verified_outcomes", []) or []):
        kind = str(e.get("origin_kind", "unknown")) if isinstance(e, dict) else "unknown"
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def derive_pattern_stats(rec) -> Optional[Dict[str, float]]:
    """La DERIVACIÓN pura, sin ninguna E/S.

    Separada de `fetch_pattern_stats` para que un consumidor que ya tenga un
    snapshot del gravity index en memoria pueda reutilizarlo en lugar de
    releer el índice entero del disco por cada fingerprint. `GravityIndex.get()`
    llama a `_load()`, que toma el lock y lee el archivo COMPLETO en cada
    llamada: evaluar 50 convergencias de 2 patrones costaría 100 lecturas
    íntegras del índice por ciclo.

    Ambas rutas comparten este cuerpo, así que no pueden divergir.
    """
    try:
        if rec is None:
            return None
        history = _gradable_history(rec)
        wins = sum(1 for o in history if str(o).lower() in ("win", "success", "ok"))
        losses = sum(1 for o in history if str(o).lower() in ("loss", "fail", "error"))
        graded = wins + losses
        if graded == 0:
            return None
        win_rate = wins / graded
        breakdown = provenance_breakdown(rec)
        return {
            "win_rate": win_rate,
            "expectancy": win_rate - (losses / graded),
            "confidence": min(1.0, graded / 20.0),  # más historia → más confianza
            # Tamaño de muestra REAL: outcomes graduados (win/loss), no hits ni
            # confirmaciones del escáner. El puente causal lo exige para poder
            # contrastar contra core.domain_knowledge.MIN_SAMPLE sin suponerlo.
            "sample_size": float(graded),
            # Sobre QUÉ está aprendiendo este patrón. `simulated` son los que
            # el núcleo excluyó del cálculo; `unknown`, los que cuentan pero
            # cuya procedencia nadie declaró.
            "real": float(breakdown.get("real", 0)),
            "simulated": float(breakdown.get("simulated", 0)),
            "unknown": float(breakdown.get("unknown", 0)),
        }
    except Exception:
        return None


def fetch_pattern_stats(fingerprint: str) -> Optional[Dict[str, float]]:
    """
    Best-effort: deriva win_rate/expectancy desde la historia graduable del
    gravity engine para un fingerprint dado (ver `_gradable_history`). Devuelve
    None si no hay registro o historia. Defensivo: nunca lanza. Lee el índice:
    para muchas consultas seguidas, usar `derive_pattern_stats` sobre un
    snapshot único (`causal_learning.cycle_stats_fetcher`).

    Usa el índice VIVO (`get_gravity_index()`), no un `GravityIndex()` recién
    construido. En producción ambos apuntan al mismo fichero, así que no
    cambia lo que se lee; lo que evita es construir un índice nuevo —con su
    migración y su limpieza de temporales— en cada consulta, y que esta ruta
    mire a un sitio distinto del que mira `cycle_stats_fetcher()`, que ya
    usaba el índice vivo. Dos rutas de lectura que no coinciden es la clase de
    divergencia que deja un resultado aplicado fuera del alcance del
    evaluador.
    """
    try:
        from core.learn.gravity_engine import get_gravity_index
        return derive_pattern_stats(get_gravity_index().get(fingerprint))
    except Exception:
        return None
