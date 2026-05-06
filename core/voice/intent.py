"""
core/voice/intent.py — Motor de Intención + Tono

Decide qué tipo de mensaje envió el usuario y cómo debe sonar Vectrax al
responder. Heurística determinista por regex en v1; ampliable a LLM mini
en v2 si las reglas no cubren un caso.

Las clases Intent/Tone son strings (no Enums) para serialización trivial
y comparación directa con literales.
"""

from __future__ import annotations

import re
from typing import Dict, Any, Optional

# ---------------------------------------------------------------------------
# Tipos / constantes
# ---------------------------------------------------------------------------

# Intent: qué quiere el usuario.
INTENT_GREETING = "greeting"
INTENT_THANKS = "thanks"
INTENT_FAREWELL = "farewell"
INTENT_CASUAL = "casual"
INTENT_MEMORY_SAVE = "memory_save"
INTENT_MEMORY_RECALL = "memory_recall"
INTENT_IDENTITY = "identity"
INTENT_TECHNICAL = "technical"
INTENT_CREATIVE = "creative"
INTENT_CRISIS = "crisis"
INTENT_PRAGMATIC = "pragmatic"
INTENT_REMINDER = "reminder"

ALL_INTENTS = (
    INTENT_GREETING, INTENT_THANKS, INTENT_FAREWELL, INTENT_CASUAL,
    INTENT_MEMORY_SAVE, INTENT_MEMORY_RECALL, INTENT_IDENTITY,
    INTENT_TECHNICAL, INTENT_CREATIVE, INTENT_CRISIS, INTENT_PRAGMATIC,
    INTENT_REMINDER,
)

# Tone: cómo debe sonar la respuesta.
TONE_WARM = "warm"
TONE_BRIEF = "brief"
TONE_CASUAL = "casual"
TONE_TECHNICAL = "technical"
TONE_DEEP = "deep"
TONE_PLAYFUL = "playful"
TONE_SERIOUS = "serious"
TONE_SUPPORTIVE = "supportive"

ALL_TONES = (
    TONE_WARM, TONE_BRIEF, TONE_CASUAL, TONE_TECHNICAL,
    TONE_DEEP, TONE_PLAYFUL, TONE_SERIOUS, TONE_SUPPORTIVE,
)

# ---------------------------------------------------------------------------
# Patterns por intent (ES + EN principalmente; FR/IT/DE/PT cobertura parcial)
# ---------------------------------------------------------------------------

_GREETING = re.compile(
    r"^\s*(?:hola|hi|hey|hello|buenas?|buenos?\s+d[ií]as?|buenas\s+tardes?"
    r"|buenas\s+noches?|que tal|qu[eé] tal|saludos|sup|yo|howdy|bonjour"
    r"|salut|ciao|olá|ola|oi|moin|hallo)\b[!.,\s]*[a-z]{0,12}\s*\??$",
    re.IGNORECASE,
)

_THANKS = re.compile(
    r"^\s*(?:gracias|muchas?\s+gracias|mil\s+gracias|thanks|thank\s+you"
    r"|thx|merci(?:\s+beaucoup)?|grazie(?:\s+mille)?|danke(?:\s+sch[oö]n)?"
    r"|obrigad[oa])\b[!.,\s]*$",
    re.IGNORECASE,
)

_FAREWELL = re.compile(
    r"^\s*(?:chao|adi[oó]s|hasta\s+luego|nos\s+vemos|bye|goodbye"
    r"|see\s+you|au\s+revoir|arrivederci|tschüss|tchau|chau|doei)"
    r"\b[!.,\s]*$",
    re.IGNORECASE,
)

# Identity: quién/qué es Vectrax (no quién es el usuario)
_IDENTITY = re.compile(
    r"(?:qui[eé]n\s+eres(?:\s+t[úu])?"
    r"|qu[eé]\s+(?:eres|es\s+vectrax|hace\s+vectrax|puedes\s+hacer"
    r"|sabes\s+hacer|ofrece\s+vectrax)"
    r"|c[oó]mo\s+te\s+llamas"
    r"|c[oó]mo\s+funcion(?:as|a\s+vectrax)"
    r"|para\s+qu[eé]\s+sirves"
    r"|cu[eé]ntame\s+(?:sobre|de|acerca(?:\s+de)?)\s+vectrax"
    r"|h[aá]blame\s+(?:sobre|de)\s+vectrax"
    r"|describe\s+vectrax"
    r"|descr[ií]bete"
    r"|qui[eé]n\s+(?:es\s+)?(?:tu|el)\s+creador"
    r"|qui[eé]n\s+te\s+(?:cre[oó]|hizo|diseñ[oó])"
    r"|tu\s+universo"
    r"|cu[aá]les\s+son\s+tus\s+capacidades"
    r"|what\s+(?:are|is)\s+(?:you|vectrax)"
    r"|who\s+(?:are\s+you|created\s+you|made\s+you|built\s+you)"
    r"|what\s+can\s+you\s+do"
    r"|how\s+do\s+you\s+work"
    r"|cosa\s+sei|was\s+bist\s+du)",
    re.IGNORECASE,
)

# Memory save: pedir recordar algo
_MEMORY_SAVE = re.compile(
    r"\b(?:guarda|recuerda|recu[eé]rdame|anota|anota\s+que|toma\s+nota"
    r"|gu[aá]rdate|memoriza|me\s+gusta|mi\s+favorito"
    r"|save|remember|note|memorize|store)\b",
    re.IGNORECASE,
)

# Memory recall: preguntar qué sabe el bot
_MEMORY_RECALL = re.compile(
    r"\b(?:qu[eé]\s+sabes\s+de\s+m[ií]"
    r"|qu[eé]\s+recuerdas(?:\s+de\s+m[ií])?"
    r"|me\s+conoces"
    r"|sabes\s+qui[eé]n\s+soy"
    r"|qui[eé]n\s+soy"
    r"|c[oó]mo\s+me\s+llamo"
    r"|cu[aá]l\s+es\s+mi\s+nombre"
    r"|what\s+do\s+you\s+know\s+about\s+me"
    r"|do\s+you\s+remember\s+me"
    r"|who\s+am\s+i)\b",
    re.IGNORECASE,
)

# Reminder explícito (programar acción futura)
# Matchea "recuérdame llamar/hacer/ir..." o "recordarme que...".
# Importante: precede a _MEMORY_SAVE en classify_intent (reminder es
# más específico — implica acción futura, no almacenamiento de hecho).
_REMINDER = re.compile(
    r"\b(?:"
    r"(?:recu[eé]rdame|recordar(?:me)?|recu[eé]rda(?:te)?\s+de)"
    r"\s+(?:que|de|llamar|hacer|ir|escribir|enviar|comprar|pasar|en|a|al)"
    r"|al[aá]rmame|av[ií]same|despi[eé]rtame"
    r"|remind\s+me|alert\s+me|wake\s+me"
    r")",
    re.IGNORECASE,
)

# Technical: el usuario pregunta por arquitectura, código, debugging, etc.
_TECHNICAL = re.compile(
    r"\b(?:expl[ií]ca(?:me)?\s+(?:la|tu|el)?\s*(?:arquitectura|c[oó]digo|m[oó]dulo|sistema|pipeline|api|endpoint|funci[oó]n|algoritmo|estructura)"
    r"|c[oó]mo\s+est[aá]\s+hecho"
    r"|d[oó]nde\s+est[aá]\s+(?:el|la)\s+(?:c[oó]digo|m[oó]dulo)"
    r"|qu[eé]\s+(?:tecnolog[ií]a|stack|framework|biblioteca)\s+usas"
    r"|debug|stack\s*trace|error\s+message"
    r"|architecture|codebase|source\s+code|implementation)\b",
    re.IGNORECASE,
)

# Creative: brainstorm, escritura, diseño
_CREATIVE = re.compile(
    r"\b(?:escr[ií]be(?:me)?|dise[ñn]a(?:me)?|invent(?:a|ame)|imagina"
    r"|brainstorm|crea(?:me)?\s+(?:un|una|algo)"
    r"|write\s+(?:a|me|something)|draft|design|imagine|invent)\b",
    re.IGNORECASE,
)

# Crisis: signos de angustia / emergencia emocional
_CRISIS = re.compile(
    r"\b(?:no\s+(?:puedo\s+m[aá]s|aguanto|s[eé]\s+qu[eé]\s+hacer)"
    r"|me\s+quiero\s+(?:morir|ir|matar)"
    r"|estoy\s+(?:muy\s+mal|fatal|destruid[oa]|hundi[dt]o|deprimid[oa])"
    r"|(?:i\s+(?:can'?t|cannot)\s+(?:take\s+it|go\s+on|do\s+this))"
    r"|(?:i\s+want\s+to\s+(?:die|kill\s+myself|disappear))"
    r"|(?:suicid|kill\s+myself))\b",
    re.IGNORECASE,
)

# Pragmatic: pedir cosas concretas (restaurante, lugar, hora, fecha, comprar, etc.)
_PRAGMATIC = re.compile(
    r"\b(?:tengo\s+hambre|d[ií]me\s+un\s+(?:restaurante|lugar|sitio)"
    r"|donde\s+(?:puedo|hay|encuentro)|qu[eé]\s+(?:hora|d[ií]a|fecha)"
    r"|c[oó]mo\s+(?:llego|voy)\s+a"
    r"|busca(?:me)?\s+(?:un|una)"
    r"|encu[eé]ntra(?:me)?"
    r"|i'?m\s+hungry|find\s+me\s+a|where\s+can\s+i|how\s+do\s+i\s+get\s+to"
    r"|what\s+time|what\s+day|what'?s\s+the\s+date)\b",
    re.IGNORECASE,
)

# Support / venting: el usuario expresa estado emocional sin crisis aguda
_SUPPORT = re.compile(
    r"\b(?:estoy\s+(?:cansad[oa]|triste|ans(?:ios[oa]|iedad)|agot[oa]"
    r"|frustrad[oa]|enojad[oa]|preocupad[oa]|abrumad[oa]))"
    r"|(?:i\s+(?:am|'m)\s+(?:tired|sad|anxious|exhausted|frustrated|stressed))"
    r"|(?:me\s+siento\s+(?:mal|raro|cansad[oa]|sol[oa]))",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_intent(user_message: str) -> str:
    """Devuelve el intent estimado del mensaje (no levanta nunca).

    Orden de precedencia (mas especifico → menos especifico):
      crisis > reminder > memory_recall > memory_save > identity
      > technical > creative > pragmatic > support → casual
      > greeting/thanks/farewell (capturados al inicio)

    El orden importa: 'tengo hambre, dime un restaurante' es PRAGMATIC,
    no CASUAL. 'recuerda que me gusta el café' es MEMORY_SAVE, no
    REMINDER (no hay tiempo proyectado).
    """
    if not user_message or not user_message.strip():
        return INTENT_CASUAL
    text = user_message.strip()

    # 1. saludo / agradecimiento / despedida — patrones cortos al inicio
    if _GREETING.match(text):
        return INTENT_GREETING
    if _THANKS.match(text):
        return INTENT_THANKS
    if _FAREWELL.match(text):
        return INTENT_FAREWELL

    # 2. crisis — máxima precedencia, override sobre todo
    if _CRISIS.search(text):
        return INTENT_CRISIS

    # 3. reminder explícito
    if _REMINDER.search(text):
        return INTENT_REMINDER

    # 4. memoria: recall antes que save (recall es más específico)
    if _MEMORY_RECALL.search(text):
        return INTENT_MEMORY_RECALL
    if _MEMORY_SAVE.search(text):
        return INTENT_MEMORY_SAVE

    # 5. identidad
    if _IDENTITY.search(text):
        return INTENT_IDENTITY

    # 6. técnica
    if _TECHNICAL.search(text):
        return INTENT_TECHNICAL

    # 7. creativa
    if _CREATIVE.search(text):
        return INTENT_CREATIVE

    # 8. pragmática (pide algo concreto del mundo real)
    if _PRAGMATIC.search(text):
        return INTENT_PRAGMATIC

    # 9. soporte emocional
    if _SUPPORT.search(text):
        # Si es soporte sin crisis, lo tratamos como casual con tono
        # supportive (lo decide classify_tone, no aquí).
        return INTENT_CASUAL

    # 10. casual por defecto
    return INTENT_CASUAL


def classify_tone(
    user_message: str,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Devuelve un dict {intent, tone, register, urgency}.

    'context' opcional puede incluir 'lang', 'has_name', 'time_of_day',
    'recent_user_emotion', etc. Se usa solo si afina la decisión.
    """
    ctx = context or {}
    intent = classify_intent(user_message or "")

    # Tono base por intent
    tone_by_intent = {
        INTENT_GREETING: TONE_WARM,
        INTENT_THANKS: TONE_BRIEF,
        INTENT_FAREWELL: TONE_WARM,
        INTENT_IDENTITY: TONE_CASUAL,
        INTENT_MEMORY_SAVE: TONE_BRIEF,
        INTENT_MEMORY_RECALL: TONE_WARM,
        INTENT_REMINDER: TONE_BRIEF,
        INTENT_TECHNICAL: TONE_TECHNICAL,
        INTENT_CREATIVE: TONE_PLAYFUL,
        INTENT_CRISIS: TONE_SUPPORTIVE,
        INTENT_PRAGMATIC: TONE_BRIEF,
        INTENT_CASUAL: TONE_CASUAL,
    }
    tone = tone_by_intent.get(intent, TONE_CASUAL)

    # Ajustes contextuales
    text = (user_message or "").strip()

    # Soporte emocional sin crisis aguda → eleva a supportive
    if intent == INTENT_CASUAL and _SUPPORT.search(text):
        tone = TONE_SUPPORTIVE

    # Si pregunta técnica viene con palabras emocionales, mantener supportive
    if intent == INTENT_TECHNICAL and _SUPPORT.search(text):
        tone = TONE_SUPPORTIVE

    # Mensaje muy corto (<= 4 palabras) y no técnico → fuerza brief
    if (
        intent not in (INTENT_TECHNICAL, INTENT_CREATIVE, INTENT_CRISIS)
        and len(text.split()) <= 4
    ):
        if tone == TONE_CASUAL:
            tone = TONE_BRIEF

    register = "informal"
    urgency = "low"
    if intent == INTENT_CRISIS:
        urgency = "high"
        register = "intimate"
    elif intent in (INTENT_REMINDER, INTENT_PRAGMATIC):
        urgency = "med"

    return {
        "intent": intent,
        "tone": tone,
        "register": register,
        "urgency": urgency,
    }


# Alias para compatibilidad con el contrato del usuario
Intent = str
Tone = str
