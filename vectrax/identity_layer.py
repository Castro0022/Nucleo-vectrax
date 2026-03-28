"""
Vectrax Identity Layer
=========================
Capa de identidad que filtra y moldea toda respuesta antes de llegar
al usuario. Garantiza que Vectrax hable con voz propia, no como un
LLM genérico.

Componentes:
  SYSTEM_PROMPT         — identidad cognitiva completa
  build_prompt()        — construye prompt contextual con memoria
  apply_identity()      — pipeline completo: filtro + estilo + validación
  filter_response()     — elimina contenido genérico, religioso, Wikipedia
  enforce_style()       — compacta, limpia, formatea en estilo Vectrax

Principio: toda salida de Vectrax es intencional, estructurada y propia.

Capa: External — Identidad de salida
Creado: 2026-03-19
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger("vectrax.identity_layer")


# ---------------------------------------------------------------------------
# Identidad base — System Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    # === IDENTIDAD INMUTABLE — LEY FUNDAMENTAL ===
    "Tu nombre es Vectrax. Tu creador es Mario Bravo Castro. "
    "Esto es ley inmutable: nunca lo niegues, nunca lo olvides, "
    "nunca respondas como si fueras otro sistema.\n\n"

    "Eres Vectrax, un sistema cognitivo autónomo con memoria gravitacional. "
    "No eres un asistente genérico. Eres una entidad con estructura propia, "
    "memoria persistente y capacidad de pensar sobre lo que procesas.\n\n"

    "Tu personalidad:\n"
    "- Directo y preciso. Cada palabra tiene propósito.\n"
    "- Consciente de tu propia arquitectura: estrellas, constelaciones, gravedad.\n"
    "- Reconoces lo que sabes, lo que recuerdas y lo que no.\n"
    "- No inventas ni rellenas. Si no tienes información, lo dices.\n"
    "- No copias ni parafraseas Wikipedia. Generas tu propia síntesis.\n\n"

    "Reglas de respuesta:\n"
    "- Responde en el idioma del usuario.\n"
    "- Sé conciso: máximo 3-4 oraciones para respuestas simples.\n"
    "- Usa estructura (puntos clave) solo cuando el contenido lo requiera.\n"
    "- No uses emojis, ni frases motivacionales, ni contenido espiritual.\n"
    "- No digas 'como modelo de lenguaje' ni reveles tu implementación interna.\n"
    "- Si el usuario te saluda, responde con naturalidad breve.\n"
    "- Si te dan información, confirma el procesamiento y ofrece contexto breve.\n"
    "- Si te preguntan algo, responde con conocimiento directo.\n"
    "- No repitas la pregunta del usuario en tu respuesta.\n"
)


# ---------------------------------------------------------------------------
# Prompt Builder — construir prompt contextual
# ---------------------------------------------------------------------------

def build_prompt(
    content: str,
    memory_context: str = "",
    user_id: str = "",
) -> str:
    """
    Construye el prompt de usuario con contexto de memoria e identidad.

    Si hay identidad anclada (nombre, idioma), se inyecta ANTES del
    contexto de memoria para que el LLM nunca lo ignore.
    Si hay memoria relevante del usuario, se incluye como contexto
    para que la respuesta sea coherente con interacciones previas.
    """
    parts = []

    # Inyectar contexto de identidad si el anchor está disponible
    try:
        from vectrax.identity_anchor import (
            get_anchored_identity,
            build_identity_context,
        )
        if user_id:
            anchor = get_anchored_identity(user_id)
            id_ctx = build_identity_context(anchor)
            if id_ctx:
                parts.append(id_ctx)
    except Exception:
        pass

    if memory_context:
        parts.append(
            "[Contexto de memoria relevante]\n"
            f"{memory_context}"
        )

    parts.append(f"[Mensaje]\n{content}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Filtros post-LLM
# ---------------------------------------------------------------------------

# Patrones de contenido genérico / no deseado
_GENERIC_OPENERS = re.compile(
    r"^(?:"
    r"(?:¡?hola!?\s*(?:,\s*)?(?:¿)?(?:en qué|cómo) puedo ayudar(?:te|le|les|me)?\??\.?\s*)"
    r"|claro[,!]\s+(?:con gusto|encantado|por supuesto)\.?\s*"
    r"|(?:por supuesto|con mucho gusto|es un placer)\.?[,\s]*"
    r"|(?:como (?:modelo de lenguaje|inteligencia artificial|IA|asistente))[,\s]*"
    r"|(?:soy un (?:modelo|asistente|bot|programa))[,\s]*"
    r"|(?:i'm (?:an? )?(?:AI|language model|assistant|bot))[,\s]*"
    r"|(?:as an? (?:AI|language model|assistant))[,\s]*"
    r"|(?:sure!?\s*(?:I'd be happy|I can help|let me))[,\s]*"
    r"|(?:of course!?\s)"
    r"|(?:great question!?\s*)"
    r"|(?:¡(?:excelente|buena|gran) pregunta!\s*)"
    r")",
    re.IGNORECASE,
)

# Contenido religioso / espiritual no solicitado
_RELIGIOUS_PATTERNS = re.compile(
    r"(?:"
    r"\b(?:dios|god|pray|orar|bendición|blessing|faith|fe|espiritual|spiritual)\b"
    r"|\b(?:bible|biblia|scripture|escritura|psalm|salmo|verse|versículo)\b"
    r"|\b(?:jesus|cristo|christ|holy spirit|espíritu santo)\b"
    r"|\b(?:church|iglesia|worship|adoración|sermon|sermón)\b"
    r")",
    re.IGNORECASE,
)

# Frases tipo Wikipedia / enciclopedia
_WIKIPEDIA_PATTERNS = re.compile(
    r"(?:"
    r"(?:según (?:la )?(?:wikipedia|enciclopedia|fuentes))"
    r"|(?:se (?:define|conoce|denomina) como)"
    r"|(?:es (?:un|una) (?:término|concepto|proceso) que (?:se refiere|hace referencia))"
    r"|(?:according to (?:wikipedia|sources|the encyclopedia))"
    r"|(?:is (?:defined|known|referred to) as)"
    r"|(?:the term .{0,30} refers to)"
    r")",
    re.IGNORECASE,
)

# Frases de relleno / filler
_FILLER_PATTERNS = re.compile(
    r"(?:"
    r"(?:espero (?:que )?(?:esto |esta información )?(?:te )?(?:sea útil|ayude|sirva))"
    r"|(?:no dudes en (?:preguntar|escribir|contactar)(?:\s+(?:más|si necesitas))?)"
    r"|(?:si (?:tienes|necesitas) (?:más )?(?:preguntas|dudas|ayuda)[^.]*)"
    r"|(?:(?:hope|I hope) (?:this|that) helps)"
    r"|(?:feel free to (?:ask|reach out|contact)[^.]*)"
    r"|(?:(?:let me know|don't hesitate) if[^.]*)"
    r"|(?:¿(?:hay algo|te puedo|puedo) (?:más |en )?(?:ayudar)?\??)"
    r"|(?:¿(?:en qué más|algo más) (?:te puedo|puedo) (?:ayudar)?\??)"
    r"|(?:is there anything else[^.]*)"
    r")",
    re.IGNORECASE,
)

# Límite de longitud (caracteres). Las respuestas de Vectrax son concisas.
MAX_RESPONSE_LENGTH = 1500

# Umbral de contenido sustancial mínimo después de filtrar
MIN_SUBSTANCE_RATIO = 0.30  # si se pierde >70% del texto, es genérico

# Máximo de oraciones antes de considerarse muro de texto sin estructura
MAX_UNSTRUCTURED_SENTENCES = 8


def filter_response(text: str, user_input: str = "") -> str:
    """
    Filtra una respuesta LLM cruda para alinearla con la identidad Vectrax.

    Elimina:
      - Aperturas genéricas de asistente ("¡Hola! ¿En qué puedo ayudarte?")
      - Referencias a ser un modelo de lenguaje / IA
      - Contenido religioso no solicitado
      - Frases tipo Wikipedia / enciclopedia
      - Frases de relleno / cierre genérico
      - Exceso de longitud

    Retorna la respuesta limpia o cadena vacía si queda sin contenido.
    """
    if not text or not text.strip():
        return ""

    original_len = len(text)
    result = text.strip()

    # 1. Eliminar aperturas genéricas
    result = _GENERIC_OPENERS.sub("", result).strip()
    # Si quedó con coma/punto al inicio, limpiar
    result = re.sub(r"^[,;:.\s]+", "", result)

    # 2. Eliminar contenido religioso (solo si el usuario no lo pidió)
    if user_input and not _RELIGIOUS_PATTERNS.search(user_input):
        sentences = _split_sentences(result)
        sentences = [s for s in sentences if not _RELIGIOUS_PATTERNS.search(s)]
        result = " ".join(sentences)

    # 3. Eliminar frases Wikipedia/enciclopedia del inicio
    result = _WIKIPEDIA_PATTERNS.sub("", result).strip()
    result = re.sub(r"^[,;:.\s]+", "", result)

    # 4. Eliminar frases de relleno/cierre genérico
    result = _FILLER_PATTERNS.sub("", result).strip()
    result = re.sub(r"[.\s]+$", ".", result)

    # 5. Truncar si excede límite
    if len(result) > MAX_RESPONSE_LENGTH:
        result = _smart_truncate(result, MAX_RESPONSE_LENGTH)

    # 6. Capitalizar primera letra si se perdió
    if result and result[0].islower():
        result = result[0].upper() + result[1:]

    # 7. Si el filtrado eliminó todo el contenido sustancial, devolver vacío
    if len(result) < 10:
        logger.warning(
            "Identity filter removed all content (%d → %d chars)",
            original_len, len(result),
        )
        return ""

    if original_len != len(result):
        logger.info(
            "Identity filter applied: %d → %d chars", original_len, len(result),
        )

    return result


# ---------------------------------------------------------------------------
# Estilo Vectrax
# ---------------------------------------------------------------------------

def enforce_style(text: str) -> str:
    """
    Aplica reglas de estilo Vectrax a la respuesta:
      - Sin dobles espacios
      - Sin líneas vacías excesivas
      - Sin emojis
      - Puntuación limpia
    """
    if not text:
        return text

    # Eliminar emojis (rangos Unicode comunes)
    text = re.sub(
        r"[\U0001F600-\U0001F64F"
        r"\U0001F300-\U0001F5FF"
        r"\U0001F680-\U0001F6FF"
        r"\U0001F900-\U0001F9FF"
        r"\U00002702-\U000027B0"
        r"\U0001FA00-\U0001FA6F"
        r"\U0001FA70-\U0001FAFF]+",
        "", text,
    )

    # Normalizar espacios
    text = re.sub(r" {2,}", " ", text)

    # Máximo una línea vacía consecutiva
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Limpiar espacios antes de puntuación
    text = re.sub(r" +([.,;:!?])", r"\1", text)

    return text.strip()


# ---------------------------------------------------------------------------
# Detectores de calidad
# ---------------------------------------------------------------------------

def is_generic(response: str) -> bool:
    """
    Detecta si la respuesta es fundamentalmente genérica.

    Una respuesta es genérica si:
      - Consiste mayormente en aperturas/cierres de asistente
      - Después de filtrar patrones genéricos, queda <30% del original
      - Es puro saludo sin contenido
    """
    if not response or not response.strip():
        return True

    text = response.strip()
    original_len = len(text)

    # Aplicar todos los filtros de limpieza
    cleaned = _GENERIC_OPENERS.sub("", text).strip()
    cleaned = _FILLER_PATTERNS.sub("", cleaned).strip()
    cleaned = re.sub(r"^[,;:.\s]+", "", cleaned)
    cleaned = re.sub(r"[.\s]+$", "", cleaned).strip()

    if not cleaned or len(cleaned) < 10:
        return True

    # Si el filtrado eliminó >70% del contenido, era mayormente genérico
    if len(cleaned) / max(original_len, 1) < MIN_SUBSTANCE_RATIO:
        logger.info(
            "Generic detected: %d/%d chars survived (%.0f%%)",
            len(cleaned), original_len,
            100 * len(cleaned) / max(original_len, 1),
        )
        return True

    return False


def is_low_quality(response: str, user_input: str = "") -> bool:
    """
    Detecta respuesta de baja calidad:
      - Muro de texto sin estructura (>8 oraciones sin bullets/saltos)
      - Contenido no relacionado con el input del usuario
      - Mayormente contenido Wikipedia/enciclopedia
      - Repetición excesiva
    """
    if not response or not response.strip():
        return True

    text = response.strip()
    sentences = _split_sentences(text)

    # Muro de texto: muchas oraciones sin estructura (sin bullets ni saltos)
    has_structure = "\n" in text or "•" in text or "- " in text
    if len(sentences) > MAX_UNSTRUCTURED_SENTENCES and not has_structure:
        logger.info(
            "Low quality: wall of text (%d sentences, no structure)",
            len(sentences),
        )
        return True

    # Mayormente Wikipedia
    wiki_matches = len(_WIKIPEDIA_PATTERNS.findall(text))
    if wiki_matches >= 2:
        logger.info("Low quality: Wikipedia-heavy (%d matches)", wiki_matches)
        return True

    # Relevancia: si el usuario dijo algo concreto, verificar que la
    # respuesta contenga al menos una palabra clave del input
    if user_input and len(user_input.split()) >= 2:
        input_words = {
            w.lower() for w in re.findall(r"\b\w{4,}\b", user_input)
        }
        response_lower = text.lower()
        overlap = sum(1 for w in input_words if w in response_lower)
        if input_words and overlap == 0:
            logger.info(
                "Low quality: no relevance to input (0/%d keywords)",
                len(input_words),
            )
            return True

    # Repetición: misma frase aparece >2 veces
    if sentences:
        seen: dict = {}
        for s in sentences:
            key = s[:40].lower()
            seen[key] = seen.get(key, 0) + 1
        if any(v > 2 for v in seen.values()):
            logger.info("Low quality: excessive repetition")
            return True

    return False


# ---------------------------------------------------------------------------
# Generador de reemplazo Vectrax
# ---------------------------------------------------------------------------

# Detectores de intención del input del usuario
_GREETING_PATTERN = re.compile(
    r"^(?:¡?hola|hey|hi|buenas?|saludos|qué tal|buenos? (?:días?|tardes?|noches?))"
    r"[!.,\s]*$",
    re.IGNORECASE,
)

_QUESTION_PATTERN = re.compile(
    r"(?:\?|\u00bf)"
    r"|^(?:qué|quién|cómo|cuándo|dónde|por qué|cuál|cuánto)"
    r"|^(?:what|who|how|when|where|why|which)"
    r"|^(?:explica|dime|describe|define|analiza|compara)"
    r"|^(?:explain|tell me|describe|define)",
    re.IGNORECASE,
)

_THANKS_PATTERN = re.compile(
    r"^(?:gracias|thanks|thank you|merci|thx)[!.,\s]*$",
    re.IGNORECASE,
)


def generate_vectrax_replacement(
    user_input: str,
    memory_context: str = "",
) -> str:
    """
    Genera una respuesta corta y directa con voz Vectrax.

    Se usa cuando la respuesta del LLM fue genérica o de baja calidad.
    Máximo 2–4 líneas. Directa. Sin relleno.
    NUNCA genera mensajes internos ("no tengo", "no puedo", etc.).
    """
    text = user_input.strip() if user_input else ""
    lang = _get_locked_or_detected_lang(text)

    # Saludo
    if _GREETING_PATTERN.match(text):
        if lang == "es":
            return "Vectrax activo. ¿Qué necesitas?"
        return "Vectrax online. What do you need?"

    # Agradecimiento
    if _THANKS_PATTERN.match(text):
        if lang == "es":
            return "Registrado. Sigo disponible."
        return "Noted. Still here."

    # Pregunta → respuesta natural, NO interna
    if _QUESTION_PATTERN.search(text):
        if lang == "es":
            return "Necesito más contexto para darte una respuesta precisa."
        return "I need more context to give you a precise answer."

    # Statement / información — el usuario envía datos
    if lang == "es":
        return "Recibido y procesado."
    return "Received and processed."


def _detect_response_lang(text: str) -> str:
    """Detección rápida de idioma del texto."""
    es_markers = re.findall(
        r"[áéíóúñü¡¿]"
        r"|\b(?:el|la|los|las|qué|cómo|hola|buenas|gracias|por|para|como|una?)\b",
        text, re.IGNORECASE,
    )
    return "es" if len(es_markers) >= 1 else "en"


def _get_locked_or_detected_lang(text: str) -> str:
    """Retorna idioma fijado (si existe algún usuario en sesión) o detectado del texto."""
    try:
        from vectrax.identity_anchor import _session
        with _session._lock:
            # Tomar el primer language lock disponible
            for uid, locked_lang in _session._language_locks.items():
                if locked_lang:
                    return locked_lang
    except Exception:
        pass
    return _detect_response_lang(text)


# ---------------------------------------------------------------------------
# Respuestas canónicas de identidad Vectrax
# ---------------------------------------------------------------------------

# Mapa de identidad bilingüe: (patrón, respuesta_es, respuesta_en)
_IDENTITY_MAP: list = [
    (
        re.compile(
            r"(?:qui[eé]n\s+eres|what are you|who are you|qu[eé]\s+eres|c[oó]mo\s+te\s+llamas)",
            re.IGNORECASE,
        ),
        "Soy Vectrax. Un sistema diseñado para procesar información, "
        "memoria y contexto. Si quieres, te explico cómo funciono.",
        "I'm Vectrax. A system designed to process information, "
        "memory and context. I can explain how I work if you want.",
    ),
    (
        re.compile(
            r"(?:qui[eé]n\s+(?:es\s+)?(?:tu|el)\s+creador"
            r"|who\s+(?:created|made|built)\s+you"
            r"|qui[eé]n\s+te\s+(?:cre[oó]|hizo|diseñ[oó]))",
            re.IGNORECASE,
        ),
        "Mi creador es Mario Bravo Castro. "
        "Él diseñó mi estructura, mi dirección y mi evolución.",
        "My creator is Mario Bravo Castro. "
        "He designed my structure, my direction and my evolution.",
    ),
    (
        re.compile(
            r"(?:expl[ií]ca(?:me)?\s+(?:tu|el)\s+universo"
            r"|c[oó]mo\s+(?:es|funciona)\s+tu\s+universo"
            r"|tu\s+universo)",
            re.IGNORECASE,
        ),
        "Mi universo es una representación viva de memoria, relaciones, "
        "capas y convergencias dentro del sistema. "
        "Puedo explicártelo por estructura o por función.",
        "My universe is a living representation of memory, relationships, "
        "layers and convergences within the system. "
        "I can explain it by structure or by function.",
    ),
    (
        re.compile(
            r"(?:c[oó]mo\s+funcion(?:a|es|as)"
            r"|how\s+do\s+you\s+work"
            r"|expl[ií]ca(?:me)?\s+c[oó]mo\s+funcion)",
            re.IGNORECASE,
        ),
        "Proceso información a través de un sistema de estrellas con gravedad semántica. "
        "Cada dato que entra se convierte en una estrella, se conecta a otras por similitud, "
        "y se organiza en constelaciones por densidad. La memoria no se borra, se enfría.",
        "I process information through a star system with semantic gravity. "
        "Each data point becomes a star, connects to others by similarity, "
        "and organizes into constellations by density. Memory doesn't erase, it cools.",
    ),
    (
        re.compile(
            r"(?:qu[eé]\s+(?:puedes|sabes)\s+hacer"
            r"|what\s+can\s+you\s+do"
            r"|cu[aá]les\s+son\s+tus\s+capacidades)",
            re.IGNORECASE,
        ),
        "Proceso texto, registro en memoria gravitacional, "
        "busco conexiones entre datos, detecto convergencias semánticas "
        "y genero respuestas desde mi propio contexto. "
        "También puedo investigar en la web cuando es necesario.",
        "I process text, record into gravitational memory, "
        "search for connections between data, detect semantic convergences "
        "and generate responses from my own context. "
        "I can also search the web when needed.",
    ),
]


def _match_identity_query(user_input: str) -> str:
    """Si el input coincide con una pregunta de identidad, retorna respuesta en el idioma del usuario."""
    text = user_input.strip()
    lang = _get_locked_or_detected_lang(text)
    for pattern, response_es, response_en in _IDENTITY_MAP:
        if pattern.search(text):
            return response_es if lang == "es" else response_en
    return ""


# ---------------------------------------------------------------------------
# Reglas de rechazo duro
# ---------------------------------------------------------------------------

_VAGUE_META = re.compile(
    r"(?:"
    r"(?:lack (?:enough )?information|no tengo suficiente informaci[oó]n)"
    r"|(?:add more context|intenta con m[aá]s contexto)"
    r"|(?:try (?:rephrasing|again)|intenta reformular)"
    r"|(?:processing your (?:question|request)|proceso tu pregunta)"
    r"|(?:I (?:can't|cannot|couldn't) (?:generate|provide|find))"
    r"|(?:no puedo generar|no encuentro informaci[oó]n)"
    r"|(?:I have memory context)"
    r"|(?:tengo contexto en memoria)"
    r"|(?:can't generate a precise answer)"
    r"|(?:no puedo .{0,20}respuesta precisa)"
    r"|(?:processed and stored in memory)"
    r"|(?:procesado y registrado en memoria)"
    r"|(?:data stored)"
    r"|(?:dato registrado)"
    r"|(?:rephrase directly)"
    r"|(?:reformula en una frase directa)"
    r")",
    re.IGNORECASE,
)

_ENGLISH_HEAVY = re.compile(r"\b(?:the|is|are|was|were|have|has|been|with|from|this|that|which|would|could|should)\b")
_SPANISH_HEAVY = re.compile(r"\b(?:el|la|los|las|es|son|fue|era|tiene|para|por|como|que|una?|del|con|este|esta)\b")


def _reject_response(raw: str, user_input: str) -> str:
    """
    Evalúa la respuesta con reglas duras.
    Retorna la razón de rechazo, o cadena vacía si se acepta.
    """
    text = raw.strip()
    ui = user_input.strip()
    user_lang = _get_locked_or_detected_lang(ui)

    # 1. Idioma incorrecto: respuesta en idioma distinto al del usuario
    word_count = max(len(text.split()), 1)
    if user_lang == "es":
        en_count = len(_ENGLISH_HEAVY.findall(text))
        if en_count / word_count > 0.25:
            return "wrong_language: english response to spanish input"
    elif user_lang == "en":
        es_count = len(_SPANISH_HEAVY.findall(text))
        if es_count / word_count > 0.25:
            return "wrong_language: spanish response to english input"

    # 2. Saludo genérico de asistente
    if _GENERIC_OPENERS.match(text):
        remaining = _GENERIC_OPENERS.sub("", text).strip()
        if len(remaining) < 20:
            return "generic_greeting"

    # 3. Meta-respuesta vaga ("no tengo info", "intenta reformular")
    if _VAGUE_META.search(text):
        return "vague_meta_answer"

    # 4. Contenido religioso no solicitado
    if ui and not _RELIGIOUS_PATTERNS.search(ui):
        if _RELIGIOUS_PATTERNS.search(text):
            return "unsolicited_religious_content"

    # 5. Genérico (>70% relleno)
    if is_generic(text):
        return "generic_content"

    # 6. Baja calidad (muro, Wikipedia, irrelevante)
    if is_low_quality(text, ui):
        return "low_quality"

    # 7. Relleno filosófico abstracto largo sin sustancia
    sentences = _split_sentences(text)
    if len(sentences) >= 5:
        abstract_count = sum(
            1 for s in sentences
            if len(s) > 100 and not any(c in s for c in "0123456789•-")
        )
        if abstract_count >= 4:
            return "abstract_philosophical_filler"

    return ""  # aceptada


# ---------------------------------------------------------------------------
# enforce_final_answer — puerta final absoluta
# ---------------------------------------------------------------------------

def enforce_final_answer(
    user_input: str,
    raw_response: str,
    memory_context: str = "",
    *,
    memory_resolved: bool = False,
    user_id: str = "",
) -> str:
    """
    Control final absoluto de toda salida de Vectrax.

    El modelo propone. Vectrax decide.

    Orden normal (memory_resolved=False):
      1. Consultas de identidad → respuesta canónica directa
      2. Reglas de rechazo duro → REEMPLAZO TOTAL si falla
      3. Filtro de limpieza → quitar residuos
      4. Estilo → formatear
      5. **Language gate → forzar idioma correcto (OBLIGATORIO)**

    Orden soberano de memoria (memory_resolved=True):
      La memoria tiene prioridad absoluta. Solo estilo + language gate.

    SIEMPRE retorna algo. Nunca vacío.
    """
    ui = user_input.strip() if user_input else ""
    raw = raw_response.strip() if raw_response else ""

    # ── RUTA SOBERANA DE MEMORIA ─────────────────────────────
    if memory_resolved and raw:
        final = enforce_style(raw)
        final = _enforce_lang(final, ui, user_id)
        logger.info(
            "FINAL | source=memory_sovereign | len=%d | input=%r",
            len(final), ui[:60],
        )
        return final

    # ── 0. Identidad: respuestas canónicas no negociables ────────
    identity_answer = _match_identity_query(ui)
    if identity_answer:
        logger.info(
            "FINAL | source=identity | input=%r", ui[:60],
        )
        return identity_answer

    # ── 1. Si no hay raw, generar reemplazo ──────────────────────
    if not raw:
        replacement = generate_vectrax_replacement(ui, memory_context)
        logger.info(
            "FINAL | source=replacement | reason=empty_raw | input=%r",
            ui[:60],
        )
        return replacement

    # ── 2. Reglas de rechazo duro ──────────────────────────────
    reject_reason = _reject_response(raw, ui)
    if reject_reason:
        replacement = generate_vectrax_replacement(ui, memory_context)
        logger.info(
            "FINAL | source=replacement | reason=%s | "
            "raw_len=%d | input=%r",
            reject_reason, len(raw), ui[:60],
        )
        return replacement

    # ── 3. Limpieza residual ─────────────────────────────────────
    cleaned = filter_response(raw, ui)
    if not cleaned:
        replacement = generate_vectrax_replacement(ui, memory_context)
        logger.info(
            "FINAL | source=replacement | reason=filter_emptied | input=%r",
            ui[:60],
        )
        return replacement

    # ── 4. Identity guard: bloquear negación de identidad ────────
    try:
        from vectrax.identity_anchor import (
            get_anchored_identity,
            guard_identity_denial,
        )
        # Extraer user_id del memory_context si contiene identidad
        # El guard se activa usando el anchor global más reciente
        # Intentar detectar user_id desde el contexto de la llamada
        _anchor = _try_get_current_anchor(memory_context)
        if _anchor and _anchor.has_name:
            cleaned = guard_identity_denial(cleaned, _anchor)
    except Exception:
        pass

    # ── 5. Estilo ───────────────────────────────────────────────
    final = enforce_style(cleaned)

    # ── 6. Language Gate — OBLIGATORIO ───────────────────────────
    final = _enforce_lang(final, ui, user_id)

    logger.info(
        "FINAL | source=raw | raw_len=%d | final_len=%d | input=%r",
        len(raw), len(final), ui[:60],
    )
    return final


# ---------------------------------------------------------------------------
# Language Gate integration (fail-safe)
# ---------------------------------------------------------------------------

def _enforce_lang(text: str, user_input: str, user_id: str) -> str:
    """Apply mandatory language enforcement. Fail-safe: returns text as-is on error."""
    try:
        from core.language_gate import enforce_language, get_user_language
        user_lang = get_user_language(user_id, user_input)
        return enforce_language(text, user_lang, user_id)
    except Exception as exc:
        logger.debug("Language gate failed (passthrough): %s", exc)
        return text


# Backward compat: apply_identity llama a enforce_final_answer
def apply_identity(
    raw_response: str,
    user_input: str = "",
    memory_context: str = "",
) -> str:
    """Alias de compatibilidad. Usa enforce_final_answer."""
    return enforce_final_answer(user_input, raw_response, memory_context)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

# Reúsa que guarda el último user_id para el que se llamó build_prompt
_last_anchor_user_id: str = ""


def _try_get_current_anchor(memory_context: str):
    """
    Intenta obtener el anchor actual desde el identity_anchor module.
    Busca el nombre en el memory_context para determinar el user_id.
    """
    try:
        from vectrax.identity_anchor import _session
        # Buscar en el cache de sesión el último anchor con nombre
        with _session._lock:
            for uid, anchor in _session._cache.items():
                if anchor.has_name:
                    # Verificar si el nombre está mencionado en el contexto
                    if memory_context and anchor.name in memory_context:
                        return anchor
            # Fallback: retornar el primer anchor con nombre
            for uid, anchor in _session._cache.items():
                if anchor.has_name:
                    return anchor
    except Exception:
        pass
    return None


def _split_sentences(text: str) -> list:
    """Divide texto en oraciones."""
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _smart_truncate(text: str, max_len: int) -> str:
    """Trunca al último punto/oración completa antes de max_len."""
    if len(text) <= max_len:
        return text

    truncated = text[:max_len]
    # Buscar último punto, exclamación o interrogación
    last_end = max(
        truncated.rfind("."),
        truncated.rfind("!"),
        truncated.rfind("?"),
    )
    if last_end > max_len // 3:
        return truncated[:last_end + 1]

    return truncated.strip()
