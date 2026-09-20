"""
Vectrax Resolver — Cognitive Chat Resolution Engine
=====================================================
Classifies user messages and resolves them through 3 modes:

  MEMORY  — plain note/statement, ingested as a star
  LOCAL   — question answerable from existing memory stars
  ONLINE  — question requiring external/current information

Components:
  classify()         — determine message intent
  resolve_local()    — search memory for relevant stars
  resolve_online()   — search web via DuckDuckGo, extract snippets
  resolve()          — orchestrator: classify → route → answer
"""
from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger("vectrax.resolver")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Source:
    """A reference returned from online research."""
    title: str
    url: str
    snippet: str


@dataclass
class Resolution:
    """Result of resolving a user message."""
    mode: str                   # "memory" | "local" | "online"
    answer: str = ""            # internal/debug answer (may list sources)
    sovereign_answer: str = ""  # clean user-facing answer (no source info)
    sources: List[Source] = field(default_factory=list)
    context_stars: int = 0      # how many stars were consulted
    search_query: str = ""      # query used for online search (if any)
    fallback_from: str = ""     # non-empty if mode was escalated (e.g. "local")
    top_score: float = 0.0      # best relevance score from local search
    engines_used: List[str] = field(default_factory=list)

# ---------------------------------------------------------------------------
# 1. CLASSIFIER
# ---------------------------------------------------------------------------

# Patterns indicating a question at the START of the text
_QUESTION_STARTS = re.compile(
    r"^(what|who|where|when|why|how|is|are|was|were|do|does|did|can|could|"
    r"should|would|will|which|tell me|explain|describe|define|compare|analyze|summarize|"
    r"give me|find|search|look up|show me|list|fetch|get me|"
    r"qué|quién|quien|cómo|como|cuándo|cuando|dónde|donde|por qué|por que|cuál|cual|cuánto|cuanto|"
    r"dame|dime|busca|buscar|encuentra|muéstrame|muestrame|muestrame|lista|trae|consigue|"
    r"explica|explícame|explicame|analiza|compara|resume|resúmeme|resumeme|describe|"
    r"noticias|noticia|últimas noticias|que pasó|qué pasó|qué paso|que paso)\b",
    re.IGNORECASE,
)

# Semantic intent markers that indicate a query ANYWHERE in the text.
# These catch imperative verbs and accented interrogatives even mid-sentence
# (e.g. "por favor explica cómo funciona la gravedad").
_QUERY_INTENT = re.compile(
    r"(?:"
    r"\b(?:explica|explícame|explicame|dime|dame|analiza|compara|resume|resúmeme|resumeme|describe)\b"
    r"|\b(?:busca|buscar|encuentra|muéstrame|muestrame|lista|trae|consigue|muestra)\b"
    r"|\b(?:quién|quien|qué|cómo|cuándo|dónde|por qué|cuál|cuánto)\b"
    r"|\b(?:what is|who is|how does|how do|how is|what are|tell me|explain|find|search|give me|show me)\b"
    r"|\b(?:noticias|noticia|precio de|precio del|cotización|tendencia|hoy en|últimas)\b"
    r")",
    re.IGNORECASE,
)

# Explicit self-memory references → LOCAL
# These phrases clearly indicate the user is asking about their OWN history/data.
_LOCAL_KEYWORDS = re.compile(
    r"(\bwhat did i\b|\bwhat i said\b|\bwhat have i\b"
    r"|\bmy (messages|history|notes|data|stars|memory|conversations?)\b"
    r"|\bqué dije\b|\bqué te dije\b|\bqué hablamos\b|\bqué recuerdas\b"
    r"|\bmi (historial|memoria|mensajes|notas|conversación|datos)\b"
    r"|\besta conversación\b|\bthis conversation\b"
    r"|\bdo you remember\b|\brecuerdas\b"
    r"|\blo que te conté\b|\blo que escribí\b"
    r"|\bmy previous\b|\bmy last\b)",
    re.IGNORECASE,
)

# Self-name references → the system ITSELF, never the web. There is an unrelated
# CNC company also called "Vectrax"; searching the web for the name returns it.
# Rule: no internet for self/identity queries.
_SELF_NAME = re.compile(r"\bvectrax\b|api\.vectrax\.app", re.IGNORECASE)

# Greetings / social smalltalk → conversational, never a factual web query.
_GREETING = re.compile(
    r"^\s*(?:hola+|hol[ai]|buenas|buenos\s+d[ií]as|buenas\s+tardes|"
    r"buenas\s+noches|qu[eé]\s+tal|qu[eé]\s+onda|qu[eé]\s+hubo|"
    r"c[oó]mo\s+est[aá]s|como\s+estas|c[oó]mo\s+vas|c[oó]mo\s+te\s+va|"
    r"c[oó]mo\s+andas|hey+|hello+|\bhi\b|saludos|gracias|buen[ao]s)\b",
    re.IGNORECASE,
)

# Creator / identity questions → answered from identity, never the web.
_CREATOR = re.compile(
    r"\bqui[eé]n\s+te\s+(?:cre[oó]|hizo|dise[nñ][oó]|construy[oó])\b"
    r"|\bwho\s+(?:created|made|built)\s+you\b"
    r"|\btu\s+creador\b|\byour\s+creator\b",
    re.IGNORECASE,
)


def _is_conversational(text: str) -> bool:
    """True for self-reference (the system itself), greetings, or creator/identity.

    These must resolve from identity/memory and NEVER trigger a web search
    (avoids confusing Vectrax-the-system with the unrelated CNC company).
    """
    t = (text or "").strip()
    return bool(_SELF_NAME.search(t) or _GREETING.match(t) or _CREATOR.search(t))


# ---------------------------------------------------------------------------
# PERSONAL RELATIONSHIP QUERIES — privacy guardrail (SSOT vocabulary)
# ---------------------------------------------------------------------------
# Corrección 2026-09-20 (caso real: "¿Quién es mi novia?" -> route=online ->
# el sistema "descubrió" en internet una persona que no existía y la presentó
# como respuesta verificada). Vocabulario GENÉRICO de relaciones personales
# (sin nombres propios) -- ÚNICA fuente reutilizada por:
#   - core/semantic_classifier.py (frame ASK_MEMORY)
#   - core/smart_router.py (regex _LOCAL_KEYWORDS fallback)
#   - core/nucleus/nucleus_authority.py (guardrail de privacidad final)
# para que las 4 capas nunca diverjan en qué cuenta como "relación personal".
_RELATIONSHIP_NOUNS_ES = (
    r"esposa|esposo|marido|pareja|novi[ao]|prometid[ao]|hij[ao]|madre|padre|"
    r"mam[aá]|pap[aá]|herman[ao]|amig[ao]|jefe|socia?|colega|"
    r"suegr[ao]|cu[ñn]ad[ao]|abuel[ao]|niet[ao]|prim[ao]|t[ií][ao]"
)
_RELATIONSHIP_NOUNS_EN = (
    r"wife|husband|partner|girlfriend|boyfriend|fianc[ée]e?|son|daughter|"
    r"mother|father|mom|dad|brother|sister|friend|boss|colleague|"
    r"grandmother|grandfather|grandson|granddaughter|cousin|aunt|uncle"
)

# Pregunta (no afirmación) sobre QUIÉN ES una persona relacionada con el
# usuario -- "mi novia se llama Ana" es un enunciado a guardar (no coincide);
# "¿quién es mi novia?" es una consulta que NUNCA debe resolverse saliendo a
# internet a "descubrir" quién podría ser esa persona.
_PERSONAL_RELATIONSHIP_QUERY_RE = re.compile(
    r"\b(?:qui[eé]n\s+es|qui[eé]n\s+ser[aá]|c[oó]mo\s+se\s+llama|"
    r"cu[aá]l\s+es\s+el\s+nombre\s+de)\s+mi\s+"
    rf"(?:{_RELATIONSHIP_NOUNS_ES})\b"
    rf"|\bwho\s+is\s+my\s+(?:{_RELATIONSHIP_NOUNS_EN})\b"
    rf"|\bwhat'?s?\s+my\s+(?:{_RELATIONSHIP_NOUNS_EN})'?s?\s+name\b",
    re.IGNORECASE,
)


def is_personal_relationship_query(text: str) -> bool:
    """True si el texto pregunta QUIÉN ES una persona relacionada con el
    usuario (p.ej. "¿Quién es mi novia?", "who is my boss") -- consulta de
    memoria personal relacional, GENÉRICA (sin nombres propios ni reglas
    específicas de ningún usuario). Esta consulta SOLO puede resolverse
    desde la memoria propia del usuario; nunca debe salir a internet a
    "descubrir" quién podría ser esa persona -- eso es una fuga de
    privacidad, no una respuesta válida."""
    return bool(_PERSONAL_RELATIONSHIP_QUERY_RE.search((text or "").strip()))


# ---------------------------------------------------------------------------
# PERSONAL MEMORY QUERIES (genérico) -- superconjunto de relationship queries
# ---------------------------------------------------------------------------
# Corrección estructural 2026-09-20 (cierre de memoria conversacional
# multiusuario): detector GENÉRICO -- por ESTRUCTURA sintáctica (marcador
# temporal/posesivo + verbo de recuerdo/decisión), no por una lista cerrada
# de frases exactas -- de preguntas sobre el HISTORIAL, las DECISIONES o la
# EXPERIENCIA PREVIA del propio usuario con Vectrax. Cubre paráfrasis como
# "¿qué hablamos ayer?", "¿qué decidimos sobre aquel proyecto?", "¿cuándo te
# mencioné a X?", "recuérdame lo que hablamos de Y", "¿qué fue lo último que
# decidimos?" -- sin nombres propios, sin fechas ni proyectos hardcodeados.
_RECALL_VERBS_ES = (
    r"hablamos|hablaste|dijimos|dijiste|dije|platicamos|conversamos|"
    r"coment[eé]|coment[a]mos|comentaste|decidimos|decidiste|acordamos|"
    r"qued[a]mos|habl[eé]|mencion[eé]|mencionaste|discutimos"
)
_RECALL_VERBS_EN = (
    r"talk(?:ed)?|discuss(?:ed)?|say|said|decide[d]?|agree[d]?|mention(?:ed)?|chat(?:ted)?"
)

_PERSONAL_RECALL_QUERY_RE = re.compile(
    r"\b(?:qu[eé]|de\s+qu[eé]|sobre\s+qu[eé]|cu[aá]ndo|c[oó]mo)\s+(?:te\s+|me\s+|nos\s+|le\s+)?(?:"
    rf"{_RECALL_VERBS_ES}"
    r")\b"
    r"|\bqu[eé]\s+fue\s+lo\s+(?:\u00faltimo|ultimo)\s+que\s+(?:" + _RECALL_VERBS_ES + r")\b"
    r"|\bte\s+acuerdas\s+de\b|\brecu[eé]rdame\s+(?:lo\s+que|qu[eé])\b"
    r"|\bcu[aá]l\s+fue\s+(?:nuestra|la)\s+\w*\s*(?:decisi[oó]n|conversaci[oó]n)\b"
    rf"|\bwhat\s+did\s+we\s+(?:{_RECALL_VERBS_EN})\b"
    r"|\bwhen\s+did\s+(?:i|you)\s+mention\b"
    r"|\bdo\s+you\s+remember\s+(?:when|the|that)\b"
    r"|\bwhat\s+was\s+(?:our|the)\s+(?:last\s+)?decision\b",
    re.IGNORECASE,
)


def is_personal_memory_query(text: str) -> bool:
    """True si el texto es una consulta GENÉRICA sobre la memoria personal
    del usuario -- historial conversacional, relaciones, decisiones o
    experiencia previa con Vectrax -- sin depender de una lista cerrada de
    frases exactas. Superconjunto de `is_personal_relationship_query()` y
    de los patrones de auto-referencia de memoria ya existentes
    (`_LOCAL_KEYWORDS`, `_PROFILE_SUMMARY_RE`). Esta es la única fuente
    usada por el guardrail de privacidad de NucleusAuthority: cualquier
    consulta que matchee aquí NUNCA puede resolverse vía ONLINE/PLACES/
    MARKET, sin importar qué proponga el enrutamiento ascendente."""
    t = (text or "").strip()
    if not t:
        return False
    return bool(
        is_personal_relationship_query(t)
        or _PERSONAL_RECALL_QUERY_RE.search(t)
        or _PROFILE_SUMMARY_RE.search(t)
        or _LOCAL_KEYWORDS.search(t)
    )


def classify(text: str) -> str:
    """
    Classify a user message into one of: 'memory', 'local', 'online'.

    Logic (priority order):
      1. If it references the user's own memory explicitly → local
      2. If it is a question / query (punctuation OR start pattern OR
         semantic intent marker anywhere in text) → online
      3. Otherwise → memory (plain note/statement for ingestion)

    Intent detection prioritises semantics over punctuation:
    "explica cómo funciona la gravedad" is recognised as a query even
    without a question mark.
    """
    text_stripped = text.strip()
    has_question_mark = "?" in text_stripped or "¿" in text_stripped
    has_question_start = bool(_QUESTION_STARTS.match(text_stripped))
    has_query_intent = bool(_QUERY_INTENT.search(text_stripped))
    has_local_keywords = bool(_LOCAL_KEYWORDS.search(text_stripped))

    is_question = has_question_mark or has_question_start or has_query_intent

    logger.debug(
        "classify(%r): ?=%s start=%s intent=%s local=%s → question=%s",
        text_stripped[:60], has_question_mark, has_question_start,
        has_query_intent, has_local_keywords, is_question,
    )

    # Self-reference (the system itself), greetings, and creator/identity
    # questions are conversational: resolve from identity/memory, NEVER from
    # the web. This prevents a self query like "cómo estás vectrax" from
    # web-searching the unrelated CNC company "Vectrax".
    # (Rule: no internet for self/identity/greetings.)
    if _is_conversational(text_stripped):
        return "local"
    # Self-memory reference takes priority → LOCAL
    # Local keywords alone are sufficient (user asking about their own data).
    if has_local_keywords:
        return "local"
    # Personal relationship queries ("¿Quién es mi novia?") are ALWAYS local
    # memory lookups -- never a web search to "discover" who that person is.
    if is_personal_relationship_query(text_stripped):
        return "local"
    # Any other question / query → ONLINE (factual default)
    if is_question:
        return "online"
    return "memory"


# ---------------------------------------------------------------------------
# PROFILE SUMMARY — recuperación diversa para consultas amplias de perfil
# ---------------------------------------------------------------------------
# Genérico y aplicable a CUALQUIER usuario: ni el detector de intención ni
# los clasificadores de categoría a continuación contienen nombres propios,
# frases exactas de una persona concreta, ni ninguna regla especial para
# "Mario"/el creador — son patrones lingüísticos posesivos/genéricos
# ("qué sabes de mí", "mi pareja", "mi proyecto"...) igual de válidos para
# cualquier owner/channel.

# Detecta la INTENCIÓN de "resume/cuéntame lo que sabes de mí" -- una
# pregunta AMPLIA de perfil, distinta de una pregunta puntual ("¿cómo me
# llamo?", "¿dónde vivo?") que ya funciona bien con la búsqueda por
# similitud simple existente.
_PROFILE_SUMMARY_RE = re.compile(
    r"(?:"
    r"qu[eé]\s+sabes\s+(?:de|sobre|acerca\s+de)\s+m[ií]\b"
    r"|cu[eé]ntame\s+(?:sobre\s+m[ií]|de\s+m[ií]|qui[eé]n\s+soy|c[oó]mo\s+soy)\b"
    r"|resume(?:me)?\s+(?:lo\s+que\s+sabes\s+de\s+m[ií]|mi\s+perfil)"
    r"|haz(?:me)?\s+un\s+resumen\s+(?:de\s+m[ií]|sobre\s+m[ií]|de\s+mi\s+perfil)"
    r"|^\s*qui[eé]n\s+soy(?:\s+yo)?\s*\??\s*$"
    r"|^\s*c[oó]mo\s+soy(?:\s+yo)?\s*\??\s*$"
    r"|descr[ií]beme\b"
    r"|todo\s+lo\s+que\s+sabes\s+de\s+m[ií]\b"
    r"|qu[eé]\s+(?:tienes|hay)\s+en\s+mi\s+memoria\b"
    r"|qu[eé]\s+recuerdas\s+de\s+m[ií]\b"
    r"|mi\s+perfil\b"
    r"|what\s+do\s+you\s+know\s+about\s+me\b"
    r"|tell\s+me\s+about\s+(?:myself|me)\b"
    r"|^\s*who\s+am\s+i\s*\??\s*$"
    r"|summarize\s+(?:what\s+you\s+know\s+about\s+me|my\s+profile)\b"
    r"|describe\s+me\b"
    r"|what'?s\s+my\s+profile\b"
    r"|what\s+do\s+you\s+have\s+on\s+me\b"
    r"|what\s+do\s+you\s+remember\s+about\s+me\b"
    r")",
    re.IGNORECASE,
)


def _is_profile_summary_query(text: str) -> bool:
    """True para preguntas AMPLIAS que piden un resumen del usuario, en
    cualquiera de sus paráfrasis habituales (ES/EN) -- nunca depende del
    nombre de una persona concreta."""
    return bool(_PROFILE_SUMMARY_RE.search((text or "").strip()))


# Categorías de evidencia personal -- patrones GENÉRICOS (posesivos +
# vocabulario de dominio), jamás nombres propios ni frases de una persona
# concreta. Cualquier owner cuyo contenido use estos patrones se beneficia
# igual; no hay trato especial para nadie.
_CATEGORY_PATTERNS = {
    "identity": re.compile(
        r"\bme\s+llamo\b|\bmi\s+nombre\s+es\b|\bsoy\s+de\b|\bvivo\s+en\b|"
        r"\bresido\s+en\b|\bnac[ií]\b|\btengo\s+\d+\s+a[nñ]os\b|\bmi\s+edad\b|"
        r"\bmi\s+ciudad\b|\bmi\s+ubicaci[oó]n\b|\bmi\s+ocupaci[oó]n\b|"
        r"\bmy\s+name\s+is\b|\bi\s*'?m\s+from\b|\bi\s+live\s+in\b|\bi\s+was\s+born\b|"
        r"\bi\s+am\s+\d+\s+years?\s+old\b|\bmy\s+age\b|\bmy\s+occupation\b",
        re.IGNORECASE,
    ),
    "preferences": re.compile(
        r"\bme\s+gusta\b|\bme\s+encanta\b|\bprefiero\b|\bno\s+me\s+gusta\b|"
        r"\bmi\s+favorit[oa]\b|\bodio\b|\bdetesto\b|"
        r"\bi\s+like\b|\bi\s+love\b|\bi\s+prefer\b|\bi\s+hate\b|\bi\s+dislike\b|"
        r"\bmy\s+favorite\b",
        re.IGNORECASE,
    ),
    "relationships": re.compile(
        r"\bmi\s+(?:esposa|esposo|pareja|novi[ao]|hij[ao]|madre|padre|mam[aá]|pap[aá]|"
        r"herman[ao]|amig[ao]|jefe|colega|familia)\b|\btrabajo\s+con\b|\bvivo\s+con\b|"
        r"\bmy\s+(?:wife|husband|partner|girlfriend|boyfriend|son|daughter|mother|"
        r"father|mom|dad|brother|sister|friend|boss|colleague|family)\b",
        re.IGNORECASE,
    ),
    "projects": re.compile(
        r"\bestoy\s+trabajando\s+en\b|\bmi\s+proyecto\b|\bestoy\s+construyendo\b|"
        r"\bestoy\s+desarrollando\b|\bmi\s+empresa\b|\bmi\s+startup\b|\bmi\s+negocio\b|"
        r"\bi'?m\s+working\s+on\b|\bmy\s+project\b|\bi'?m\s+building\b|"
        r"\bi'?m\s+developing\b|\bmy\s+(?:company|startup|business)\b",
        re.IGNORECASE,
    ),
    "interests": re.compile(
        r"\bme\s+interesa\b|\bme\s+apasiona\b|\bmi\s+hobby\b|\bmis\s+hobbies\b|"
        r"\ben\s+mi\s+tiempo\s+libre\b|\bme\s+dedico\s+a\b|"
        r"\bi'?m\s+interested\s+in\b|\bi'?m\s+passionate\s+about\b|\bmy\s+hobby\b|"
        r"\bin\s+my\s+free\s+time\b",
        re.IGNORECASE,
    ),
}
_CATEGORY_ORDER = ("identity", "relationships", "projects", "preferences", "interests")


def _classify_star_category(content: str) -> Optional[str]:
    """Devuelve la primera categoría de evidencia personal que coincide con
    `content`, o ``None`` si no encaja en ninguna (contenido genérico/otro
    -- sigue siendo elegible vía la dimensión de "contexto reciente")."""
    for name in _CATEGORY_ORDER:
        if _CATEGORY_PATTERNS[name].search(content or ""):
            return name
    return None


def _layer_weight(layer: str) -> float:
    return {"core": 1.0, "mid": 0.6, "outer": 0.3}.get(layer, 0.3)


def _star_scores(star, now: float) -> dict:
    """Descompone los 4 ejes de ranking pedidos -- relevancia, confianza,
    importancia y actualidad -- en valores 0..1, y su combinación.

    - confianza: ``gravity_score`` real de la star (masa gravitacional
      acumulada -- conexiones, coherencia, activación).
    - importancia: capa (core/mid/outer) + repetición (evidencia
      reforzada por recurrencia real, nunca inventada).
    - actualidad: recencia de ``last_activated``/``timestamp`` con
      decaimiento suave (ventana de 30 días).
    - relevancia: fuerza de la coincidencia de categoría (más marcadores
      encontrados → más relevante para ese eje de perfil).
    """
    confidence = max(0.0, min(1.0, float(getattr(star, "gravity_score", 0.0) or 0.0)))
    importance = min(
        1.0,
        _layer_weight(getattr(star, "layer", "outer"))
        + 0.05 * min(getattr(star, "repetition_count", 1) or 1, 6),
    )
    last_active = getattr(star, "last_activated", 0.0) or 0.0
    ts = getattr(star, "timestamp", 0.0) or 0.0
    reference = max(last_active, ts)
    days_ago = max((now - reference) / 86400.0, 0.0) if reference else 3650.0
    recency = 1.0 / (1.0 + days_ago / 30.0)
    return {"confidence": confidence, "importance": importance, "recency": recency}


def _composite_score(scores: dict, relevance: float) -> float:
    return (
        0.30 * relevance
        + 0.25 * scores["confidence"]
        + 0.25 * scores["importance"]
        + 0.20 * scores["recency"]
    )


def _normalize_for_dedup(content: str) -> str:
    return re.sub(r"[^\w\s]", "", (content or "").strip().lower())


@dataclass
class EvidenceFragment:
    """Una pieza de evidencia entregada al sintetizador -- trazabilidad
    explícita exigida: de dónde vino (``source``), su identificador real
    (``id``), a quién pertenece (``owner``/``channel``), bajo qué
    categoría se clasificó y el contenido literal (``content``).
    """
    id: str
    source: str          # "star" (vectrax.db) | "core_memory" (vault/user_memory.db)
    owner: str
    channel: str
    category: str
    content: str
    score: float


# Categorías de vectrax.core_memory (memoria CANÓNICA -- hechos ya
# extraídos, validados por categoría y reforzados por repetición real,
# ver vectrax/core_memory.py) que cuentan como evidencia explícita para un
# resumen de perfil. "fact" se excluye deliberadamente: es el cajón
# genérico de MENOR confianza del propio esquema (peso máximo 0.5, el más
# bajo de todas las categorías en `core_memory.CATEGORIES`) -- texto
# conversacional sin extractor de identidad explícito detrás, nunca un
# hecho validado. Esta exclusión es genérica (por diseño del esquema), no
# específica de ningún owner.
_CANONICAL_EXCLUDED_CATEGORIES = {"fact"}

# vectrax.core_memory usa nombres de categoría en singular
# ("relationship", "preference") mientras que las categorías de stars usan
# plural ("relationships", "preferences") -- ver _CATEGORY_ORDER arriba.
# Se mapean explícitamente para que ambas fuentes converjan en el MISMO
# bucket (y así compitan por ranking/dedup juntas) cuando son equivalentes.
# Categorías canónicas sin equivalente claro en stars ("work", "goal",
# "emotion", "habit") conservan su propio nombre -- no se fuerzan a encajar
# en una categoría que no las describe bien.
_CANONICAL_TO_STAR_CATEGORY = {
    "identity": "identity",
    "relationship": "relationships",
    "preference": "preferences",
}


def _fetch_canonical_memory_entries(owner: str, owner_raw: str = "") -> list:
    """Memoria CANÓNICA existente (``vectrax.core_memory`` sobre
    ``vault/user_memory.db``) -- ver hallazgo de la investigación real:
    esta tabla tenía hechos validados por categoría (identity, work,
    relationship, preference, goal...) que la recuperación por stars
    NUNCA consultaba.

    ``core_memory`` se llena históricamente con la identidad CRUDA del
    canal externo (p.ej. ``tg:<id>``, ver ``absorb()`` invocado desde
    ``core/operator/external_gateway.py``), no con el owner canónico
    post-alias ("mario"). Por eso se prueban AMBAS claves -- ``owner_raw``
    primero, luego ``owner`` -- deduplicando por id de entrada. Esto es
    genérico: aplica igual a cualquier usuario cuyo owner_raw sea distinto
    de su owner canónico, no solo al creador.

    Nunca lanza; devuelve ``[]`` si el módulo o el usuario no tienen
    entradas.
    """
    try:
        from vectrax.core_memory import get_core_entries
    except Exception:
        return []

    keys = []
    for key in (owner_raw, owner):
        if key and key not in keys:
            keys.append(key)

    entries: list = []
    seen_entry_ids: set = set()
    for key in keys:
        try:
            for e in get_core_entries(key):
                if e["category"] in _CANONICAL_EXCLUDED_CATEGORIES:
                    continue
                if e["id"] in seen_entry_ids:
                    continue
                seen_entry_ids.add(e["id"])
                entries.append((e, key))  # (entry, la clave real bajo la que vive)
        except Exception as exc:
            logger.debug("_fetch_canonical_memory_entries(%r) failed: %s", key, exc)
    return entries


def _select_profile_summary_stars(
    stars: list,
    owner: str = "",
    channel: str = "",
    owner_raw: str = "",
    per_category: int = 3,
    recent_n: int = 3,
    max_total: int = 12,
) -> List[EvidenceFragment]:
    """Muestra diversa y priorizada para una consulta amplia de perfil.

    Recupera SOLO donde hay evidencia real, de DOS fuentes:
      1. Memoria CANÓNICA (``vectrax.core_memory`` / vault/user_memory.db)
         -- hechos ya extraídos y validados por categoría
         (identity/relationship/work/preference/goal/emotion/habit), con
         su propio ``weight`` como confianza explícita. Prioridad máxima:
         es la fuente más confiable disponible.
      2. Stars (``vectrax.db``) clasificadas por patrones genéricos en:
         identidad, preferencias, relaciones, proyectos, intereses, más
         una dimensión de "contexto reciente" que cubre cualquier
         contenido -- así ningún dato real se descarta solo por no
         matchear un patrón.

    Ordena cada grupo por relevancia+confianza+importancia+actualidad,
    elimina duplicados (por id y por contenido normalizado -- incluso
    entre las dos fuentes) y limita el total para mantener la síntesis
    enfocada.

    No cambia el almacenamiento ni el aislamiento: la memoria canónica se
    consulta con las MISMAS identidades (``owner``/``owner_raw``) que ya
    aislaron la lista `stars` recuperada por el caller vía
    ``db.get_all_stars(channel, owner)`` -- nunca se amplía el alcance.
    """
    now = time.time()
    buckets: dict = {name: [] for name in _CATEGORY_ORDER}
    all_scored: List[Tuple[EvidenceFragment, float]] = []

    # -- 1) Memoria CANÓNICA primero -- prioridad máxima por diseño -----
    canonical = _fetch_canonical_memory_entries(owner, owner_raw)
    for entry, matched_key in canonical:
        cat = entry["category"]
        confidence = max(0.0, min(1.0, float(entry.get("weight", 0.0) or 0.0)))
        # Reforzada por confirmación repetida real (times_confirmed), nunca
        # inventada -- mismo principio que "importancia" para stars.
        importance = min(1.0, 0.5 + 0.1 * min(entry.get("times_confirmed", 1) or 1, 5))
        recency = 1.0  # ya es la forma más reciente/consolidada conocida del hecho
        relevance = 0.9  # categoría ya validada explícitamente, no inferida por regex
        composite = 0.30 * relevance + 0.25 * confidence + 0.25 * importance + 0.20 * recency
        frag = EvidenceFragment(
            id=f"core_memory:{entry['id']}",
            source="core_memory",
            owner=matched_key,  # identidad REAL bajo la que vive esta entrada
            channel=channel,
            category=cat,
            content=entry["content"],
            score=composite,
        )
        bucket_key = _CANONICAL_TO_STAR_CATEGORY.get(cat, cat)
        buckets.setdefault(bucket_key, []).append((frag, composite))
        all_scored.append((frag, composite))

    if not stars and not canonical:
        return []

    # -- 2) Stars, exactamente como antes --------------------------------
    for s in stars:
        content = getattr(s, "content", "") or ""
        scores = _star_scores(s, now)
        cat = _classify_star_category(content)
        frag = EvidenceFragment(
            id=str(getattr(s, "id", "")),
            source="star",
            owner=getattr(s, "owner", owner),
            channel=getattr(s, "channel", channel),
            category=cat or "recent_context",
            content=content,
            score=0.0,
        )
        if cat:
            hits = len(_CATEGORY_PATTERNS[cat].findall(content))
            relevance = min(1.0, 0.6 + 0.2 * hits)
            composite = _composite_score(scores, relevance)
            frag.score = composite
            buckets[cat].append((frag, composite))
        # Contexto reciente: considera TODA star (con o sin categoría) --
        # relevancia moderada fija, priorizada por actualidad/confianza.
        recent_relevance = 0.5
        recent_composite = _composite_score(scores, recent_relevance)
        recent_frag = frag if not cat else EvidenceFragment(
            id=frag.id, source="star", owner=frag.owner, channel=frag.channel,
            category="recent_context", content=frag.content, score=recent_composite,
        )
        all_scored.append((recent_frag, recent_composite))

    selected: List[EvidenceFragment] = []
    seen_ids: set = set()
    seen_content: set = set()

    def _add(candidates: List[Tuple[EvidenceFragment, float]], limit: int) -> None:
        for frag, score in sorted(candidates, key=lambda t: t[1], reverse=True):
            if len(selected) >= max_total:
                return
            dedup_key = (frag.source, frag.id)
            if dedup_key in seen_ids:
                continue
            norm = _normalize_for_dedup(frag.content)
            if norm and norm in seen_content:
                continue
            selected.append(frag)
            seen_ids.add(dedup_key)
            if norm:
                seen_content.add(norm)
            limit -= 1
            if limit <= 0:
                return

    # 1) Dimensiones categóricas -- solo las que tienen evidencia real.
    #    La memoria canónica ya vive en estos buckets junto a las stars,
    #    y al ordenar por score gana naturalmente (confidence/importance
    #    más altos por diseño arriba).
    for name in buckets:
        if buckets[name]:
            _add(buckets[name], per_category)

    # 2) Contexto reciente -- red de seguridad genérica: cubre usuarios
    #    cuyo contenido no matchea ninguna categoría (memoria escasa/atípica)
    #    y refuerza actualidad para los que sí tienen categorías.
    if all_scored:
        _add(all_scored, recent_n)

    return selected[:max_total]


# ---------------------------------------------------------------------------
# 2. LOCAL RESOLVER — search existing memory stars
# ---------------------------------------------------------------------------

def resolve_local(
    text: str,
    channel: str,
    owner: str,
    top_k: int = 5,
    threshold: float = 0.40,
    execution_context=None,
    owner_raw: str = "",
) -> Resolution:
    """
    Search the user's existing stars for content relevant to the question.

    Two retrieval strategies, selected by intent (see
    ``_is_profile_summary_query()``):
      - Consulta puntual (por defecto): embedding + similitud coseno
        contra el texto literal -- igual que siempre.
      - Consulta AMPLIA de perfil ("qué sabes de mí", "cuéntame sobre mí",
        y paráfrasis): una similitud contra el texto literal de la
        pregunta rinde mal ("qué sabes de mí" no se parece semánticamente
        a hechos concretos como "vivo en X"). En su lugar,
        ``_select_profile_summary_stars()`` arma una muestra diversa y
        priorizada por evidencia real de DOS fuentes -- memoria CANÓNICA
        (``vectrax.core_memory``, ver esa función) y stars -- ordenada por
        relevancia/confianza/importancia/actualidad, con deduplicación.
        Ninguna de las dos rutas cambia lo que sigue después (síntesis,
        aislamiento).

    Retrieval scope (unchanged, load-bearing for isolation): stars are
    fetched via ``db.get_all_stars(channel=channel, owner=owner)`` --
    exactly the ``channel``/``owner`` (tenant/user identity) the caller
    passes in. Everything below only changes how the ALREADY-ISOLATED
    fragments for THIS owner/channel are turned into a user-facing answer;
    it never widens or changes which stars get read.

    `owner_raw`: identidad CRUDA pre-alias del caller (p.ej. ``tg:<id>``
    para Telegram), opcional. Solo se usa para localizar memoria CANÓNICA
    (``vectrax.core_memory``, ver ``_fetch_canonical_memory_entries()``)
    que históricamente se guarda bajo esa identidad cruda -- nunca amplía
    ni cambia el alcance de `db.get_all_stars()` arriba. Si se omite
    (comportamiento actual de la mayoría de los callers), la memoria
    canónica se busca solo bajo ``owner``.

    `execution_context`: propagado tal cual a la interpretación LLM
    (frontera "llm", ver ``_interpret_with_llm()``). Opcional -- si el
    caller no lo pasa (comportamiento actual de ``NucleusAuthority``), la
    interpretación sigue funcionando igual que en ``resolve_online()`` sin
    execution_context.
    """
    from vectrax import db

    db.init_db()
    stars = db.get_all_stars(channel=channel, owner=owner)
    star_map = {s.id: s for s in stars}

    is_profile_query = _is_profile_summary_query(text)

    if is_profile_query:
        fragments = _select_profile_summary_stars(
            stars, owner=owner, channel=channel, owner_raw=owner_raw,
        )
        if not fragments:
            lang = _detect_lang(text)
            labels = _get_labels(lang)
            return Resolution(
                mode="local",
                answer="No relevant memory found.",
                sovereign_answer=labels["no_memory"],
                context_stars=0,
                top_score=0.0,
            )
        top_score = fragments[0].score if fragments else 0.0
        # Evidencia EXPLÍCITA entregada al sintetizador: fuente (star en
        # vectrax.db vs core_memory en vault/user_memory.db), id real,
        # owner bajo el que vive, categoría asignada y extracto -- nunca
        # solo el contenido crudo. Campo interno/depuración, nunca se
        # muestra al usuario (ver `sovereign_answer` para eso).
        debug_parts = [
            f"• [{f.source}] id={f.id} owner={f.owner} category={f.category} "
            f"score={f.score:.0%} :: {f.content[:160]}"
            for f in fragments
        ]
        clean_parts = [f.content for f in fragments]
        answer = "Evidence given to synthesizer:\n\n" + "\n".join(debug_parts)
        context_stars = len(fragments)
        lang = _detect_lang(text)
    else:
        from vectrax.embeddings import decode_embedding, embed, find_similar
        query_vec = embed(text)
        star_embeddings: List[Tuple[str, object]] = [
            (s.id, decode_embedding(s.embedding))
            for s in stars
            if s.embedding is not None
        ]
        matches = find_similar(query_vec, star_embeddings, threshold=threshold)
        matches = matches[:top_k]

        if not matches:
            lang = _detect_lang(text)
            labels = _get_labels(lang)
            return Resolution(
                mode="local",
                answer="No relevant memory found.",
                sovereign_answer=labels["no_memory"],
                context_stars=0,
                top_score=0.0,
            )

        top_score = matches[0][1] if matches else 0.0

        # Build debug answer (with scores) and sovereign answer (clean)
        debug_parts = []
        clean_parts = []
        for star_id, score in matches:
            s = star_map.get(star_id)
            if s:
                debug_parts.append(f"• {s.content} (relevance: {score:.0%}, layer: {s.layer})")
                clean_parts.append(s.content)

        answer = "Based on your memory:\n\n" + "\n".join(debug_parts)
        context_stars = len(matches)
        lang = _detect_lang(text)

    # ══ COHERENT SYNTHESIS ══
    # Antes: los fragmentos recuperados (ya aislados por owner/channel, ver
    # docstring arriba) se mostraban como una lista cruda de viñetas
    # ("Esto es lo que tengo en tu memoria:\n\n• frag1\n• frag2..."). El
    # requisito real es una respuesta directa y natural, no un volcado de
    # fragmentos sueltos. Mismo patrón ya usado en resolve_online(): LLM
    # como intérprete principal (mode="memory", prompt dedicado -- ver
    # _INTERPRET_MEMORY_PROMPT_*), con fallback exacto al comportamiento
    # anterior (_synthesize_local, viñetas) si el LLM no está disponible o
    # el gate constitucional lo bloquea -- nunca se pierde la capacidad de
    # responder, solo mejora la forma cuando es posible.
    sovereign = _interpret_with_llm(
        text, clean_parts, lang=lang, execution_context=execution_context,
        mode="memory",
    )
    if not sovereign:
        sovereign = _synthesize_local(clean_parts, lang=lang)

    return Resolution(
        mode="local",
        answer=answer,
        sovereign_answer=sovereign,
        context_stars=context_stars,
        top_score=top_score,
    )


# ---------------------------------------------------------------------------
# LANGUAGE DETECTION
# ---------------------------------------------------------------------------

_ES_MARKERS = re.compile(
    r"[áéíóúñü¿¡]"
    r"|\b(el|la|los|las|del|de|una|unos|un|es|fue|son|est[aá]|por|para|con|como|que|"
    r"pero|m[aá]s|sobre|entre|desde|hasta|tiene|puede|hab[ií]a|ser|tambi[eé]n|y|o|se|"
    r"hoy|hay|dame|busca|noticias?|tambi[eé]n|mi|al|lo|ya|yo|tu|su|"
    r"qu[eé]|c[oó]mo|cu[aá]ndo|d[oó]nde|cu[aá]l|cu[aá]nto)\b",
    re.IGNORECASE,
)

# Palabras unívocamente españolas (cualquiera es suficiente para clasificar como ES)
_ES_UNAMBIGUOUS = re.compile(
    r"\b(hoy|dame|noticias?|busca|buscar|encuentra|ayuda|gracias|por\s+favor|"
    r"también|todavía|siempre|nunca|ahora|antes|después|mientras|aunque|"
    r"porque|entonces|además|sin\s+embargo|"
    r"clima|tiempo|temperatura|lluvia|sol|calor|fr[ií]o|pron[oó]stico)\b",
    re.IGNORECASE,
)

# Fast-path: unambiguously Spanish verb/interrogative at start of text
_ES_VERB_START = re.compile(
    r"^(?:explica|explícame|explicame|dime|analiza|compara|resume|resúmeme|"
    r"quién|qué|cómo|cuándo|dónde|cuál|cuánto)\b",
    re.IGNORECASE,
)


def _detect_lang(text: str) -> str:
    """Detect language of text. Returns 'es' or 'en'."""
    stripped = text.strip()
    # Fast-path: starts with unambiguously Spanish verb/interrogative
    if _ES_VERB_START.match(stripped):
        return "es"
    # Fast-path: contains unambiguously Spanish word
    if _ES_UNAMBIGUOUS.search(stripped):
        return "es"
    es_hits = len(_ES_MARKERS.findall(stripped))
    word_count = max(len(stripped.split()), 1)
    # If >10% of words trigger ES markers → Spanish (threshold lowered from 15%)
    if es_hits / word_count > 0.10:
        return "es"
    return "en"


def _sentence_lang(sentence: str) -> str:
    """Detect language of a single sentence."""
    return _detect_lang(sentence)


# Labels by language
_LABELS = {
    "es": {
        "key_points": "Puntos clave:",
        "comparison": "Comparación:",
        "no_info": "No encontré información suficiente para responder eso con certeza.",
        "no_memory": "No tengo información sobre eso en mi memoria.",
        "memory_single": "En tu memoria encontré esto:",
        "memory_multi": "Esto es lo que tengo en tu memoria:",
        "memory_extra": "registros adicionales",
        "registered": "Registrado.",
        "updated": "Actualizado.",
        "disambig": "puede referirse a:",
    },
    "en": {
        "key_points": "Key points:",
        "comparison": "Comparison:",
        "no_info": "I couldn't find enough information to answer that with certainty.",
        "no_memory": "I don't have information about that in my memory.",
        "memory_single": "Found this in your memory:",
        "memory_multi": "Here's what I have in your memory:",
        "memory_extra": "additional records",
        "registered": "Registered.",
        "updated": "Updated.",
        "disambig": "can refer to:",
    },
}


def _get_labels(lang: str) -> dict:
    return _LABELS.get(lang, _LABELS["en"])


# ---------------------------------------------------------------------------
# SOVEREIGN SYNTHESIS — clean answers without source attribution
# ---------------------------------------------------------------------------

# Boilerplate phrases to strip from snippets
_BOILERPLATE = re.compile(
    r"(click here|read more|learn more|subscribe|sign up|log in|cookie|"
    r"privacy policy|terms of service|advertisement|sponsored|\.\.\.$)",
    re.IGNORECASE,
)

# CTA / promotional sentence starts to discard
_CTA_STARTS = re.compile(
    r"^(descubre|discover|encuentra|find out|explore|visit|shop|buy|get|"
    r"aprende|check out|see |view |watch |haz |mira |conoce |entra )",
    re.IGNORECASE,
)

# Article / blog meta phrases — stripped from snippets before synthesis
_ARTICLE_META = re.compile(
    r"(?:en este artículo|in this article|in this post|in this guide|"
    r"exploraremos|we will explore|we'll explore|we explore|"
    r"vamos a ver|vamos a explorar|a continuación veremos|"
    r"in this tutorial|in this section|as we'll see|"
    r"sigue leyendo|keep reading|read on|"
    r"te explicamos|te contamos|te mostramos|"
    r"hoy hablaremos|today we'll|let's take a look|"
    r"let's explore|let's dive|let us explore|"
    r"this article explains|este artículo explica|"
    r"here we discuss|aquí discutimos|aquí explicamos|"
    r"you'll learn|aprenderás|en esta guía|in this review)",
    re.IGNORECASE,
)

# First-person article voice — discard entire sentence if it starts this way
_ARTICLE_VOICE = re.compile(
    r"^(?:en este artículo|in this article|this article|este artículo|"
    r"here we |aquí |in this post|in this guide|en esta guía|"
    r"let's |vamos a explorar)",
    re.IGNORECASE,
)


def _strip_article_meta(text: str) -> str:
    """Remove article/blog meta phrases from text."""
    cleaned = _ARTICLE_META.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    cleaned = cleaned.lstrip(",;:. ")
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


# ---------------------------------------------------------------------------
# COMPLEXITY DETECTION
# ---------------------------------------------------------------------------

_SIMPLE_Q = re.compile(
    r"^(?:qué es|que es|what is|who is|quién es|quien es|"
    r"define|what does\b.+\bmean|qué significa|qué son|what are)\b",
    re.IGNORECASE,
)

_COMPARISON_Q = re.compile(
    r"(?:\bcompara\b|\bcompare\b|\bvs\b|\bversus\b|\bdiferencia entre\b|"
    r"\bdifference between\b|\bfrente a\b)",
    re.IGNORECASE,
)


def _query_complexity(text: str) -> str:
    """
    Classify question complexity.
    Returns: 'simple', 'comparison', or 'complex'.
    """
    t = text.strip()
    if _COMPARISON_Q.search(t):
        return "comparison"
    if _SIMPLE_Q.match(t):
        return "simple"
    # Short queries without complex verbs → simple
    if len(t.split()) <= 4 and not re.search(
        r"\b(explica|explain|analiza|analyze|cómo funciona|como funciona|"
        r"how does|por qué|por que|why)\b", t, re.I,
    ):
        return "simple"
    return "complex"


# ---------------------------------------------------------------------------
# DISAMBIGUATION
# ---------------------------------------------------------------------------

_PERSON_IND = re.compile(
    r"\b(born|died|inventor|scientist|physicist|engineer|biography|"
    r"nació|murió|inventó|científico|físico|ingeniero|biografía|"
    r"was a |fue un |fue una )",
    re.IGNORECASE,
)

_COMPANY_IND = re.compile(
    r"\b(Inc|Corp|LLC|company|empresa|stock|revenue|"
    r"products|productos|founded|fundada|headquarters|"
    r"sede|market|shares|CEO|startup|brand|marca)",
    re.IGNORECASE,
)

_SCIENCE_IND = re.compile(
    r"\b(unit|unidad|formula|fórmula|physics|física|chemistry|química|"
    r"SI unit|measurement|medida|equation|ecuación|magnetic|magnético)",
    re.IGNORECASE,
)


def _detect_ambiguity(
    query: str, sources: List[Source], lang: str,
) -> Optional[str]:
    """
    Detect if search results suggest multiple interpretations for a short query.
    Returns a disambiguation preamble or None.
    """
    # Strip common question prefixes to extract the core subject
    subject = re.sub(
        r"^(?:qué es|que es|what is|who is|quién es|quien es|"
        r"define|dime qué es|dime que es|tell me about|tell me what is|"
        r"quien inventó|quién inventó|who invented|who created)\s+",
        "", query, flags=re.I,
    ).strip().rstrip("?").strip()

    # Only check short subjects (1–3 words)
    if len(subject.split()) > 3:
        return None

    # Skip if the query already has disambiguating context
    if re.search(
        r"\b(empresa|company|inventor|persona|person|unit|unidad|"
        r"marca|brand|car|auto|coche|molécula|molecule|supplement|suplemento)\b",
        query, re.I,
    ):
        return None

    # Scan source text for category signals
    all_text = " ".join(s.title + " " + s.snippet for s in sources)
    cats: List[Tuple[str, str]] = []
    if _PERSON_IND.search(all_text):
        cats.append(("persona/inventor" if lang == "es" else "person/inventor",))
    if _COMPANY_IND.search(all_text):
        cats.append(("empresa/marca" if lang == "es" else "company/brand",))
    if _SCIENCE_IND.search(all_text):
        cats.append(("concepto científico" if lang == "es" else "scientific concept",))

    if len(cats) < 2:
        return None

    labels = _get_labels(lang)
    lines = [f"\"{subject}\" {labels['disambig']}"]
    for (cat_label,) in cats:
        lines.append(f"• {subject} ({cat_label})")
    return "\n".join(lines)


def _clean_snippet(text: str) -> str:
    """Strip boilerplate, article meta, and trailing junk from a snippet."""
    text = _BOILERPLATE.sub("", text).strip()
    text = _strip_article_meta(text)
    # Remove trailing fragments (incomplete sentences ending without punctuation)
    if text and text[-1] not in ".!?\"')'":
        last_period = max(text.rfind("."), text.rfind("!"), text.rfind("?"))
        if last_period > len(text) // 3:
            text = text[: last_period + 1]
    return text.strip()


def _extract_sentences(
    snippets: List[str],
    max_sentences: int = 12,
    lang: str = "es",
) -> List[str]:
    """Extract, filter by language, and deduplicate informative sentences."""
    all_sentences = []
    for snippet in snippets:
        cleaned = _clean_snippet(snippet)
        if not cleaned:
            continue
        sents = re.split(r"(?<=[.!?])\s+", cleaned)
        for s in sents:
            s = s.strip()
            if len(s) < 40:
                continue
            if s.endswith("?") or s.startswith("¿"):
                continue
            if _CTA_STARTS.match(s):
                continue
            if _ARTICLE_VOICE.match(s):
                continue
            if s.startswith("(") and len(s) < 60:
                continue
            all_sentences.append(s)

    # Filter by target language — keep only sentences matching the query language
    filtered = [s for s in all_sentences if _sentence_lang(s) == lang]
    # If filtering removed everything, fall back to unfiltered
    if not filtered:
        filtered = all_sentences

    # Deduplicate by prefix similarity
    seen = set()
    unique = []
    for s in filtered:
        key = s[:50].lower()
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique[:max_sentences]


def _synthesize_online(
    snippets: List[str],
    lang: str = "es",
    query: str = "",
    sources: Optional[List[Source]] = None,
) -> str:
    """
    Build a structured, depth-aware answer from web snippets.

    Depth is determined by query complexity:
      simple     → definition (1–2 sentences) + max 3 key facts
      comparison → lead paragraph + comparison bullets
      complex    → full lead (2–3 sentences) + key points + context

    If the query is ambiguous (short term with divergent results),
    a disambiguation preamble is prepended.

    All content in the same language as the user's question.
    No source names, no links, no engine references, no article meta.
    """
    labels = _get_labels(lang)
    sentences = _extract_sentences(snippets, max_sentences=12, lang=lang)
    if not sentences:
        return labels["no_info"]

    complexity = _query_complexity(query) if query else "complex"
    parts: List[str] = []

    # Disambiguation preamble (if applicable)
    if sources and query:
        disambig = _detect_ambiguity(query, sources, lang)
        if disambig:
            parts.append(disambig)
            parts.append("")

    # --- SIMPLE: definition + max 3 bullets ---
    if complexity == "simple":
        lead = sentences[:2]
        parts.append(" ".join(lead))
        rest = sentences[2:5]
        if rest:
            parts.append("")
            parts.append(labels["key_points"])
            for kp in rest:
                if len(kp) > 200:
                    dot = kp.find(".", 80)
                    if dot > 0:
                        kp = kp[: dot + 1]
                parts.append(f"• {kp}")

    # --- COMPARISON: lead + comparison bullets ---
    elif complexity == "comparison":
        lead = sentences[:2]
        parts.append(" ".join(lead))
        rest = sentences[2:]
        if rest:
            parts.append("")
            parts.append(labels["comparison"])
            for kp in rest[:6]:
                if len(kp) > 250:
                    dot = kp.find(".", 100)
                    if dot > 0:
                        kp = kp[: dot + 1]
                parts.append(f"• {kp}")

    # --- COMPLEX: full lead + key points + context ---
    else:
        lead_count = min(3, len(sentences))
        lead = sentences[:lead_count]
        rest = sentences[lead_count:]

        parts.append(" ".join(lead))

        if rest:
            key_points = rest[:5]
            parts.append("")
            parts.append(labels["key_points"])
            for kp in key_points:
                if len(kp) > 250:
                    dot = kp.find(".", 100)
                    if dot > 0:
                        kp = kp[: dot + 1]
                parts.append(f"• {kp}")

            extra = rest[5:8]
            if extra:
                context = " ".join(extra)
                if len(context) > 30:
                    parts.append("")
                    parts.append(context)

    return "\n".join(parts)


def _synthesize_local(star_contents: List[str], lang: str = "es") -> str:
    """
    Build a structured answer from the user's memory stars.
    Natural voice, no relevance scores, no layer names.
    """
    labels = _get_labels(lang)

    if not star_contents:
        return labels["no_memory"]

    # Single match → direct answer
    if len(star_contents) == 1:
        return f"{labels['memory_single']}\n\n{star_contents[0]}"

    # Multiple matches → structured list
    parts = [labels["memory_multi"]]
    parts.append("")
    for c in star_contents[:5]:
        parts.append(f"• {c}")

    if len(star_contents) > 5:
        parts.append(f"\n(+{len(star_contents) - 5} {labels['memory_extra']})")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 3. ONLINE RESOLVER — Multi-engine search
# ---------------------------------------------------------------------------

_DDG_URL = "https://html.duckduckgo.com/html/"
_USER_AGENT = "Vectrax/1.0 (Cognitive Memory Platform)"


def _search_tavily(query: str, max_results: int = 5) -> List[Source]:
    """
    Search via Tavily API — diseñado para agentes de IA.
    Requiere TAVILY_API_KEY. Tier gratuito: 1000 búsquedas/mes.
    Es el motor principal cuando la clave está disponible.
    """
    import os
    try:
        from dotenv import load_dotenv
        from pathlib import Path
        _env = Path(__file__).resolve().parent.parent / ".env"
        if _env.exists():
            load_dotenv(_env, override=False)
    except ImportError:
        pass

    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return []

    import requests
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
            },
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Tavily search failed: %s", exc)
        return []

    sources: List[Source] = []
    for item in (data.get("results", []))[:max_results]:
        title = item.get("title", "").strip()
        snippet = item.get("content", "").strip()
        url = item.get("url", "")
        if title and snippet:
            sources.append(Source(title=title, url=url, snippet=snippet))
    return sources


# Cache simple en memoria para resultados de búsqueda (5 min TTL)
_search_cache: dict = {}  # query_key → (timestamp, sources, engines)
_SEARCH_CACHE_TTL = 300   # 5 minutos


def _get_cached_search(query: str) -> Optional[Tuple[List[Source], List[str]]]:
    """Retorna resultados cacheados o None si no hay / expiraron."""
    import time
    key = query.lower().strip()[:120]
    entry = _search_cache.get(key)
    if entry and (time.time() - entry[0]) < _SEARCH_CACHE_TTL:
        logger.debug("Search cache HIT: %r", query[:50])
        return entry[1], entry[2]
    return None


def _cache_search(query: str, sources: List[Source], engines: List[str]) -> None:
    """Guarda resultados en cache. Limpia entradas viejas si hay demasiadas."""
    import time
    if len(_search_cache) > 200:
        # Limpiar el 50% más antiguo
        sorted_keys = sorted(_search_cache, key=lambda k: _search_cache[k][0])
        for k in sorted_keys[: len(sorted_keys) // 2]:
            _search_cache.pop(k, None)
    key = query.lower().strip()[:120]
    _search_cache[key] = (time.time(), sources, engines)


def _enrich_with_jina(
    sources: List[Source],
    max_enrich: int = 2,
    skip_if_rich: bool = True,
) -> List[Source]:
    """
    Jina Reader — enriquece resultados con snippets cortos.

    Optimizaciones:
    - skip_if_rich: si la mayoría de snippets ya son ricos (>150 chars), no hace nada
    - Paralelo: fetch de múltiples URLs en hilos concurrentes
    - Timeout reducido: 2s por URL (antes 6s)
    """
    import requests
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Calcular cuántos snippets son cortos
    short_count = sum(1 for s in sources if len(s.snippet) < 150)

    # Si la mayoría de snippets ya son ricos, omitir Jina completamente
    if skip_if_rich and short_count == 0:
        return sources
    if skip_if_rich and short_count <= 1 and len(sources) >= 2:
        # Solo 1 corto de 2+ resultados: no merece el costo
        return sources

    # Identificar cuales enriquecer
    to_enrich = [
        (i, src) for i, src in enumerate(sources)
        if len(src.snippet) < 150 and src.url.startswith("http")
    ][:max_enrich]

    if not to_enrich:
        return sources

    def _fetch_jina(i: int, src: Source):
        try:
            r = requests.get(
                f"https://r.jina.ai/{src.url}",
                headers={"Accept": "text/plain", "X-Return-Format": "text"},
                timeout=2,  # reducido de 6s a 2s
            )
            if r.status_code == 200:
                content = r.text.strip()[:600]
                if content and len(content) > len(src.snippet):
                    return i, Source(title=src.title, url=src.url, snippet=content)
        except Exception:
            pass
        return i, src

    # Fetch en paralelo
    enriched = list(sources)
    with ThreadPoolExecutor(max_workers=max_enrich) as executor:
        futures = {executor.submit(_fetch_jina, i, src): i for i, src in to_enrich}
        for future in as_completed(futures, timeout=3):
            try:
                idx, new_src = future.result()
                if new_src != sources[idx]:
                    enriched[idx] = new_src
                    logger.info("Jina enriched: %s", new_src.url[:60])
            except Exception:
                pass

    return enriched


def _search_duckduckgo(query: str, max_results: int = 5) -> List[Source]:
    """
    Search DuckDuckGo HTML endpoint (no API key required).
    Parse results from the HTML response using regex (no BeautifulSoup needed).
    """
    import requests

    try:
        resp = requests.post(
            _DDG_URL,
            data={"q": query},
            headers={"User-Agent": _USER_AGENT},
            timeout=5,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("DuckDuckGo search failed: %s", exc)
        return []

    body = resp.text
    sources = []

    # Extract result blocks: <a class="result__a" href="...">title</a>
    # and <a class="result__snippet">snippet</a>
    result_blocks = re.findall(
        r'<a[^>]+class="result__a"[^>]+href="([^"]*)"[^>]*>(.*?)</a>'
        r'.*?'
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        body,
        re.DOTALL,
    )

    for url, raw_title, raw_snippet in result_blocks[:max_results]:
        # DuckDuckGo wraps URLs in a redirect — extract the actual URL
        actual_url = url
        uddg_match = re.search(r'uddg=([^&]+)', url)
        if uddg_match:
            from urllib.parse import unquote
            actual_url = unquote(uddg_match.group(1))

        title = _strip_html(raw_title).strip()
        snippet = _strip_html(raw_snippet).strip()

        if title and snippet:
            sources.append(Source(title=title, url=actual_url, snippet=snippet))

    return sources


def _search_brave(query: str, max_results: int = 5) -> List[Source]:
    """
    Search via Brave Search API (requires BRAVE_API_KEY env var).
    Fallback engine — only used when DuckDuckGo returns insufficient results.
    """
    import os
    try:
        from dotenv import load_dotenv
        from pathlib import Path
        _env = Path(__file__).resolve().parent.parent / ".env"
        if _env.exists():
            load_dotenv(_env, override=False)
    except ImportError:
        pass
    api_key = os.environ.get("BRAVE_API_KEY", "")
    if not api_key:
        return []

    import requests
    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
            },
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Brave search failed: %s", exc)
        return []

    sources: List[Source] = []
    for item in (data.get("web", {}).get("results", []))[:max_results]:
        title = item.get("title", "").strip()
        snippet = item.get("description", "").strip()
        url = item.get("url", "")
        if title and snippet:
            sources.append(Source(title=title, url=url, snippet=snippet))
    return sources


def _search_google_cse(query: str, max_results: int = 5) -> List[Source]:
    """
    Search via Google Custom Search Engine API.
    Requires GOOGLE_CSE_CX env var + API key.
    API key: uses GOOGLE_CSE_KEY, fallback to GOOGLE_PLACES_API_KEY.
    Fallback engine — only used when other engines return insufficient results.
    """
    import os
    try:
        from dotenv import load_dotenv
        from pathlib import Path
        _env = Path(__file__).resolve().parent.parent / ".env"
        if _env.exists():
            load_dotenv(_env, override=False)
    except ImportError:
        pass

    api_key = os.environ.get("GOOGLE_CSE_KEY", "") or os.environ.get("GOOGLE_PLACES_API_KEY", "")
    cx = os.environ.get("GOOGLE_CSE_CX", "")
    if not api_key or not cx:
        return []

    import requests
    try:
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": api_key,
                "cx": cx,
                "q": query,
                "num": min(max_results, 10),
            },
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Google CSE search failed: %s", exc)
        return []

    sources: List[Source] = []
    for item in (data.get("items", []))[:max_results]:
        title = item.get("title", "").strip()
        snippet = item.get("snippet", "").strip()
        url = item.get("link", "")
        if title and snippet:
            sources.append(Source(title=title, url=url, snippet=snippet))
    return sources


def _search_multi_engine(
    query: str,
    max_results: int = 5,
    min_sources: int = 3,
) -> Tuple[List[Source], List[str]]:
    """
    Multi-engine search con prioridad automática.

    Prioridad:
      1. Tavily (si TAVILY_API_KEY disponible) — motor principal para IA
      2. DuckDuckGo (siempre, sin clave) — fallback gratuito
      3. Brave Search (si BRAVE_API_KEY disponible)
      4. Google CSE (si GOOGLE_CSE_KEY + GOOGLE_CSE_CX disponibles)
      5. Jina Reader — enriquece snippets cortos con contenido real de la página

    Deduplica por URL entre motores.
    Retorna (sources, engines_used).
    """
    all_sources: List[Source] = []
    seen_urls: set = set()
    engines_used: List[str] = []

    def _add_sources(new_sources: List[Source], engine_name: str) -> None:
        nonlocal all_sources, seen_urls, engines_used
        added = 0
        for src in new_sources:
            normalized = src.url.rstrip("/").lower()
            if normalized not in seen_urls:
                seen_urls.add(normalized)
                all_sources.append(src)
                added += 1
        if added > 0:
            engines_used.append(engine_name)
            logger.info(
                "_search_multi_engine: %s returned %d new sources (total=%d)",
                engine_name, added, len(all_sources),
            )

    # Cache check — evitar búsquedas repetidas en < 5 min
    cached = _get_cached_search(query)
    if cached:
        return cached[0][:max_results], cached[1]

    # Motor 1: Tavily (principal cuando hay clave — más fiable para IA)
    tavily_results = _search_tavily(query, max_results=max_results)
    _add_sources(tavily_results, "tavily")
    tavily_active = len(tavily_results) >= min_sources

    # Motor 2: DuckDuckGo (solo si Tavily no llenó el mínimo)
    if len(all_sources) < min_sources:
        ddg_results = _search_duckduckgo(query, max_results=max_results)
        _add_sources(ddg_results, "duckduckgo")

    # Motor 3: Brave (si hay clave y aun falta)
    if len(all_sources) < min_sources:
        brave_results = _search_brave(query, max_results=max_results)
        _add_sources(brave_results, "brave")

    # Motor 4: Google CSE (si hay claves y aun falta)
    if len(all_sources) < min_sources:
        google_results = _search_google_cse(query, max_results=max_results)
        _add_sources(google_results, "google_cse")

    # Jina Reader: solo cuando Tavily NO está activo (sus snippets ya son ricos)
    # Cuando viene de DDG los snippets son cortos y Jina sí agrega valor.
    if all_sources and not tavily_active:
        before = [s.snippet for s in all_sources]
        all_sources = _enrich_with_jina(all_sources, max_enrich=2, skip_if_rich=True)
        if any(all_sources[i].snippet != before[i] for i in range(len(before))):
            engines_used.append("jina")

    if not engines_used:
        engines_used = ["none"]

    result = all_sources[:max_results]
    _cache_search(query, result, engines_used)
    return result, engines_used


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    cleaned = re.sub(r'<[^>]+>', '', text)
    return html.unescape(cleaned)


# ---------------------------------------------------------------------------
# INTELLIGENT INTERPRETATION — LLM-powered synthesis
# ---------------------------------------------------------------------------

_INTERPRET_PROMPT_ES = """Eres un asistente experto. Analiza la siguiente información y responde la pregunta del usuario de forma clara, directa y útil.

REGLAS:
- Nunca copies texto tal cual de las fuentes
- Analiza, comprende y reformula con tus propias palabras
- Extrae solo lo importante y relevante
- Responde en 3-6 oraciones máximo
- Si hay datos numéricos o hechos clave, inclúyelos
- No menciones fuentes, URLs, ni que buscaste en internet
- Responde en español
- Sé preciso y útil, no genérico

PREGUNTA DEL USUARIO: {query}

INFORMACIÓN RECOPILADA:
{context}

RESPUESTA INTELIGENTE:"""

_INTERPRET_PROMPT_EN = """You are an expert assistant. Analyze the following information and answer the user's question clearly, directly and usefully.

RULES:
- Never copy text verbatim from sources
- Analyze, understand and reformulate in your own words
- Extract only what is important and relevant
- Answer in 3-6 sentences maximum
- Include key numbers or facts if relevant
- Do not mention sources, URLs, or that you searched the internet
- Respond in English
- Be precise and useful, not generic

USER QUESTION: {query}

GATHERED INFORMATION:
{context}

INTELLIGENT RESPONSE:"""

# Prompts para síntesis de MEMORIA PROPIA (owner/channel del usuario actual,
# nunca de otro) -- fronteras distintas de los prompts de búsqueda online de
# arriba: aquí los fragmentos son mensajes/notas previos del propio usuario,
# no resultados externos, así que las reglas de "no menciones fuentes/
# internet" no aplican y en su lugar se pide explícitamente que NUNCA se
# listen los fragmentos como viñetas sueltas.
_INTERPRET_MEMORY_PROMPT_ES = """Eres Vectrax. A continuación tienes fragmentos reales de tu propia memoria (mensajes, notas y hechos ya confirmados de ESTE usuario) relacionados con su pregunta actual.

REGLAS:
- Responde la pregunta de forma directa, coherente y natural, como si genuinamente recordaras -- NUNCA como una lista de fragmentos sueltos ni con viñetas.
- Cada afirmación que hagas debe estar respaldada LITERALMENTE por al menos un fragmento dado. Si no puedes señalar el fragmento exacto que sustenta una frase, no la incluyas.
- PROHIBIDO inferir o generalizar rasgos de personalidad, carácter, valores o gustos que no estén escritos explícitamente en un fragmento (ejemplo prohibido: deducir "valoras la precisión" de un fragmento que solo describe configuración de voz/TTS de Vectrax). Nunca conviertas datos sobre el propio Vectrax (su voz, su configuración, su identidad) en afirmaciones sobre el usuario.
- Si dos fragmentos se contradicen sobre el mismo hecho (p.ej. dos lugares de residencia distintos), NO seleccione arbitrariamente uno como verdadero: indica la incertidumbre explícitamente o simplemente omite ese dato.
- Si los fragmentos no contienen una respuesta clara a la pregunta, dilo con naturalidad en vez de forzar una.
- No cites los fragmentos textualmente uno por uno ni menciones "fragmentos"/"memoria" de forma mecánica.
- Responde en 1-4 oraciones, en español.

PREGUNTA DEL USUARIO: {query}

FRAGMENTOS DE MEMORIA PROPIA:
{context}

RESPUESTA:"""

_INTERPRET_MEMORY_PROMPT_EN = """You are Vectrax. Below are real fragments from your own memory (this user's previous messages, notes, and already-confirmed facts) related to their current question.

RULES:
- Answer the question directly, coherently and naturally, as if you genuinely remembered -- NEVER as a list of loose fragments or bullet points.
- Every claim you make must be LITERALLY backed by at least one given fragment. If you cannot point to the exact fragment supporting a statement, do not include it.
- FORBIDDEN to infer or generalize personality traits, character, values, or tastes that are not explicitly written in a fragment (forbidden example: inferring "you value precision" from a fragment that only describes Vectrax's own voice/TTS configuration). Never turn facts about Vectrax itself (its voice, its configuration, its identity) into claims about the user.
- If two fragments contradict each other on the same fact (e.g. two different places of residence), do NOT arbitrarily pick one as true: state the uncertainty explicitly or simply omit that data point.
- If the fragments don't contain a clear answer, say so naturally instead of forcing one.
- Do not quote the fragments verbatim one by one, and do not mechanically say "fragments"/"memory".
- Answer in 1-4 sentences, in English.

USER QUESTION: {query}

OWN MEMORY FRAGMENTS:
{context}

RESPONSE:"""


def _record_provider_xp(provider: str, query: str, outcome: str, quality: float) -> None:
    """Best-effort: registra una experiencia de IA externa como estrella en el
    universo gravitacional. Nunca lanza — no debe afectar la resolución."""
    if not provider:
        return
    try:
        from core.learn.provider_stars import (
            record_provider_experience,
            infer_task_type,
        )
        record_provider_experience(
            provider=provider,
            task_type=infer_task_type(query),
            outcome=outcome,
            quality=quality,
        )
    except Exception:
        pass


def _interpret_with_llm(
    query: str,
    snippets: List[str],
    lang: str = "es",
    execution_context=None,
    mode: str = "online",
) -> str:
    """
    Intelligent interpretation: pass retrieved fragments through the LLM to
    produce an analyzed, synthesized response instead of raw fragment
    assembly.

    Falls back to empty string if LLM is unavailable.

    `mode`: selects the prompt framing -- ``"online"`` (default, unchanged
    behaviour) for web search snippets, or ``"memory"`` for the user's own
    previously retrieved stars (see ``resolve_local()``). Both share the
    exact same gate/bridge/fallback machinery below; only the prompt text
    differs, since a web snippet and a past user message need different
    instructions (memory fragments are the user's OWN words, not an
    external source to attribute/avoid-copying-from-a-URL).

    `execution_context` (parámetro, frontera del caller): esta
    interpretación es una frontera DISTINTA ("llm") anidada dentro de
    `resolve_online()`/`resolve_local()`. NO hereda el veredicto del
    boundary del caller — se deriva un `ExecutionContext` independiente
    (`.derive("resolve_llm")`) que reutiliza solo los campos de transporte,
    y se autoriza UNA vez aquí, cubriendo ambos intentos (bridge + OpenAI
    directo) de este mismo método.
    """
    context = "\n".join(f"- {s}" for s in snippets if s.strip())
    if not context:
        return ""

    if mode == "memory":
        prompt_template = _INTERPRET_MEMORY_PROMPT_ES if lang == "es" else _INTERPRET_MEMORY_PROMPT_EN
    else:
        prompt_template = _INTERPRET_PROMPT_ES if lang == "es" else _INTERPRET_PROMPT_EN
    prompt = prompt_template.format(query=query, context=context)

    # === PRE-EXECUTION CONSTITUTIONAL GATE (frontera "llm", independiente) ===
    llm_ctx = execution_context.derive("resolve_llm") if execution_context is not None else None
    from core.operator import pre_execution_gate
    llm_gate_decision = pre_execution_gate.authorize("llm", llm_ctx)
    if not llm_gate_decision.should_execute:
        return ""

    # Try Intelligence Bridge (multi-model)
    try:
        from vectrax.intelligence_bridge import is_ready, route_single
        if is_ready():
            result = route_single(prompt, execution_context=llm_ctx)
            if result.get("success") and result.get("content"):
                interpreted = result["content"].strip()
                logger.info(
                    "Intelligent interpretation via %s | len=%d",
                    result.get("provider", "?"), len(interpreted),
                )
                # La experiencia de proveedor (provider_stars) se registra una
                # sola vez a nivel del IntelligenceRouter para esta ruta
                # (route_single → router.route). No duplicar aquí.
                return interpreted
    except Exception as exc:
        logger.debug("Intelligence Bridge unavailable for interpretation: %s", exc)

    # Try direct OpenAI fallback (with API gate check)
    try:
        from core.api_gate import check_gate, record_429, record_success
        if not check_gate("openai"):
            logger.debug("Interpretation: OpenAI gate closed, skipping")
            return ""
    except Exception:
        pass
    try:
        import os
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if api_key:
            import requests as _req
            from vectrax.core_identity import VECTRAX_SYSTEM_PROMPT
            resp = _req.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": VECTRAX_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 400,
                    "temperature": 0.5,
                },
                timeout=10,
            )
            if resp.status_code == 429:
                try:
                    record_429("openai")
                except Exception:
                    pass
                logger.warning("Interpretation: OpenAI 429")
                _record_provider_xp("openai", query, "rate_limited", 0.0)
                return ""
            try:
                record_success("openai")
            except Exception:
                pass
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            logger.info("Intelligent interpretation via OpenAI direct | len=%d", len(content))
            _record_provider_xp("openai", query, "success", 0.85)
            return content.strip()
    except Exception as exc:
        logger.debug("OpenAI direct interpretation failed: %s", exc)
        _record_provider_xp("openai", query, "fail", 0.05)

    return ""


def resolve_online(
    text: str,
    channel: str,
    owner: str,
    execution_context=None,
) -> Resolution:
    """
    Build a search query from the user's question, search the web
    using multi-engine fallback, and compose an intelligent answer.

    Pipeline:
      1. Multi-engine search (DDG + Brave + Google CSE)
      2. Intelligent interpretation via LLM (analyzes, synthesizes,
         never returns raw results)
      3. Fallback to structured synthesis if LLM unavailable

    Mode: comprensión > copia — always analyze, never copy.

    `execution_context`: frontera constitucional PRE-ejecución ("online").
    UNA sola autorización cubre el fallback interno completo de
    `_search_multi_engine()` (Tavily→DDG→Brave→Google CSE). La
    interpretación LLM anidada (`_interpret_with_llm`) es una frontera
    DISTINTA ("llm") con su PROPIA autorización independiente — ver
    `ExecutionContext.derive()`.
    """
    # Build query: use the question directly
    query = text.strip().rstrip("?").strip()
    if len(query) > 200:
        query = query[:200]

    # === PRE-EXECUTION CONSTITUTIONAL GATE (frontera "online") ===
    from core.operator import pre_execution_gate
    online_gate_decision = pre_execution_gate.authorize("online", execution_context)
    if not online_gate_decision.should_execute:
        lang0 = _detect_lang(text)
        labels0 = _get_labels(lang0)
        return Resolution(
            mode="online",
            answer=f"pre_execution_gate:{online_gate_decision.execution}",
            sovereign_answer=labels0["no_info"],
            sources=[],
            search_query=query,
            engines_used=[],
        )

    sources, engines_used = _search_multi_engine(query, max_results=5)

    lang = _detect_lang(text)
    labels = _get_labels(lang)

    if not sources:
        return Resolution(
            mode="online",
            answer="No online results found.",
            sovereign_answer=labels["no_info"],
            sources=[],
            search_query=query,
            engines_used=engines_used,
        )

    # Debug answer (numbered sources — internal only)
    debug_parts = []
    for i, src in enumerate(sources[:5], 1):
        debug_parts.append(f"{i}. **{src.title}**\n   {src.snippet}")
    answer = "DEBUG sources:\n\n" + "\n\n".join(debug_parts)

    snippets = [src.snippet for src in sources[:5]]

    # ══ INTELLIGENT INTERPRETATION ══
    # Priority: LLM analysis > structured synthesis
    sovereign = _interpret_with_llm(text, snippets, lang=lang, execution_context=execution_context)

    # Fallback: structured synthesis (if LLM unavailable)
    if not sovereign:
        sovereign = _synthesize_online(snippets, lang=lang, query=text, sources=sources)

    return Resolution(
        mode="online",
        answer=answer,
        sovereign_answer=sovereign,
        sources=sources,
        search_query=query,
        engines_used=engines_used,
    )


# ---------------------------------------------------------------------------
# 4. ORCHESTRATOR
# ---------------------------------------------------------------------------

# Minimum top-match relevance score for local results to be considered useful.
# Below this, the local answer is treated as low-confidence and triggers
# an automatic fallback to online research.
_LOCAL_RELEVANCE_THRESHOLD = 0.55


def resolve(
    text: str,
    channel: str,
    owner: str,
    execution_context=None,
) -> Resolution:
    """
    Main entry point: classify → route → resolve.

    - memory → returns empty Resolution (caller handles ingest)
    - local  → searches memory stars; falls back to online if
               no matches OR top match relevance < threshold
    - online → searches the web

    `execution_context`: propagado tal cual a `resolve_online()` (frontera
    "online") en ambos caminos que pueden llegar a la web.
    """
    mode = classify(text)
    logger.info("Resolver: text=%r → mode=%s (channel=%s, owner=%s)",
                text[:60], mode, channel, owner)

    if mode == "memory":
        return Resolution(mode="memory")

    if mode == "local":
        result = resolve_local(text, channel, owner)
        # Self-reference / greetings, and ANY personal-memory query (not just
        # relationship queries -- "¿Quién es mi novia?") must NEVER escalate
        # to a web search, even when local memory is empty (rule: no internet
        # for self/identity, and no internet to "discover" who a related
        # person is, what was decided, or what was said previously -- that is
        # a privacy leak, not a valid answer). resolve_local() already
        # returns an honest "no tengo información" label when it finds
        # nothing; that is the correct terminal answer here.
        # PARTE 5 (2026-09-20): broadened from `is_personal_relationship_query`
        # (narrow: only "who is my X") to `is_personal_memory_query` (superset:
        # also covers recall/decision/history queries -- "¿qué hablamos ayer?",
        # "¿qué decidimos?") so `_LOCAL_RELEVANCE_THRESHOLD` never governs the
        # jump to internet when the intent is personal memory, per spec.
        if not _is_conversational(text) and not is_personal_memory_query(text) and (
            result.context_stars == 0 or result.top_score < _LOCAL_RELEVANCE_THRESHOLD
        ):
            logger.info(
                "Resolver: local insufficient (stars=%d, top_score=%.2f), "
                "falling back to online",
                result.context_stars, result.top_score,
            )
            result = resolve_online(text, channel, owner, execution_context=execution_context)
            result.fallback_from = "local"
        return result

    # mode == "online"
    return resolve_online(text, channel, owner, execution_context=execution_context)
