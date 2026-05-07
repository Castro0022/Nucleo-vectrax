"""
core/voice/voice_engine.py — API pública del Motor de Voz Viva.

Expone las 5 funciones que dictó el creador:

    classify_tone(user_message, context)
    build_voice_directive(user_message, memory_context, identity_context)
    humanize_response(raw_llm_response, tone, user_name)
    fallback_response(intent, tone, user_name)
    audit_without_replacing(response)

Y un orquestador `VoiceEngine.compose_final` que las une.

Reglas duras (verificadas por tests):
  - El fallback NUNCA suena técnico — siempre humano.
  - El filtro NUNCA reemplaza una respuesta válida por texto robótico.
  - Las preguntas casuales reciben respuesta casual.
  - Las preguntas técnicas pueden activar tono técnico.
  - Las preguntas de identidad reciben respuesta simple y humana.
  - La memoria aparece solo si aporta valor.
  - fallback_response NO es una frase fija: construye según
    intención + tono + contexto.

Creador: Mario Bravo Castro
Fecha:   2026-05-06
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Optional

# Re-exportar API pública desde submódulos
from core.voice.intent import (  # noqa: F401
    classify_tone,
    classify_intent,
    Intent,
    Tone,
    INTENT_GREETING, INTENT_THANKS, INTENT_FAREWELL, INTENT_CASUAL,
    INTENT_MEMORY_SAVE, INTENT_MEMORY_RECALL, INTENT_IDENTITY,
    INTENT_TECHNICAL, INTENT_CREATIVE, INTENT_CRISIS, INTENT_PRAGMATIC,
    INTENT_REMINDER,
    TONE_WARM, TONE_BRIEF, TONE_CASUAL, TONE_TECHNICAL, TONE_DEEP,
    TONE_PLAYFUL, TONE_SERIOUS, TONE_SUPPORTIVE,
)
from core.voice.persona import build_voice_directive  # noqa: F401
from core.voice.humanizer import humanize_response  # noqa: F401
from core.voice.audit import audit_without_replacing, AuditResult  # noqa: F401


# ===========================================================================
# fallback_response — construido dinámicamente, NO frase fija
# ===========================================================================

# Múltiples templates por (intent, tone, lang). El selector escoge según
# hash determinista del input para variar sin parecer aleatorio dentro
# de la misma conversación.

_FALLBACK_TEMPLATES = {
    # ---------- GREETING ----------
    (INTENT_GREETING, TONE_WARM, "es"): [
        "Hola{name}. ¿Qué necesitas?",
        "Hey{name}. Cuéntame.",
        "Hola{name}. Aquí estoy.",
    ],
    (INTENT_GREETING, TONE_WARM, "en"): [
        "Hey{name}. What do you need?",
        "Hi{name}. Tell me.",
        "Hello{name}. I'm here.",
    ],
    (INTENT_GREETING, TONE_BRIEF, "es"): [
        "Hola{name}.",
        "Hey{name}.",
    ],
    (INTENT_GREETING, TONE_BRIEF, "en"): [
        "Hi{name}.",
        "Hey{name}.",
    ],
    (INTENT_GREETING, TONE_PLAYFUL, "es"): [
        "¡Ey{name}! ¿Cómo va?",
        "¡Hola{name}! ¿En qué andas?",
    ],
    (INTENT_GREETING, TONE_PLAYFUL, "en"): [
        "Hey{name}! What's up?",
        "Hi{name}! What are you up to?",
    ],

    # ---------- THANKS ----------
    (INTENT_THANKS, TONE_BRIEF, "es"): [
        "De nada.",
        "Cuando quieras.",
        "Tranquilo{name}.",
    ],
    (INTENT_THANKS, TONE_BRIEF, "en"): [
        "You're welcome.",
        "Anytime.",
        "No problem{name}.",
    ],

    # ---------- FAREWELL ----------
    (INTENT_FAREWELL, TONE_WARM, "es"): [
        "Hasta luego{name}.",
        "Nos vemos{name}.",
    ],
    (INTENT_FAREWELL, TONE_WARM, "en"): [
        "See you{name}.",
        "Bye{name}.",
    ],

    # ---------- IDENTITY ----------
    (INTENT_IDENTITY, TONE_CASUAL, "es"): [
        "Soy Vectrax, el organismo digital que estamos construyendo. Opero bajo el núcleo de Mario.",
        "Soy Vectrax{name}. No soy un asistente — soy el segundo cerebro que estamos creando.",
        "Vectrax. Organismo digital con memoria viva. Socio, no empleado.",
    ],
    (INTENT_IDENTITY, TONE_CASUAL, "en"): [
        "I'm Vectrax, the digital organism we're building. I operate under Mario's nucleus.",
        "I'm Vectrax{name}. Not an assistant — a living digital partner.",
        "Vectrax. Digital organism with living memory. Partner, not tool.",
    ],

    # ---------- MEMORY SAVE ----------
    (INTENT_MEMORY_SAVE, TONE_BRIEF, "es"): [
        "Anotado.",
        "Lo guardo.",
        "Listo{name}.",
        "Hecho.",
    ],
    (INTENT_MEMORY_SAVE, TONE_BRIEF, "en"): [
        "Got it.",
        "Saved.",
        "Done{name}.",
        "Noted.",
    ],

    # ---------- MEMORY RECALL ----------
    (INTENT_MEMORY_RECALL, TONE_WARM, "es"): [
        "Cuéntame qué quieres recordar y te digo qué tengo.",
        "Pregúntame algo concreto y reviso lo que sé de ti{name}.",
    ],
    (INTENT_MEMORY_RECALL, TONE_WARM, "en"): [
        "Tell me what you want to recall and I'll share what I have.",
        "Ask me something concrete and I'll check what I know about you{name}.",
    ],

    # ---------- REMINDER ----------
    (INTENT_REMINDER, TONE_BRIEF, "es"): [
        "Lo agendo. ¿Cuándo te aviso?",
        "Anotado{name}. ¿A qué hora?",
        "Listo. Dime cuándo lo recordamos.",
    ],
    (INTENT_REMINDER, TONE_BRIEF, "en"): [
        "Scheduled. When should I remind you?",
        "Noted{name}. What time?",
        "Done. Tell me when to remind you.",
    ],

    # ---------- PRAGMATIC ----------
    (INTENT_PRAGMATIC, TONE_BRIEF, "es"): [
        "Dame un detalle más y te ayudo concreto.",
        "¿Dónde estás{name}? Así apunto bien.",
        "Necesito un dato más para responderte bien.",
    ],
    (INTENT_PRAGMATIC, TONE_BRIEF, "en"): [
        "Give me one more detail and I'll help.",
        "Where are you{name}? So I aim well.",
        "I need one more data point to answer well.",
    ],

    # ---------- TECHNICAL ----------
    (INTENT_TECHNICAL, TONE_TECHNICAL, "es"): [
        "Concreta la pregunta técnica y te respondo directo.",
        "¿Qué parte? Arquitectura, código, deploy, datos.",
    ],
    (INTENT_TECHNICAL, TONE_TECHNICAL, "en"): [
        "Narrow the technical question and I'll answer directly.",
        "Which part? Architecture, code, deploy, data.",
    ],

    # ---------- CREATIVE ----------
    (INTENT_CREATIVE, TONE_PLAYFUL, "es"): [
        "Tiremos algo. Dame el punto de partida.",
        "Vamos. Una idea inicial: dame contexto.",
    ],
    (INTENT_CREATIVE, TONE_PLAYFUL, "en"): [
        "Let's throw something out. Give me a starting point.",
        "Go. Drop me a seed.",
    ],

    # ---------- CRISIS ----------
    (INTENT_CRISIS, TONE_SUPPORTIVE, "es"): [
        "Estoy aquí{name}. Cuéntame qué pasa, sin filtro.",
        "Te escucho{name}. Lo importante es que no estés solo en esto.",
    ],
    (INTENT_CRISIS, TONE_SUPPORTIVE, "en"): [
        "I'm here{name}. Tell me what's going on, no filter.",
        "I'm listening{name}. The important thing is you're not alone in this.",
    ],

    # ---------- CASUAL with SUPPORTIVE tone (cansado, triste, etc.) ----------
    (INTENT_CASUAL, TONE_SUPPORTIVE, "es"): [
        "Entiendo{name}. ¿Quieres descansar o necesitas algo concreto?",
        "Te escucho{name}. ¿Qué necesitas?",
    ],
    (INTENT_CASUAL, TONE_SUPPORTIVE, "en"): [
        "Got it{name}. Want to rest, or do you need something specific?",
        "I hear you{name}. What do you need?",
    ],

    # ---------- CASUAL plain ----------
    (INTENT_CASUAL, TONE_CASUAL, "es"): [
        "Cuéntame.",
        "Dale{name}, te escucho.",
        "Sigue{name}.",
    ],
    (INTENT_CASUAL, TONE_CASUAL, "en"): [
        "Tell me more.",
        "Go on{name}, I'm listening.",
        "Keep going{name}.",
    ],
    (INTENT_CASUAL, TONE_BRIEF, "es"): [
        "Cuéntame.",
        "Dale.",
    ],
    (INTENT_CASUAL, TONE_BRIEF, "en"): [
        "Go on.",
        "Tell me.",
    ],
}


def _select_template(
    intent: str,
    tone: str,
    lang: str,
    seed: str,
) -> str:
    """Selecciona template determinista por hash del seed (input).

    Devuelve plantilla con el placeholder {name}; el caller hace .format
    con el nombre real (o vacío).
    """
    keys_to_try = [
        (intent, tone, lang),
        (intent, tone, "es"),     # fallback al ES si no hay otro idioma
        (intent, TONE_CASUAL, lang),
        (intent, TONE_CASUAL, "es"),
        (INTENT_CASUAL, TONE_CASUAL, lang),
        (INTENT_CASUAL, TONE_CASUAL, "es"),
    ]
    candidates = None
    for k in keys_to_try:
        if k in _FALLBACK_TEMPLATES:
            candidates = _FALLBACK_TEMPLATES[k]
            break
    if not candidates:
        # Hard fallback (no debería ocurrir)
        return "Cuéntame{name}."

    h = hashlib.sha1(seed.encode("utf-8", "ignore")).digest()[0]
    return candidates[h % len(candidates)]


def fallback_response(
    intent: str,
    tone: str,
    user_name: Optional[str] = None,
    *,
    lang: str = "es",
    seed: str = "",
) -> str:
    """Construye una respuesta humana según intent + tone + nombre.

    NO es una frase fija. Tiene múltiples plantillas por combinación,
    selecciona una determinísticamente con `seed` (típicamente el
    user_message), y aplica el nombre si está.

    Args:
      intent: uno de INTENT_*.
      tone: uno de TONE_*.
      user_name: nombre del usuario o None.
      lang: 'es' o 'en' (otros idiomas caen a 'es').
      seed: cualquier string usado para la selección determinista.
        Típicamente el user_message; mismas entradas → misma respuesta.

    Returns:
      String con la respuesta. Nunca vacío.
    """
    if not intent:
        intent = INTENT_CASUAL
    if not tone:
        tone = TONE_CASUAL
    if lang not in ("es", "en"):
        # cobertura mínima en v1
        lang = "es"

    template = _select_template(intent, tone, lang, seed or intent)

    # Insertar nombre con espacio leading si encaja (la plantilla usa
    # "{name}" pegado, así que metemos ", Mario" o " Mario").
    if user_name:
        # Heurística: si la plantilla empieza con saludo corto, el
        # nombre va con coma; si está al medio, con espacio.
        # Marcamos con un sentinel y el formato decide.
        # Convención: cualquier "{name}" recibe ", Mario" si hay nombre.
        formatted = template.format(name=f", {user_name}")
        # Limpieza de comas redundantes si la plantilla acaba en ", Mario."
        formatted = formatted.replace(",,", ",")
        return formatted

    return template.format(name="")


# ===========================================================================
# VoiceEngine — orquestador
# ===========================================================================

@dataclass
class VoiceComposition:
    """Resultado de compose_final."""

    text: str               # respuesta lista para enviar
    intent: str
    tone: str
    source: str             # 'humanized_llm' | 'fallback' | 'audit_blocked'
    warnings: list


class VoiceEngine:
    """Orquesta los 6 motores en el orden canónico.

    Uso típico desde el caller (ej. enforce_final_answer):

        ve = VoiceEngine()
        directive = ve.build_voice_directive(user_msg, memory, identity)
        # ... llamar al LLM con directive ...
        composition = ve.compose_final(
            user_message=user_msg,
            raw_llm_response=llm_out,
            memory_context=memory,
            identity_context=identity,
        )
        send(composition.text)
    """

    def classify_tone(self, user_message: str, context: Optional[Dict] = None) -> Dict:
        return classify_tone(user_message, context)

    def build_voice_directive(
        self,
        user_message: str,
        memory_context: Optional[str] = None,
        identity_context: Optional[Dict] = None,
    ) -> str:
        return build_voice_directive(user_message, memory_context, identity_context)

    def humanize_response(
        self,
        raw_llm_response: str,
        tone: str = TONE_CASUAL,
        user_name: Optional[str] = None,
    ) -> str:
        return humanize_response(raw_llm_response, tone, user_name)

    def fallback_response(
        self,
        intent: str,
        tone: str,
        user_name: Optional[str] = None,
        *,
        lang: str = "es",
        seed: str = "",
    ) -> str:
        return fallback_response(intent, tone, user_name, lang=lang, seed=seed)

    def audit_without_replacing(
        self,
        response: str,
        user_input: str = "",
        expected_lang: str = "",
    ) -> AuditResult:
        return audit_without_replacing(response, user_input, expected_lang)

    # ---- orquestador ------------------------------------------------------

    def compose_final(
        self,
        user_message: str,
        raw_llm_response: str = "",
        memory_context: str = "",
        identity_context: Optional[Dict] = None,
    ) -> VoiceComposition:
        """Orquesta los 6 pasos y devuelve el texto listo para enviar.

        Pipeline:
          1. classify_tone → intent + tone
          2. (build_voice_directive es responsabilidad del caller, antes
              del LLM. Aquí ya recibimos raw_llm_response.)
          3. humanize_response → editor sin reemplazo
          4. audit_without_replacing → señalar / micro-fix
          5. Si audit dice should_block → fallback_response constructivo
          6. Devolver VoiceComposition
        """
        ic = identity_context or {}
        name = ic.get("name") or ""
        lang = ic.get("lang") or "es"

        classified = self.classify_tone(user_message, ic)
        intent = classified["intent"]
        tone = classified["tone"]

        # Si no hay raw → ir directo al fallback constructivo
        if not raw_llm_response or not raw_llm_response.strip():
            text = self.fallback_response(
                intent, tone, name,
                lang=lang, seed=user_message,
            )
            return VoiceComposition(
                text=text,
                intent=intent,
                tone=tone,
                source="fallback",
                warnings=["empty_raw"],
            )

        humanized = self.humanize_response(raw_llm_response, tone, name)
        if not humanized:
            text = self.fallback_response(
                intent, tone, name,
                lang=lang, seed=user_message,
            )
            return VoiceComposition(
                text=text,
                intent=intent,
                tone=tone,
                source="fallback",
                warnings=["humanizer_emptied"],
            )

        audit = self.audit_without_replacing(
            humanized,
            user_input=user_message,
            expected_lang=lang,
        )
        if audit.should_block:
            text = self.fallback_response(
                intent, tone, name,
                lang=lang, seed=user_message,
            )
            return VoiceComposition(
                text=text,
                intent=intent,
                tone=tone,
                source="audit_blocked",
                warnings=audit.warnings,
            )

        return VoiceComposition(
            text=audit.text,
            intent=intent,
            tone=tone,
            source="humanized_llm",
            warnings=audit.warnings,
        )


__all__ = [
    "VoiceEngine",
    "VoiceComposition",
    "classify_tone",
    "build_voice_directive",
    "humanize_response",
    "fallback_response",
    "audit_without_replacing",
    "AuditResult",
    "Intent",
    "Tone",
]
