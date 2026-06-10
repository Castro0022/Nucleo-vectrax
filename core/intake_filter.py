"""
Vectrax Intake Filter — Triage de Entrada
============================================
Filtro unificado que evalúa cada dato entrante antes de procesarlo.

INPUT:
  - dato entrante (texto del usuario)
  - contexto (memoria cercana al núcleo)
  - estado actual del sistema (tier, módulos activos)

OUTPUT:
  - importancia: alta / media / baja
  - acción: guardar / ignorar / ejecutar / esperar

Se ejecuta ANTES de todo: antes del router, antes del LLM,
antes de la memoria. Es el primer filtro del sistema.

Creado: 2026-03-30
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger("vectrax.intake_filter")


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------

class Importance(str, Enum):
    HIGH = "alta"
    MEDIUM = "media"
    LOW = "baja"


class Action(str, Enum):
    EXECUTE = "ejecutar"    # Procesar con LLM / pipeline completo
    STORE = "guardar"       # Solo registrar en memoria, no responder
    IGNORE = "ignorar"      # Descartar (spam, ruido, duplicado)
    WAIT = "esperar"        # Acumular hasta convergencia


@dataclass
class IntakeResult:
    """Resultado del filtro de entrada."""
    importance: Importance
    action: Action
    reason: str
    bypass_llm: bool = False    # Si True, no pasa al LLM
    context_hint: str = ""      # Pista para el pipeline sobre qué contexto buscar

    def to_dict(self) -> dict:
        return {
            "importance": self.importance.value,
            "action": self.action.value,
            "reason": self.reason,
            "bypass_llm": self.bypass_llm,
        }


# ---------------------------------------------------------------------------
# Patrones de detección
# ---------------------------------------------------------------------------

# Comandos del sistema (alta prioridad, ejecución directa)
_SYSTEM_COMMANDS = re.compile(
    r"^(?:/?\b(?:upgrade|pro|status|tier|aprobar|approve)\b)",
    re.IGNORECASE,
)

# Datos personales (alta prioridad, guardar en memoria)
_PERSONAL_DATA = re.compile(
    r"(?:me llamo|mi nombre es|soy\s+\w+|my name is|i'?m\s+\w+|"
    r"tengo\s+\d+\s+a[ñn]os|vivo en|trabajo en|mi empresa|"
    r"mi correo|mi email|mi tel[eé]fono)",
    re.IGNORECASE,
)

# Preguntas de alta complejidad (requieren LLM + contexto)
_COMPLEX_QUERY = re.compile(
    r"(?:anal[ií]za|compara|expl[ií]ca.*diferencia|cu[aá]l es mejor|"
    r"ventajas y desventajas|pros? y contras?|estrategia para|"
    r"c[oó]mo puedo.*negocio|c[oó]mo ganar|c[oó]mo ahorrar|"
    r"plan para|analyze|compare|strategy for)",
    re.IGNORECASE,
)

# Preguntas factuales (búsqueda web / respuesta directa)
_FACTUAL_QUERY = re.compile(
    r"(?:qu[eé] es|who is|what is|cu[aá]ndo|when|d[oó]nde|where|"
    r"cu[aá]nto|how much|how many|precio de|weather|clima|"
    r"temperatura|lluvia|sol|calor|fr[ií]o|pron[oó]stico|"
    r"estado del tiempo|c[oó]mo est[aá] el tiempo|va a llover)",
    re.IGNORECASE,
)

# Preguntas semánticas (sin signo ? pero claramente una consulta)
_SEMANTIC_QUERY = re.compile(
    r"(?:qu[eé]\s+(?:tengo|hay|tiene|sabes|recuerdas|reuniones?|citas?))"
    r"|(?:a\s+qu[eé]\s+hora)"
    r"|(?:cu[aá]l\s+es\s+mi)"
    r"|(?:(?:te\s+)?(?:acuerdas|recuerdas)\s+(?:de|que))"
    r"|(?:(?:mi|la|el)\s+(?:reuni[oó]n|cita|junta|evento))"
    r"|(?:tengo\s+algo\s+(?:pendiente|agendado|programado))"
    r"|(?:(?:algo|alguna)\s+(?:para|pendiente))"
    r"|(?:dime|explica|describe|define|compara|resume|cu[eé]ntame)"
    r"|(?:me\s+conoces|sabes\s+qui[eé]n\s+soy|qui[eé]n\s+soy\s+para\s+ti)"
    r"|(?:qu[eé]\s+(?:sabes|recuerdas|tienes)\s+(?:de|sobre)\s+m[ií])"
    r"|(?:qui[eé]n\s+es\s+\w+)"
    r"|(?:qu[eé]\s+(?:te\s+)?(?:dije|cont[eé]|mencion[eé]))"
    r"|(?:(?:qui|que)\s+(?:est|es[- ]ce))",
    re.IGNORECASE,
)

# Tipo de contexto — calibra el tono de la respuesta
_CONTEXT_PRACTICAL = re.compile(
    r"\b(?:organiza|crea|haz|dam?e|genera|escribe|lista|resume|calcula|busca|encuentra|muestra)"
    r"|\b(?:create|make|generate|write|list|summarize|calculate|find|show)",
    re.IGNORECASE,
)
_CONTEXT_CREATIVE = re.compile(
    r"\b(?:idea[s]?|imagina|dise[ñn]a|inventa|propone|sugiere|brainstorm)"
    r"|\b(?:creativo|original|diferente|innovador|\bqui[eé]n ser[ií]a\b)",
    re.IGNORECASE,
)
_CONTEXT_TECHNICAL = re.compile(
    r"\b(?:c[oó]mo\s+funciona|por\s+qu[eé]|cu[aá]l\s+es\s+la\s+diferencia|implementa|arquitectura)"
    r"|\b(?:c[oó]digo|api|sql|python|error|debug|funci[oó]n|m[oó]dulo|clase|variable)",
    re.IGNORECASE,
)


def detect_context_type(text: str) -> str:
    """Detecta el tipo de contexto del mensaje para calibrar el tono."""
    t = text.strip()
    if _CONTEXT_TECHNICAL.search(t):
        return "technical"
    if _CONTEXT_CREATIVE.search(t):
        return "creative"
    if _CONTEXT_PRACTICAL.search(t):
        return "practical"
    return "conversational"


# Instrucciones de idioma (no son preguntas, pero requieren ejecución)
_LANGUAGE_INSTRUCTION = re.compile(
    r"(?:h[aá]blame|resp[oó]nde(?:me)?|parle[- ]?(?:moi)?|speak|talk|switch|cambia)"
    r"[\s-]+(?:solo\s+|only\s+|uniquement\s+)?(?:en\s+|in\s+|to\s+)?"
    r"(?:espa[nñ]ol|ingl[eé]s|franc[eé]s|english|french|spanish"
    r"|fran[cç]ais|italiano|alem[aá]n|portugu[eé]s|deutsch)",
    re.IGNORECASE,
)

# Mensajes sobre el propio sistema (Vectrax) — siempre ejecutar
_VECTRAX_REFERENCE = re.compile(
    r"(?:"
    r"\bvectrax\b"
    r"|\b(?:comercializar|monetizar|vender|escalar|lanzar)(?:\s+(?:el|esto|mi|nuestro))?\b"
    r"|\b(?:el|nuestro|este|mi)\s+(?:sistema|proyecto|producto|app)\b"
    r"|\b(?:siguiente|pr[oó]ximo)\s+paso\b"
    r"|\b(?:modelo\s+de\s+negocio|plan\s+comercial|estrategia\s+de)\b"
    r")",
    re.IGNORECASE,
)

# Intención de búsqueda / consulta online (imperativo sin ?)
# Captura: "dame noticias", "busca X", "muéstrame", "precio del X", etc.
_SEARCH_INTENT = re.compile(
    r"(?:"
    r"\b(?:dame|d[aá]me|busca|buscar|encuentra|encuéntrame|encuentrame)"
    r"|\b(?:mu[eé]strame|muestra|trae|cons[ií]gue|consigue|obt[eé]n|obtén)"
    r"|\b(?:noticias?|noticia|titulares?|headlines?)\b"
    r"|\b(?:clima|tiempo|temperatura|lluvia|sol|calor|fr[ií]o|pron[oó]stico|weather|forecast|humidity)\b"
    r"|\b(?:estado\s+del\s+tiempo|c[oó]mo\s+est[aá]\s+el\s+tiempo)\b"
    r"|\b(?:[uú]ltimas?\s+noticias?|lo\s+[uú]ltimo|hoy\s+en)"
    r"|\b(?:precio\s+(?:del?|de\s+la)|cotizaci[oó]n|cu[aá]nto\s+(?:vale|cuesta|está))"
    r"|\b(?:qu[eé]\s+pas[oó]|qu[eé]\s+ha\s+pasado|qu[eé]\s+hay\s+de)"
    r"|\b(?:dime|inform[aá]me|cu[eé]ntame|lista\s+de)"
    r")",
    re.IGNORECASE,
)

# Ruido / baja señal (expanded: conversational acks, single-word replies)
_NOISE = re.compile(
    r"^(?:"
    r"\.+|…+|\?+|!+"
    r"|jaja|haha|lol|xd|hmm+"
    r"|ok+|okey|okay|okie"
    r"|ah+|oh+|uh+|eh+|mm+"
    r"|s[ií]+|si+|y[ea]+[hs]*|yep|yup|nope?"
    r"|no+|nah+|nel"
    r"|gracias|thanks|thx|merci|danke|grazie|obrigad[oa]"
    r"|de nada|you'?re welcome|de rien"
    r"|genial|perfecto|perfect|exacto|claro|dale|va|vale|bien|bueno"
    r"|cool|nice|great|awesome|got it|entendido|listo"
    r"|bye|chao|adi[oó]s|nos vemos|hasta luego|see you"
    r")!?\.?$",
    re.IGNORECASE,
)

# Repetición (mismo mensaje)
_last_messages: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Filtro principal
# ---------------------------------------------------------------------------

def evaluate_intake(
    content: str,
    user_id: str,
    *,
    user_tier: str = "free",
    has_memory: bool = False,
) -> IntakeResult:
    """
    Evalúa un dato entrante y decide importancia + acción.

    Se ejecuta antes de cualquier otro procesamiento.
    Es rápido (<1ms), sin I/O, sin LLM.
    """
    text = content.strip()
    text_lower = text.lower().rstrip("!?.")

    # ── Filtro 0: vacío ──────────────────────────────────────
    if not text or len(text) < 1:
        return IntakeResult(
            importance=Importance.LOW,
            action=Action.IGNORE,
            reason="empty_input",
            bypass_llm=True,
        )

    # ── Filtro 1: ruido ──────────────────────────────────────
    if _NOISE.match(text_lower):
        return IntakeResult(
            importance=Importance.LOW,
            action=Action.IGNORE,
            reason="noise",
            bypass_llm=True,
        )

    # ── Filtro 2: duplicado exacto del último mensaje ────────
    # Exception: temporal questions are never duplicates (time changes)
    _is_temporal = bool(re.match(
        r"^(?:qu[eé]\s+(?:hora|d[ií]a|fecha)|what\s+(?:time|day|date)|quelle\s+heure)",
        text_lower,
    ))
    last = _last_messages.get(user_id, "")
    if text_lower == last and not _is_temporal:
        return IntakeResult(
            importance=Importance.LOW,
            action=Action.IGNORE,
            reason="duplicate",
            bypass_llm=True,
        )
    _last_messages[user_id] = text_lower

    # ── Filtro 3: comandos del sistema ───────────────────────
    if _SYSTEM_COMMANDS.match(text_lower):
        return IntakeResult(
            importance=Importance.HIGH,
            action=Action.EXECUTE,
            reason="system_command",
            bypass_llm=True,
        )

    # ── Filtro 4: datos personales → ejecutar (para que core_memory absorba
    #    Y resolve_with_memory pueda responder con identidad)
    if _PERSONAL_DATA.search(text):
        return IntakeResult(
            importance=Importance.HIGH,
            action=Action.EXECUTE,
            reason="personal_data",
            context_hint="identity",
        )

    # ── Filtro 5: consulta compleja → ejecución completa ─────
    if _COMPLEX_QUERY.search(text):
        return IntakeResult(
            importance=Importance.HIGH,
            action=Action.EXECUTE,
            reason="complex_query",
            context_hint="full_pipeline",
        )

    # ── Filtro 5.5: instrucción de idioma → ejecutar ───────────
    if _LANGUAGE_INSTRUCTION.search(text):
        return IntakeResult(
            importance=Importance.HIGH,
            action=Action.EXECUTE,
            reason="language_instruction",
            context_hint="language_switch",
        )

    # ── Filtro 6: consulta factual → ejecución directa ───────
    if _FACTUAL_QUERY.search(text):
        return IntakeResult(
            importance=Importance.MEDIUM,
            action=Action.EXECUTE,
            reason="factual_query",
            context_hint="search_or_memory",
        )

    # ── Filtro 6.5: pregunta semántica (sin ?) → ejecutar ──────
    if _SEMANTIC_QUERY.search(text):
        return IntakeResult(
            importance=Importance.MEDIUM,
            action=Action.EXECUTE,
            reason="semantic_query",
            context_hint="search_or_memory",
        )

    # ── Filtro 6.7: mensajes sobre Vectrax o el sistema → ejecutar siempre ────
    if _VECTRAX_REFERENCE.search(text):
        return IntakeResult(
            importance=Importance.HIGH,
            action=Action.EXECUTE,
            reason="vectrax_self_reference",
            context_hint="self_aware",
        )

    # ── Filtro 6.8: intención de búsqueda / consulta online → ejecutar ────
    if _SEARCH_INTENT.search(text):
        return IntakeResult(
            importance=Importance.HIGH,
            action=Action.EXECUTE,
            reason="search_intent",
            context_hint="online_search",
        )

    # ── Filtro 6.85: natural memory triggers → guardar + confirmar
    # El usuario dice 'recuérdame', 'guarda', 'anota', 'no olvides' →
    # guardar en memoria Y responder confirmando.
    _MEMORY_TRIGGER = re.compile(
        r"(?:"
        r"\b(?:recu[eé]rdame|recu[eé]rda\s+(?:que|esto)|recuerda)"
        r"|\b(?:guarda|guárda(?:me|lo)?|guardar|anota|anóta(?:me|lo)?)"
        r"|\b(?:no\s+(?:te\s+)?olvides|no\s+olvid(?:ar|es))"
        r"|\b(?:remember|save|note\s+(?:this|that)|don'?t\s+forget)"
        r"|\b(?:tengo\s+(?:que|una?)\s+(?:reuni[oó]n|cita|junta|examen|entrevista|vuelo|evento))"
        r"|\b(?:mañana\s+(?:tengo|voy|necesito|debo))"
        r")",
        re.IGNORECASE,
    )
    if _MEMORY_TRIGGER.search(text):
        return IntakeResult(
            importance=Importance.HIGH,
            action=Action.EXECUTE,
            reason="natural_memory_trigger",
            context_hint="memory_store_and_confirm",
        )

    # ── Filtro 6.9: emociones / conversación casual → siempre ejecutar
    # Estos mensajes necesitan respuesta humana, no silencio.
    _EMOTIONAL = re.compile(
        r"(?:"
        r"\b(?:estoy|me siento|I'?m|I feel)\s+"
        r"(?:cansad[oa]|tired|triste|sad|sol[oa]|lonely|aburrid[oa]|bored"
        r"|feliz|happy|content[oa]|ansios[oa]|anxious|nervios[oa]|nervous"
        r"|frustrad[oa]|frustrated|enferm[oa]|sick|bien|mal|mejor|peor"
        r"|preocupad[oa]|worried|estresad[oa]|stressed|confundid[oa]|confused)"
        r"|\b(?:tengo\s+(?:hambre|sueño|sed|fr[ií]o|calor|miedo|dolor|fiebre))"
        r"|\b(?:I'?m\s+(?:hungry|sleepy|thirsty|cold|hot|scared))"
        r"|\b(?:qu[eé]\s+d[ií]a\s+tan|what a day|vaya d[ií]a)"
        r"|\b(?:no puedo m[aá]s|I can'?t anymore|estoy harto|I'?m done)"
        r")",
        re.IGNORECASE,
    )
    if _EMOTIONAL.search(text):
        return IntakeResult(
            importance=Importance.MEDIUM,
            action=Action.EXECUTE,
            reason="emotional_conversational",
            context_hint="empathy",
        )

    # ── Filtro 7: mensaje corto sin pregunta ───────────────────────
    word_count = len(text.split())
    has_question = (
        "?" in text or "¿" in text
        or bool(re.match(
            r"^(?:qu[eé]|c[oó]mo|cu[aá]l|cu[aá]ndo|d[oó]nde|cu[aá]nto|qui[eé]n"
            r"|what|who|where|when|why|how|which)\b",
            text_lower,
        ))
    )
    if word_count <= 5 and not has_question:
        # Active conversation check: if there was a recent interaction
        # (<10 min), the user is continuing a thread. ALWAYS execute.
        # Uses the DB (interactions table), not in-memory state, because
        # subprocess isolation loses ConversationState between messages.
        try:
            import sqlite3 as _sq3
            import os as _os
            _mem_db = _os.path.join(
                _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                "vault", "user_memory.db",
            )
            if _os.path.exists(_mem_db):
                _c = _sq3.connect(_mem_db, timeout=2)
                _row = _c.execute(
                    "SELECT MAX(timestamp) FROM interactions WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
                _c.close()
                if _row and _row[0] and (time.time() - float(_row[0])) < 600:
                    return IntakeResult(
                        importance=Importance.MEDIUM,
                        action=Action.EXECUTE,
                        reason="active_conversation",
                        context_hint="conversational",
                    )
        except Exception:
            pass

        # Word Gravity check: if any word has high gravitational mass,
        # it's significant — don't discard.
        try:
            from core.word_gravity import get_effective_mass
            max_mass = 0.0
            for token in text_lower.split():
                m, _ = get_effective_mass(token, user_id)
                if m > max_mass:
                    max_mass = m
            if max_mass >= 0.5:
                return IntakeResult(
                    importance=Importance.HIGH,
                    action=Action.EXECUTE,
                    reason="gravity_word",
                    context_hint="self_aware" if max_mass >= 0.8 else "full_pipeline",
                )
        except Exception:
            pass

        # Fact memory check before discarding
        try:
            from vectrax.fact_memory import query_facts
            fact_result = query_facts(user_id, text)
            if fact_result:
                return IntakeResult(
                    importance=Importance.MEDIUM,
                    action=Action.EXECUTE,
                    reason="fact_query_detected",
                    context_hint="search_or_memory",
                )
        except Exception:
            pass
        return IntakeResult(
            importance=Importance.LOW,
            action=Action.STORE,
            reason="short_statement",
            context_hint="memory_only",
        )

    # ── Filtro 8: mensaje largo sin pregunta → guardar + esperar
    if word_count > 20 and not has_question:
        return IntakeResult(
            importance=Importance.MEDIUM,
            action=Action.STORE,
            reason="long_input_no_question",
            context_hint="memory_and_context",
        )

    # ── Default: ejecutar con importancia media ──────────────
    return IntakeResult(
        importance=Importance.MEDIUM,
        action=Action.EXECUTE,
        reason="general_input",
    )


# ---------------------------------------------------------------------------
# Limpieza periódica del cache de duplicados
# ---------------------------------------------------------------------------

def cleanup_cache(max_entries: int = 1000) -> None:
    """Evitar crecimiento ilimitado del cache de duplicados."""
    global _last_messages
    if len(_last_messages) > max_entries:
        # Mantener solo los últimos N
        keys = list(_last_messages.keys())
        for k in keys[:-max_entries]:
            _last_messages.pop(k, None)
