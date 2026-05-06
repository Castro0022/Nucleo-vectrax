"""
core/voice/humanizer.py — Motor de Voz Final

Recibe la respuesta cruda del LLM y la deja humana. Editor, no censor.

Reglas:
  - Quita aperturas de modelo de lenguaje ("Como modelo de lenguaje...",
    "As an AI assistant...").
  - Quita disclaimers innecesarios al final.
  - Normaliza espacios y puntuación.
  - Si tone=brief y la respuesta excede el límite, trunca al último
    punto natural; NO la sustituye por placeholder.
  - Si tone=warm y user_name disponible y nombre no aparece, deja la
    respuesta como está (no fuerza el nombre — eso lo hace el LLM
    siguiendo la persona directive).

NUNCA reemplaza por texto genérico. Si después de limpiar queda algo
sustancial, devuelve eso. Si queda vacío, devuelve "" y el caller
decide (típicamente: usa fallback_response constructiva).
"""

from __future__ import annotations

import re
from typing import Optional

from core.voice.intent import (
    TONE_BRIEF, TONE_WARM, TONE_TECHNICAL, TONE_CASUAL,
    TONE_PLAYFUL, TONE_SUPPORTIVE, TONE_DEEP, TONE_SERIOUS,
)


# Aperturas robóticas: se eliminan SIEMPRE
_ROBOTIC_OPENERS = re.compile(
    r"^\s*(?:"
    r"como\s+(?:modelo\s+de\s+lenguaje|asistente\s+(?:virtual|de\s+IA)|IA|inteligencia\s+artificial)[,\s]+"
    r"|soy\s+un(?:a)?\s+(?:modelo|asistente|bot|programa|inteligencia)[,\s]+"
    r"|as\s+an?\s+(?:AI|language\s+model|assistant|virtual\s+assistant)[,\s]+"
    r"|i'm\s+an?\s+(?:AI|language\s+model|assistant|bot)[,\s]+"
    r"|claro[,!]\s+(?:con\s+gusto|encantado|por\s+supuesto)[.,!]?\s+"
    r"|por\s+supuesto[,.!]?\s+"
    r"|con\s+(?:mucho\s+)?gusto[,.!]?\s+"
    r"|sure!?\s+(?:i'd\s+be\s+happy|i\s+can\s+help|let\s+me)[,.!]*\s*"
    r"|of\s+course!?[,.!]?\s+"
    r"|great\s+question!?\s+"
    r"|¡(?:excelente|buena|gran)\s+pregunta!?\s*"
    r"|¡hola!?\s*¿en\s+qu[eé]\s+puedo\s+ayudar(?:te|le)?\??\s*"
    r")",
    re.IGNORECASE,
)

# Cierres genéricos / disclaimers al final
_TRAILING_FILLERS = re.compile(
    r"\s*(?:"
    r"espero\s+(?:que\s+)?(?:esto|esta\s+información)\s+(?:te\s+)?(?:sea\s+útil|ayude|sirva)\.?"
    r"|si\s+(?:tienes|necesitas)\s+(?:más\s+)?(?:preguntas|dudas|ayuda)[^.]*\.?"
    r"|no\s+dudes\s+en\s+(?:preguntar|escribir|contactar)[^.]*\.?"
    r"|i\s+hope\s+(?:this|that)\s+helps\.?"
    r"|feel\s+free\s+to\s+(?:ask|reach\s+out|contact)[^.]*\.?"
    r"|let\s+me\s+know\s+if[^.]*\.?"
    r"|is\s+there\s+anything\s+else[^.]*\.?"
    r"|¿(?:hay\s+algo|te\s+puedo|puedo)\s+(?:más\s+|en\s+)?(?:ayudar)?\??"
    r"|¿(?:en\s+qué\s+más|algo\s+más)\s+(?:te\s+puedo|puedo)\s+(?:ayudar)?\??"
    r")\s*$",
    re.IGNORECASE,
)

# Emojis (rangos Unicode comunes) — Vectrax sin emojis
_EMOJI_RE = re.compile(
    r"[\U0001F600-\U0001F64F"
    r"\U0001F300-\U0001F5FF"
    r"\U0001F680-\U0001F6FF"
    r"\U0001F900-\U0001F9FF"
    r"\U00002702-\U000027B0"
    r"\U0001FA00-\U0001FA6F"
    r"\U0001FA70-\U0001FAFF]+"
)


# Límites por tono (caracteres)
_LIMITS_BY_TONE = {
    TONE_BRIEF: 280,        # ~2 líneas
    TONE_WARM: 400,         # ~3 líneas
    TONE_CASUAL: 600,
    TONE_PLAYFUL: 600,
    TONE_SUPPORTIVE: 600,
    TONE_TECHNICAL: 2000,   # más generoso para detalle técnico
    TONE_DEEP: 1500,
    TONE_SERIOUS: 1000,
}
_DEFAULT_LIMIT = 1500


def humanize_response(
    raw_llm_response: str,
    tone: str = TONE_CASUAL,
    user_name: Optional[str] = None,
) -> str:
    """Limpia/edita la respuesta del LLM sin reemplazarla.

    Args:
      raw_llm_response: salida cruda del LLM.
      tone: tono activo (de classify_tone).
      user_name: nombre del usuario si está disponible (no se inyecta
        forzosamente; queda para que el LLM siguiendo la persona
        directive ya lo haya hecho).

    Returns:
      String limpio. Vacío si la limpieza dejó nada sustancial; el
      caller decide qué hacer (típicamente usar fallback_response).
    """
    if not raw_llm_response:
        return ""

    text = raw_llm_response.strip()

    # 1. Quitar aperturas robóticas (puede haber varias en serie)
    while True:
        new_text = _ROBOTIC_OPENERS.sub("", text).lstrip(" ,;:.")
        if new_text == text:
            break
        text = new_text

    # 2. Quitar emojis (regla Vectrax: sin emojis)
    text = _EMOJI_RE.sub("", text)

    # 3. Quitar trailing fillers
    text = _TRAILING_FILLERS.sub("", text).rstrip()

    # 4. Normalizar whitespace
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" +([.,;:!?])", r"\1", text)
    text = text.strip()

    # 5. Capitalizar primera letra si se perdió por limpieza inicial
    if text and text[0].islower():
        text = text[0].upper() + text[1:]

    # 6. Aplicar límite por tono — corte natural, no truncado seco
    limit = _LIMITS_BY_TONE.get(tone, _DEFAULT_LIMIT)
    if len(text) > limit:
        text = _smart_truncate(text, limit)

    # 7. Sustancia mínima
    if len(text.strip()) < 2:
        return ""

    return text


def _smart_truncate(text: str, max_len: int) -> str:
    """Trunca al último punto/oración completa antes de max_len.

    Si no hay un cierre natural antes del límite, simplemente trunca
    y deja que el caller decida si añade '…' (no lo añadimos por
    defecto — Vectrax es directo, sin colas).
    """
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    last_end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    if last_end > max_len // 3:
        return cut[:last_end + 1].strip()
    last_space = cut.rfind(" ")
    if last_space > max_len // 2:
        return cut[:last_space].strip()
    return cut.strip()
