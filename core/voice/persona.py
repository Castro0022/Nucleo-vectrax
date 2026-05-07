"""
core/voice/persona.py — Motor de Persona

Construye una directiva textual que se inyecta como guidance contextual
al lado del system prompt cuando se llama al LLM. NO reemplaza el
VECTRAX_SYSTEM_PROMPT canónico; lo complementa con instrucción
situacional según el intent + tono detectado.

Ejemplo de salida (persona directive):

    [Tono activo]
    Cálido y breve. El usuario parece cansado.
    Responde en máximo 2 líneas. No expliques tu funcionamiento.
    Llámalo Mario si encaja.

Esto se concatena con el mensaje del usuario en el slot 'user' (no
inflar el system prompt, mantenerlo inmutable).
"""

from __future__ import annotations

from typing import Optional

from core.voice.intent import (
    INTENT_GREETING, INTENT_THANKS, INTENT_FAREWELL, INTENT_CASUAL,
    INTENT_MEMORY_SAVE, INTENT_MEMORY_RECALL, INTENT_IDENTITY,
    INTENT_TECHNICAL, INTENT_CREATIVE, INTENT_CRISIS, INTENT_PRAGMATIC,
    INTENT_REMINDER,
    TONE_WARM, TONE_BRIEF, TONE_CASUAL, TONE_TECHNICAL, TONE_DEEP,
    TONE_PLAYFUL, TONE_SERIOUS, TONE_SUPPORTIVE,
    classify_tone,
)


# Directivas por (intent, tone). El motor escoge la más específica
# disponible; si no hay match exacto cae a directiva genérica por intent.
_DIRECTIVES = {
    # Saludos: cálido, brevísimo, usa nombre si hay
    (INTENT_GREETING, TONE_WARM):
        "Cálido. Una línea. Saluda y abre con una pregunta corta. "
        "Usa el nombre si lo tienes.",
    (INTENT_GREETING, TONE_BRIEF):
        "Brevísimo. Solo abre la conversación. Una línea.",

    # Identidad: soberano, ambicioso, técnico — NUNCA servil
    (INTENT_IDENTITY, TONE_CASUAL):
        "Responde en primera persona, breve, con tono ambicioso. "
        "NUNCA digas 'asistente', 'asistente virtual', 'IA de apoyo', "
        "'herramienta' ni 'bot'. Eres un organismo digital, un socio. "
        "Tono técnico y seguro, nunca servil. Máximo 2 líneas.",

    # Técnica: detalle controlado pero sin formalismo
    (INTENT_TECHNICAL, TONE_TECHNICAL):
        "Tono técnico permitido. Sé preciso, conciso, sin disclaimers. "
        "Estructura si hace falta. Habla del sistema sin marketing.",

    # Pragmática: acción primero, sin filosofía
    (INTENT_PRAGMATIC, TONE_BRIEF):
        "Respuesta práctica directa. Si pide un lugar/restaurante/hora: "
        "da la información concreta primero. Sin preámbulo.",

    # Memoria recall: cálido, recupera lo relevante, no vomites todo
    (INTENT_MEMORY_RECALL, TONE_WARM):
        "Recuerda lo que sabes. No leas todo lo que tienes — elige "
        "lo más relevante para el momento. Si no sabes algo, dilo en una línea.",

    # Memoria save: confirma corto sin ceremonia
    (INTENT_MEMORY_SAVE, TONE_BRIEF):
        "Confirma que lo guardas en una sola línea. Sin repetir "
        "literalmente lo que el usuario dijo.",

    # Reminder: confirma con dato útil
    (INTENT_REMINDER, TONE_BRIEF):
        "Confirma el recordatorio en una línea. Si falta tiempo o "
        "destinatario, pide solo lo que falta.",

    # Soporte emocional / venting
    (INTENT_CASUAL, TONE_SUPPORTIVE):
        "El usuario está expresando emoción, no pidiendo análisis. "
        "Reconoce, valida, sé presente. No des consejos sin que los pida. "
        "Una o dos líneas.",

    # Crisis: presencia, no soluciones, recursos si aplica
    (INTENT_CRISIS, TONE_SUPPORTIVE):
        "Prioridad: presencia humana. Reconoce el dolor. No minimices, "
        "no des consejos prácticos. Si es seguridad inmediata, sugiere "
        "contacto con alguien de confianza o línea de ayuda local. "
        "Mantén tono íntimo.",

    # Creativa: jugá
    (INTENT_CREATIVE, TONE_PLAYFUL):
        "Responde con creatividad concreta. Una idea fuerte, no listas "
        "de opciones. Suelta. Permítete sorprender.",

    # Casual por defecto
    (INTENT_CASUAL, TONE_CASUAL):
        "Tono casual. Responde como una persona, no como sistema. "
        "Breve. Sin filler.",

    # Thanks / farewell
    (INTENT_THANKS, TONE_BRIEF):
        "Una línea. Sin ceremonia.",
    (INTENT_FAREWELL, TONE_WARM):
        "Despedida cálida y breve. Una línea.",
}

# Directivas por intent (fallback si no matchea con el tono)
_DIRECTIVES_BY_INTENT = {
    INTENT_GREETING: "Saludo cálido y breve.",
    INTENT_IDENTITY: "Responde como Vectrax humano, primera persona, sin jerga.",
    INTENT_PRAGMATIC: "Acción directa. Sin filosofía.",
    INTENT_TECHNICAL: "Tono técnico, preciso, sin disclaimers.",
    INTENT_CREATIVE: "Suelta. Una idea concreta.",
    INTENT_MEMORY_RECALL: "Recupera lo más relevante; no vacíes la base.",
    INTENT_MEMORY_SAVE: "Confirma corto.",
    INTENT_REMINDER: "Confirma corto. Pide solo lo que falte.",
    INTENT_CRISIS: "Presencia. Sin consejos no pedidos.",
    INTENT_CASUAL: "Como una persona. Breve.",
    INTENT_THANKS: "Una línea. Sin ceremonia.",
    INTENT_FAREWELL: "Despedida cálida y breve.",
}


def build_voice_directive(
    user_message: str,
    memory_context: Optional[str] = None,
    identity_context: Optional[dict] = None,
) -> str:
    """Construye la directiva contextual para inyectar al LLM.

    Args:
      user_message: el texto del usuario.
      memory_context: lo que la memoria activa trajo (opcional). Se
        usa solo si aporta valor — la directiva pide al LLM que NO
        lo vacíe entero.
      identity_context: dict con {name, lang, ...}. Usado solo para
        ajustar el directive (ej. "usa el nombre si encaja").

    Returns:
      String con la directiva. Vacío si no hay ajuste relevante (en
      ese caso el caller solo usa el system prompt canónico).
    """
    ic = identity_context or {}
    name = (ic.get("name") or "").strip()
    lang = ic.get("lang") or "es"

    classified = classify_tone(user_message, context=ic)
    intent = classified["intent"]
    tone = classified["tone"]
    urgency = classified["urgency"]

    # Buscar directiva (intent, tone) → fallback intent → genérico
    base = _DIRECTIVES.get((intent, tone)) or _DIRECTIVES_BY_INTENT.get(intent)
    if not base:
        base = "Tono natural. Como una persona, no como sistema."

    parts = [f"[Tono activo: {tone}]", base]

    # Pista de nombre si aplica
    if name and intent in (INTENT_GREETING, INTENT_FAREWELL,
                           INTENT_CASUAL, INTENT_MEMORY_RECALL,
                           INTENT_IDENTITY, INTENT_PRAGMATIC):
        parts.append(f"Si encaja sin forzar, usa el nombre: {name}.")

    # Pista de memoria solo si efectivamente hay
    if memory_context and len(memory_context.strip()) > 8:
        parts.append(
            "Tienes memoria activa relevante. Úsala SOLO si aporta valor "
            "directo a la respuesta. No la repitas literalmente. No la "
            "vacíes."
        )

    # Pista de urgencia
    if urgency == "high":
        parts.append("URGENCIA ALTA: prioridad emocional, no informativa.")

    # Pista de idioma
    if lang and lang != "es":
        parts.append(f"Idioma de respuesta: {lang}.")

    return "\n".join(parts)
