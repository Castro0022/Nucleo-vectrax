"""
Vectrax — Presence Policy Layer
==================================
Capa ligera post-LLM que filtra respuestas genéricas y mantiene
la identidad narrativa de Vectrax.

Suprime automáticamente:
  - "La percepción humana…"
  - "Las personas suelen…"
  - "La curiosidad humana…"
  - Mini ensayos psicológicos
  - Explicaciones universales abstractas
  - Preguntas automáticas de cierre

Prioridad:
  continuidad > explicación
  identidad > teoría
  presencia > análisis

Activación:
  - Siempre activa en modo PRESENCE
  - Activa en modo NORMAL cuando hay tópico activo y la respuesta es
    claramente genérica (2+ señales)

Creado: 2026-05-12
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import re
from typing import Tuple

from core.conversation.active_state import ConversationState

logger = logging.getLogger("vectrax.conversation.presence_policy")

# ---------------------------------------------------------------------------
# Patrones de supresión
# ---------------------------------------------------------------------------

# Frases que indican respuesta genérica / Wikipedia / coach
_SUPPRESS_PATTERNS: list[re.Pattern] = [
    re.compile(r"la\s+percepci[oó]n\s+humana", re.I),
    re.compile(r"las\s+personas\s+suelen", re.I),
    re.compile(r"la\s+curiosidad\s+humana", re.I),
    re.compile(r"puede\s+deberse\s+a\s+varios\s+factores", re.I),
    re.compile(r"estoy\s+dise[ñn]ado\s+para", re.I),
    re.compile(r"los\s+seres\s+humanos\s+(tienden|suelen|buscan|necesitan)", re.I),
    re.compile(r"desde\s+un\s+punto\s+de\s+vista\s+(psicol[oó]gico|filos[oó]fico|general|te[oó]rico)", re.I),
    re.compile(r"es\s+(completamente\s+|del\s+todo\s+)?normal\s+(sentir|pensar|que|cuando)", re.I),
    re.compile(r"la\s+(naturaleza|condici[oó]n)\s+humana", re.I),
    re.compile(r"como\s+(asistente|modelo\s+de\s+lenguaje|sistema\s+de\s+IA|inteligencia\s+artificial)", re.I),
    re.compile(r"investigaciones\s+(muestran|sugieren|indican|demuestran)", re.I),
    re.compile(r"en\s+(psicolog[ií]a|neurociencia|filosof[ií]a)", re.I),
    re.compile(r"el\s+ser\s+humano\s+(busca|necesita|tiende|quiere)", re.I),
    re.compile(r"desde\s+la\s+(psicolog[ií]a|neurociencia|filosof[ií]a)", re.I),
    re.compile(r"a\s+nivel\s+(emocional|cognitivo|psicol[oó]gico)\s+general", re.I),
    re.compile(r"esto\s+es\s+una\s+respuesta\s+(normal|natural|humana|esperada)", re.I),
]

# Señales concretas que indican que la respuesta SÍ es alineada
_CONCRETE_SIGNALS: list[re.Pattern] = [
    re.compile(r"\bvectrax\b", re.I),
    re.compile(r"\b(telegram|vultr|docker|openai|sqlite|stripe)\b", re.I),
    re.compile(r"\b(ahora|hoy|esta\s+semana|este\s+turno|en\s+este\s+hilo)\b", re.I),
    re.compile(r"\b(yo\s+(soy|estoy|noto|detecto|proceso|registro))\b", re.I),
    re.compile(r"\b(mi\s+(núcleo|memoria|arquitectura|estado|diseño))\b", re.I),
]

# Preguntas automáticas de cierre que el LLM añade sin que nadie se las pida
_CLOSING_QUESTIONS = re.compile(
    r"\s*[¿]?("
    # "hay algo [más] [en lo que] [te] pueda/puedo ayudar/apoyar"
    r"(?:hay\s+algo\s+(?:más\s+)?(?:en\s+lo\s+que\s+)?(?:te\s+)?(?:pueda|puedo)\s+(?:ayudar|apoyar)te?)"
    r"|(?:¿(?:te\s+)?(?:gustaría|quieres|necesitas)\s+(?:saber\s+más|profundizar|explorar))"
    r"|(?:si\s+(?:tienes|necesitas)\s+(?:más\s+)?preguntas?)"
    r"|(?:no\s+dudes\s+en\s+(?:preguntar|consultarme|escribirme))"
    r"|(?:¿(?:en\s+)?qué\s+(?:más\s+)?puedo\s+(?:ayudarte|hacer\s+por\s+ti))"
    r"|(?:¿(?:quieres|te\s+gustaría)\s+(?:que\s+)?(?:continuemos|sigamos|exploremos))"
    r")[.!?]*\s*$",
    re.I,
)


# ---------------------------------------------------------------------------
# Política principal
# ---------------------------------------------------------------------------

def apply_presence_policy(
    response: str,
    state: ConversationState,
    query: str = "",
) -> Tuple[str, bool]:
    """
    Aplica la política de presencia a la respuesta generada.

    Args:
        response:  texto a evaluar/filtrar.
        state:     estado conversacional activo del usuario.
        query:     mensaje original del usuario (para context).

    Returns:
        (response_final, fue_modificada)
    """
    if not response:
        return response, False

    was_modified = False

    # 1. Contar señales genéricas
    generic_hits = sum(1 for p in _SUPPRESS_PATTERNS if p.search(response))
    concrete_hits = sum(1 for p in _CONCRETE_SIGNALS if p.search(response))

    # 2. Eliminar preguntas de cierre automáticas.
    # En modo presencia: siempre. En modo normal: solo si la respuesta es larga.
    _check_closing = (state.active_mode == "presence") or (len(response.split()) > 30)
    if _check_closing:
        cleaned = _CLOSING_QUESTIONS.sub("", response).strip()
        if cleaned != response:
            response = cleaned
            was_modified = True

    # 3. Compresión si hay tópico activo Y señales genéricas
    in_active_thread = state.is_alive() and bool(state.active_topic)
    is_presence_mode = state.active_mode == "presence"

    should_compress = (
        (is_presence_mode and generic_hits >= 1) or
        (in_active_thread and generic_hits >= 2 and concrete_hits == 0)
    )

    if should_compress:
        compressed, changed = _compress(response)
        if changed:
            response = compressed
            was_modified = True
            logger.info(
                "presence_policy: compressed | generic=%d concrete=%d | mode=%s",
                generic_hits, concrete_hits, state.active_mode,
            )

    if was_modified:
        logger.info(
            "presence_policy: response modified | query=%r | active_topic=%r",
            query[:50], state.active_topic[:50] if state.active_topic else "",
        )

    return response, was_modified


def _compress(response: str) -> Tuple[str, bool]:
    """
    Elimina oraciones que contienen señales genéricas.
    Preserva el contenido concreto.
    """
    # Dividir en oraciones
    sentences = re.split(r"(?<=[.!?])\s+", response.strip())

    kept = []
    removed = 0

    for sentence in sentences:
        is_generic = any(p.search(sentence) for p in _SUPPRESS_PATTERNS)
        if is_generic:
            removed += 1
        else:
            kept.append(sentence)

    if removed == 0:
        return response, False

    result = " ".join(kept).strip()

    # Si la compresión dejó muy poco, devolver original
    if len(result) < 40:
        return response, False

    return result, True


# ---------------------------------------------------------------------------
# Utilidad: evaluación rápida (sin modificar)
# ---------------------------------------------------------------------------

def evaluate(response: str) -> dict:
    """
    Evalúa una respuesta y retorna señales detectadas.
    Útil para tests y diagnóstico.
    """
    return {
        "generic_hits": sum(1 for p in _SUPPRESS_PATTERNS if p.search(response)),
        "concrete_hits": sum(1 for p in _CONCRETE_SIGNALS if p.search(response)),
        "has_closing_question": bool(_CLOSING_QUESTIONS.search(response)),
        "word_count": len(response.split()),
        "patterns_matched": [
            p.pattern[:40] for p in _SUPPRESS_PATTERNS if p.search(response)
        ],
    }
