"""
Vectrax — Implicit Reference Resolver
========================================
Detecta referencias implícitas en el mensaje del usuario y genera
contexto adicional para inyectar en el prompt del LLM.

NO modifica el mensaje original.
NO reemplaza el contenido.
Solo devuelve una cadena de contexto para agregar a extra_context.

Ejemplos:
  "¿Y eso por qué pasa?" + active_topic="Vectrax responde frío"
  → "[Referencia implícita] El usuario pregunta sobre: Vectrax responde frío"

  "¿Y si él no responde?" + active_entity="Carlos"
  → "[Referencia implícita] La entidad referenciada: Carlos"

Creado: 2026-05-12
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from core.conversation.active_state import ConversationState

logger = logging.getLogger("vectrax.conversation.reference_resolver")

# ---------------------------------------------------------------------------
# Patrones de referencia implícita
# ---------------------------------------------------------------------------

# Anáforas que apuntan al tópico activo
_TOPIC_REF = re.compile(
    r"\b("
    r"eso|esto|aquello|eso\s+mismo|de\s+eso|por\s+eso|lo\s+mismo"
    r"|lo\s+(?:primero|anterior|que\s+dijiste|que\s+mencionaste)"
    r"|el\s+tema|ese\s+(?:tema|problema|punto|asunto)"
    r"|volviendo\s+a|retomando|a\s+lo\s+que|en\s+cuanto\s+a\s+eso"
    r"|así|de\s+esta\s+forma|de\s+esa\s+manera"
    r")\b",
    re.I,
)

# Anáforas que apuntan a la entidad activa
_ENTITY_REF = re.compile(
    r"\b("
    r"él|ella|ellos|ellas|esa\s+persona|ese\s+(?:cliente|usuario|chico|tipo)"
    r"|ese|esa"
    r")\b",
    re.I,
)

# Preguntas cortas que implican continuación del hilo
_CONTINUATION_QUESTION = re.compile(
    r"^[¿]?\s*("
    r"(?:y\s+)?(?:por\s+qué|cómo\s+(?:así|es\s+eso|funciona|pasa))"
    r"|(?:y\s+)?(?:qué\s+(?:significa|quiere\s+decir|implica))"
    r"|(?:y\s+)?entonces"
    r"|(?:y\s+)?(?:en\s+qué\s+(?:sentido|parte))"
    r")\b",
    re.I,
)


# ---------------------------------------------------------------------------
# Resolver principal
# ---------------------------------------------------------------------------

def resolve_references(
    content: str,
    state: ConversationState,
) -> str:
    """
    Analiza el mensaje y genera contexto adicional si hay referencias implícitas.

    Retorna:
        String de contexto para inyectar en extra_context (puede ser vacío).
    """
    if not content or not state.is_alive():
        return ""

    hints: list[str] = []

    has_topic_ref = bool(_TOPIC_REF.search(content))
    has_entity_ref = bool(_ENTITY_REF.search(content))
    is_continuation = bool(_CONTINUATION_QUESTION.match(content.strip()))

    # Referencia implícita al tópico activo
    if (has_topic_ref or is_continuation) and state.active_topic:
        hints.append(f"El usuario se refiere a: «{state.active_topic}»")

    # Referencia implícita a la entidad activa
    if has_entity_ref and state.active_entity:
        hints.append(f"La entidad referenciada es: {state.active_entity}")

    # Modo presencia activo — recordar al LLM que hay un hilo en curso
    if state.active_mode == "presence" and state.active_topic and not hints:
        # No hay referencia explícita pero el modo presencia está activo
        # y el mensaje es corto → el hilo sigue siendo relevante
        words = content.strip().split()
        if len(words) <= 7:
            hints.append(f"Hilo activo sobre: «{state.active_topic}»")

    if not hints:
        return ""

    context = "[Referencia implícita detectada]\n" + "\n".join(f"• {h}" for h in hints)

    logger.info(
        "reference_resolver: context injected | refs=[topic=%s entity=%s cont=%s] | user_msg=%r",
        has_topic_ref, has_entity_ref, is_continuation,
        content[:50],
    )

    return context
