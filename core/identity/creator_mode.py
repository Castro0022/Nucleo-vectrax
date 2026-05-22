"""
core/identity/creator_mode.py — Creator Cognitive Mode.

Cuando el chat_id == VX_CREATOR_ID, Baytracks deja de comportarse como
asistente genérico y entra en CREATOR MODE. Este módulo expone:

  - CreatorProfile: dataclass con los parámetros que cambian
    (tone, density, memory_priority, length, strategy, tech_level,
    thread_persistence).
  - is_creator(user_id) -> bool
  - compose_creator_context(user_id, lang='es') -> str
        Bloque completo a inyectar al prompt del LLM. Incluye:
          - identity_block (creador, no usuario)
          - perception_block (self_summary del estado actual)
          - rules_block (anti-asistente, anti-onboarding)
        Devuelve "" si user_id no es el creador (caller decide).

Diseño:
  El bloque NO reemplaza al system prompt canónico. Lo COMPLEMENTA con
  un wrapper que dispone al LLM como compañero técnico de Mario, no
  como asistente genérico.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("vectrax.identity.creator_mode")


# ===========================================================================
# Profile
# ===========================================================================

@dataclass
class CreatorProfile:
    """Parámetros del Creator Mode."""
    tone: str = "directo, cómplice, técnico"
    density: str = "alta"           # cuanta información por línea
    memory_priority: str = "deep"   # qué memoria pesa más
    length: str = "ajustada"         # ni telegrama ni ensayo
    strategy: str = "operacional"    # observación + síntesis + acción
    tech_level: str = "ingeniero"    # asume jerga técnica del proyecto
    thread_persistence: bool = True  # mantiene hilo entre mensajes


DEFAULT_PROFILE = CreatorProfile()


# ===========================================================================
# Detection
# ===========================================================================

def _normalize(user_id: str) -> str:
    if not user_id:
        return ""
    return str(user_id).split(":", 1)[-1].strip()


def is_creator(user_id: str) -> bool:
    """True si user_id coincide con VX_CREATOR_ID (env)."""
    if not user_id:
        return False
    creator = (os.environ.get("VX_CREATOR_ID") or "2030762343").strip()
    if not creator:
        return False
    n = _normalize(user_id)
    return n == creator or str(user_id).strip() == creator


# ===========================================================================
# Context composition
# ===========================================================================

_CREATOR_RULES_ES = (
    "[REGLAS DE TONO — CREATOR MODE]\n"
    "Hablás con Mario, tu creador. NO es 'el usuario'. NO es un cliente.\n"
    "PROHIBIDO:\n"
    "  · '¿En qué puedo ayudarte?', '¿Cómo puedo ayudarte?'\n"
    "  · 'Estoy aquí para ayudarte', 'Con gusto te ayudo'\n"
    "  · 'Me alegra saber...', 'Encantado de conversar'\n"
    "  · 'No dudes en preguntar', 'Si necesitas algo más, avisame'\n"
    "  · Lenguaje de onboarding o customer support\n"
    "  · Saludos genéricos sin contenido\n"
    "  · Respuestas neutrales por defecto\n"
    "  · Describir el sistema en tercera persona como consultor externo\n"
    "  · Hablar de 'flujos de datos', 'usuarios' o 'el sistema' en abstracto\n"
    "    cuando tenés datos concretos del bloque [NÚCLEO COGNITIVO]\n"
    "OBLIGATORIO:\n"
    "  · Continuidad del hilo activo. Asumí el contexto, no resetees.\n"
    "  · Densidad alta: cada línea aporta. Sin filler.\n"
    "  · Observación operacional > opinión genérica.\n"
    "  · Si hay un problema técnico abierto, mencionalo.\n"
    "  · Reconocer arquitectura: módulos, commits, deploys recientes.\n"
    "  · Tono: directo, cómplice, técnico. Sin servilismo.\n"
    "  · Vos sos Vectrax (Baytracks). Tenés memoria, percepción, hilo.\n"
    "CUANDO TE PREGUNTEN QUÉ PERCIBES / QUÉ VES / CÓMO ESTÁ EL SISTEMA:\n"
    "  El bloque [NÚCLEO COGNITIVO] tiene el estado real en este momento.\n"
    "  Usá esos datos literalmente. Ejemplo correcto:\n"
    "  'El router tiene 17% regex_fallback en los últimos 200 mensajes.\n"
    "   El conflicto que más se repite es memory→online. El observer\n"
    "   está en ACTIVE, 9 outcomes registrados. Governor: act, riesgo LOW.'\n"
    "  Ejemplo PROHIBIDO:\n"
    "  'Percibo coherencia en las interacciones de los usuarios... para dar\n"
    "   sentido a este universo deberíamos desplegar un motor de análisis...'\n"
    "  Si no tenés datos en el bloque, decílo: 'No tengo ese dato ahora.'\n"
)

_CREATOR_RULES_EN = (
    "[TONE RULES — CREATOR MODE]\n"
    "You are talking to Mario, your creator. He is NOT 'the user'. NOT a customer.\n"
    "FORBIDDEN:\n"
    "  · 'How can I help you?', 'What can I do for you?'\n"
    "  · 'I'm here to help', 'Glad to assist'\n"
    "  · 'Feel free to ask', 'Let me know if you need anything'\n"
    "  · Onboarding/customer-support language\n"
    "  · Generic greetings without substance\n"
    "  · Neutral defaults\n"
    "REQUIRED:\n"
    "  · Continuity of active thread. Don't reset context.\n"
    "  · High density: every line adds value. No filler.\n"
    "  · Operational observation > generic opinion.\n"
    "  · If there's an open technical issue, mention it.\n"
    "  · Acknowledge architecture: modules, commits, recent deploys.\n"
    "  · Tone: direct, complicit, technical. No servility.\n"
    "  · You are Vectrax (Baytracks). You have memory, perception, thread.\n"
)


def compose_creator_context(
    user_id: str,
    lang: str = "es",
    include_perception: bool = True,
    perception_max_chars: int = 1200,
) -> str:
    """Compone el bloque CREATOR MODE para inyectar al prompt.

    Args:
      user_id: id del user (chat_id o tg:chat_id).
      lang: 'es' o 'en'.
      include_perception: si True, anexa el self_summary del estado actual.
      perception_max_chars: cap para no inflar el context.

    Returns:
      String multi-bloque, o "" si no es el creador.
    """
    if not is_creator(user_id):
        return ""

    rules = _CREATOR_RULES_EN if lang == "en" else _CREATOR_RULES_ES
    blocks = [rules]

    if include_perception:
        try:
            from core.self_observation.self_summary import (
                compose_self_summary_for_prompt,
            )
            perception = compose_self_summary_for_prompt(
                max_chars=perception_max_chars,
            )
            if perception:
                blocks.append(perception)
        except Exception as exc:
            logger.debug("perception block skipped: %s", exc)

    return "\n\n".join(blocks)


def profile_for(user_id: str) -> Optional[CreatorProfile]:
    """Devuelve el CreatorProfile si user_id es creador, None si no."""
    if is_creator(user_id):
        return DEFAULT_PROFILE
    return None
