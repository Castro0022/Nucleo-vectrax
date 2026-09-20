"""
core/nucleus/nucleus_authority.py — Autoridad única de decisión (reunificación)
=================================================================================
Precisión de genealogía (auditoría 2026-09-19, ver plan de reunificación):
`core.nucleus.total_convergence.TotalConvergenceEngine` NO es el núcleo
original de Vectrax (`vectrax_cerebro_unico.py`, `~/Vectrax_BACKUP/`) — es
una reimplementación posterior sin lineage de código con él. `NucleusAuthority`
es, por lo tanto, una FACHADA que restaura la conducta fundacional del
núcleo original: primero memoria propia (stars + memoria histórica
ARGOS/Sinapsis migrada, ver `core/argos_migration.py`); solo si la evidencia
es insuficiente, escalar a una herramienta externa (búsqueda web, lugares,
mercado). `TotalConvergenceEngine`, `SmartRouter` y las demás capacidades
(`capability_context`, ejecutores) son MOTORES SUBORDINADOS que esta fachada
orquesta — nunca sustitutos de esta autoridad ni de esta conducta.

Corrección 2026-09-19 (segunda pasada, auditoría de calidad de evidencia):
la consulta de memoria (`_query_memory()`) se ejecuta AHORA de forma
INCONDICIONAL para TODA entrada, antes de cualquier override o decisión —
sin excepciones de ruta. El resultado de esa consulta separa EXPLÍCITAMENTE
cuatro ejes independientes (nunca colapsados en un solo número):

  - coherencia:  qué tan bien matchea la memoria con la pregunta (similitud
                 de embeddings — lo que antes era el único `top_score`).
  - relevancia:  si la memoria recuperada responde de verdad el CONTENIDO
                 específico de la pregunta (solapamiento de palabras clave
                 pregunta↔memoria) — un recuerdo puede ser coherente
                 (temáticamente parecido) sin ser relevante (no contesta lo
                 preguntado). Este es el eje que corrige el defecto detectado
                 en FASE 3 (preguntas externas resueltas con memoria genérica
                 no relacionada).
  - vigencia:    si la pregunta pertenece a una categoría de dato cambiante
                 (cargos actuales, precios, disponibilidad, clima, noticias)
                 y, si es así, si la memoria tiene evidencia verificada como
                 reciente. Regla GENERAL por categoría — nunca una excepción
                 para una entidad/frase concreta.
  - procedencia:  de dónde viene cada recuerdo (source_origin: interacción
                 directa, migración ARGOS/Sinapsis, etc.) y una confianza
                 derivada de la masa gravitacional real de esos recuerdos.

Un recuerdo coherente NUNCA se acepta como evidencia suficiente si falla en
relevancia o en vigencia — ver `MemoryEvidence.sufficient`.

Contrato único, llamado igual por ambos canales (localhost y Telegram):

    from core.nucleus.nucleus_authority import NucleusAuthority
    response = NucleusAuthority().resolve(text, channel=..., owner=..., source=...)

Creado: 2026-09-19 — reunificación de Vectrax (rama nucleo/reunificacion).
"""
from __future__ import annotations

import logging
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.nucleus.authority")


# ---------------------------------------------------------------------------
# NucleusResponse — contrato de salida único, con traza completa de autoridad
# ---------------------------------------------------------------------------

@dataclass
class NucleusResponse:
    """Resultado de `NucleusAuthority.resolve()`. Traza completa exigida por
    FASE 3 del plan de reunificación."""

    final_action: str = "CLARIFICATION"   # MEMORY|LOCAL|ONLINE|PLACES|MARKET|IDENTITY|CLARIFICATION
    authority: str = "nucleus"            # SIEMPRE "nucleus" — nunca smart_router/external_gateway
    candidate_source: str = ""            # "" (evidencia/capacidad propia) | "smart_router"
    confidence: float = 0.0
    reason: str = ""
    memory_consulted: bool = False        # SIEMPRE True: la consulta de memoria es incondicional
    memory_source: str = ""               # qué fuente de memoria se revisó
    memory_evidence: Dict[str, Any] = field(default_factory=dict)  # los 4 ejes separados
    capability_selected: str = ""         # qué entrada del catálogo respaldó la acción
    tool_executed: str = ""               # ejecutor real invocado, o "" si ninguno
    evidence: Dict[str, Any] = field(default_factory=dict)
    evidence_authorized: bool = False
    answer: str = ""
    channel: str = ""
    owner: str = ""                       # identidad canónica (post alias)
    owner_raw: str = ""                   # identidad tal como llegó (pre alias)
    source: str = ""                      # "api" | "telegram" | lo que pase el adaptador

    def to_dict(self) -> Dict[str, Any]:
        return {
            "final_action": self.final_action,
            "authority": self.authority,
            "candidate_source": self.candidate_source,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            "memory_consulted": self.memory_consulted,
            "memory_source": self.memory_source,
            "memory_evidence": self.memory_evidence,
            "capability_selected": self.capability_selected,
            "tool_executed": self.tool_executed,
            "evidence": self.evidence,
            "evidence_authorized": self.evidence_authorized,
            "answer": self.answer,
            "channel": self.channel,
            "owner": self.owner,
            "owner_raw": self.owner_raw,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# Consulta de memoria INCONDICIONAL (paso 1, obligatorio para toda entrada)
# ---------------------------------------------------------------------------

# Umbral mínimo de solapamiento de palabras clave pregunta↔memoria para
# considerar un recuerdo "relevante" (no solo "coherente"). Deliberadamente
# bajo (una sola palabra clave compartida ya cuenta) — el objetivo es filtrar
# el caso de solapamiento CERO (memoria completamente ajena al contenido de
# la pregunta), no exigir una coincidencia exhaustiva.
_RELEVANCE_MIN = 0.15

_STOPWORDS = {
    "que", "qué", "quien", "quién", "como", "cómo", "cuando", "cuándo",
    "donde", "dónde", "cual", "cuál", "cuales", "cuáles", "cuanto", "cuánto",
    "el", "la", "los", "las", "del", "al", "un", "una", "unos", "unas",
    "es", "son", "fue", "era", "eran", "ser", "estar", "está", "actualmente",
    "actual", "para", "ti", "tu", "tú", "tus", "mi", "mí", "mis", "sobre",
    "con", "por", "y", "o", "en", "a", "de", "se", "lo", "le", "les",
    "the", "is", "are", "was", "were", "of", "for", "what", "who", "how",
    "when", "where", "which", "currently", "current", "to", "about",
    "you", "your", "me", "my", "do", "does", "did", "have", "has", "had",
}


def _content_words(text: str) -> set:
    """Palabras significativas de un texto (sin stopwords) — usado para
    medir relevancia por solapamiento léxico."""
    words = re.findall(r"[a-záéíóúñü]+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


# --- Detección GENERAL de categorías de dato cambiante (vigencia) ----------
# Regla por CATEGORÍA, nunca por entidad/frase concreta (p.ej. nunca
# "Australia" explícito) — cubre cualquier país/cargo/precio/clima/noticia.

_OFFICE_HOLDER_RE = re.compile(
    r"\b(?:presidente|primer\s+ministro|primera\s+ministra|rey|reina|papa|"
    r"canciller|gobernador|alcalde|ceo|director\s+general|secretario\s+general|"
    r"president|prime\s+minister|king|queen|pope|chancellor|governor|mayor|"
    r"secretary\s+general)\b",
    re.IGNORECASE,
)
_PAST_TENSE_RE = re.compile(
    r"\b(?:fue|fueron|era|eran|hab[ií]a\s+sido|was|were|had\s+been)\b",
    re.IGNORECASE,
)
_ALWAYS_VOLATILE_RE = re.compile(
    r"\b(?:precio|precios|cotizaci[oó]n|cu[aá]nto\s+cuesta|cu[aá]nto\s+vale|"
    r"price|cost|cotiza|"
    r"disponib(?:le|ilidad|les)|en\s+stock|stock|availability|"
    r"clima|tiempo\s+(?:hoy|ma[nñ]ana)|pron[oó]stico|weather|forecast|"
    r"noticias?|[uú]ltima\s+hora|breaking\s+news|news\b|"
    r"resultado\s+(?:del|de\s+la)\s+partido|marcador\s+en\s+vivo|live\s+score)\b",
    re.IGNORECASE,
)


def _requires_freshness(text: str) -> bool:
    """True si la pregunta pertenece a una categoría de dato cambiante
    (cargos actuales, precios, disponibilidad, clima, noticias) — detección
    GENERAL por categoría léxica, nunca una regla exclusiva para una
    entidad/frase concreta."""
    if _ALWAYS_VOLATILE_RE.search(text or ""):
        return True
    if _OFFICE_HOLDER_RE.search(text or "") and not _PAST_TENSE_RE.search(text or ""):
        return True
    return False


# --- Ubicación recordada (para PLACES, paso 6) ------------------------------

_LOCATION_STATEMENT_RE = re.compile(
    r"(?:vivo\s+en|mi\s+ciudad\s+es|estoy\s+en|resido\s+en|mi\s+ubicaci[oó]n\s+es|"
    r"i\s+live\s+in|my\s+city\s+is|my\s+location\s+is|based\s+in)\s+"
    r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s]{1,40}?)(?:[.,;\n]|$)",
    re.IGNORECASE,
)


def _extract_remembered_location(channel: str, owner: str) -> Optional[Dict[str, Any]]:
    """Busca ubicación del usuario ANTES de pedirla. Dos fuentes, en orden:
      1. `vectrax.user_memory.get_user_location()` — tabla estructurada
         (lat/lng), autoritativa cuando existe.
      2. Memoria propia (stars del owner/channel) — declaraciones de
         ubicación en texto libre ("vivo en...", "mi ciudad es...").
    Devuelve `None` solo si NINGUNA de las dos fuentes tiene algo."""
    try:
        from vectrax.user_memory import get_user_location
        loc = get_user_location(owner)
        if loc:
            return {"lat": loc["lat"], "lng": loc["lng"], "source": "user_locations", "text": ""}
    except Exception as exc:
        logger.debug("get_user_location failed: %s", exc)

    try:
        from vectrax.db import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        try:
            rows = conn.execute(
                "SELECT content FROM stars WHERE channel=? AND owner=? ORDER BY timestamp DESC",
                (channel, owner),
            ).fetchall()
        finally:
            conn.close()
        for (content,) in rows:
            m = _LOCATION_STATEMENT_RE.search(content or "")
            if m:
                place = m.group(1).strip()
                if place:
                    return {"lat": None, "lng": None, "source": "stars_memory", "text": place}
    except Exception as exc:
        logger.debug("location memory search failed: %s", exc)

    return None


@dataclass
class MemoryEvidence:
    """Resultado de la consulta de memoria incondicional — los 4 ejes
    separados exigidos: coherencia, relevancia, vigencia, procedencia."""

    consulted: bool = True                 # SIEMPRE True: la consulta se ejecutó
    context_stars: int = 0
    coherence: float = 0.0                 # similitud de embeddings del mejor match
    relevance: float = 0.0                 # solapamiento léxico pregunta↔memoria (0..1)
    requires_freshness: bool = False       # ¿la pregunta exige dato actualizado?
    temporal_validity: str = "not_applicable"  # not_applicable | unknown | fresh | stale
    provenance: List[str] = field(default_factory=list)   # source_origin distintos encontrados
    confidence: float = 0.0                # confianza combinada (masa real de las stars)
    sovereign_answer: str = ""             # respuesta ya sintetizada por resolve_local()
    remembered_location: Optional[Dict[str, Any]] = None
    sufficient: bool = False               # veredicto final combinando los 4 ejes
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "consulted": self.consulted,
            "context_stars": self.context_stars,
            "coherence": round(self.coherence, 4),
            "relevance": round(self.relevance, 4),
            "requires_freshness": self.requires_freshness,
            "temporal_validity": self.temporal_validity,
            "provenance": self.provenance,
            "confidence": round(self.confidence, 4),
            "remembered_location": self.remembered_location,
            "sufficient": self.sufficient,
            "reason": self.reason,
        }


def _query_memory(text: str, channel: str, owner: str) -> MemoryEvidence:
    """Consulta de memoria INCONDICIONAL — se ejecuta para TODA entrada, sin
    excepciones de ruta (requisito explícito de la reunificación, segunda
    pasada 2026-09-19). El resultado puede ser suficiente, insuficiente,
    irrelevante o desactualizado, pero la consulta SIEMPRE ocurre."""
    ev = MemoryEvidence(consulted=True)
    ev.requires_freshness = _requires_freshness(text)

    try:
        from vectrax.resolver import resolve_local
        result = resolve_local(text, channel, owner)
        ev.context_stars = result.context_stars
        ev.coherence = result.top_score
        ev.sovereign_answer = result.sovereign_answer or ""
    except Exception as exc:
        logger.debug("_query_memory: resolve_local failed: %s", exc)

    # -- Relevancia: solapamiento léxico pregunta↔memoria recuperada --------
    q_words = _content_words(text)
    if q_words and ev.sovereign_answer:
        matched_lower = ev.sovereign_answer.lower()
        hits = sum(1 for w in q_words if w in matched_lower)
        ev.relevance = round(hits / len(q_words), 4)
    elif not q_words:
        # Sin palabras de contenido específicas que exigir (p.ej. "¿quién soy?")
        # — no penalizamos por falta de solapamiento léxico.
        ev.relevance = 1.0
    else:
        ev.relevance = 0.0

    # -- Procedencia y confianza: fuentes reales + masa gravitacional -------
    try:
        from vectrax.db import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        try:
            origin_rows = conn.execute(
                "SELECT DISTINCT source_origin FROM stars "
                "WHERE channel=? AND owner=? AND source_origin != ''",
                (channel, owner),
            ).fetchall()
            mass_rows = conn.execute(
                "SELECT mass FROM stars WHERE channel=? AND owner=?",
                (channel, owner),
            ).fetchall()
        finally:
            conn.close()
        ev.provenance = sorted({r[0] for r in origin_rows if r[0]})
        if not ev.provenance:
            ev.provenance = ["interaccion_directa"] if ev.context_stars else []
        if mass_rows:
            avg_mass = sum(r[0] for r in mass_rows) / len(mass_rows)
            ev.confidence = round(min(1.0, (ev.coherence * 0.5) + (avg_mass * 0.5)), 4)
    except Exception as exc:
        logger.debug("_query_memory: provenance query failed: %s", exc)

    # -- Vigencia temporal ----------------------------------------------------
    # Ninguna star lleva hoy metadata de "verificado como vigente en fecha X"
    # — así que una pregunta que EXIGE actualidad nunca puede resolverse desde
    # memoria como "fresh"; queda "unknown" y eso basta para NO ser suficiente.
    ev.temporal_validity = "unknown" if ev.requires_freshness else "not_applicable"

    # -- Ubicación recordada (para PLACES) ------------------------------------
    ev.remembered_location = _extract_remembered_location(channel, owner)

    # -- Veredicto final: combina los 4 ejes, nunca uno solo -----------------
    if ev.requires_freshness and ev.temporal_validity != "fresh":
        ev.sufficient = False
        ev.reason = (
            f"vigencia: la pregunta exige dato actualizado y la memoria no tiene "
            f"evidencia verificada como reciente (temporal_validity={ev.temporal_validity})"
        )
    elif ev.context_stars == 0:
        ev.sufficient = False
        ev.reason = "sin coincidencias en memoria (context_stars=0)"
    elif q_words and ev.relevance < _RELEVANCE_MIN:
        ev.sufficient = False
        ev.reason = (
            f"coherente (coherence={ev.coherence:.2f}) pero irrelevante "
            f"para la pregunta (relevance={ev.relevance:.2f} < {_RELEVANCE_MIN})"
        )
    else:
        ev.sufficient = True
        ev.reason = (
            f"memoria suficiente: coherence={ev.coherence:.2f}, "
            f"relevance={ev.relevance:.2f}, provenance={ev.provenance}"
        )

    return ev


# ---------------------------------------------------------------------------
# Resumen de patrones propios ("¿qué has aprendido?" — paso 5)
# ---------------------------------------------------------------------------

# Plantillas variadas para narrar cada patrón en lenguaje natural, SIN
# cifras/IDs/conteos (esos quedan solo en el dict de evidencia interna, para
# trazabilidad/tests -- nunca en el texto que lee el usuario, salvo que lo
# pida explícitamente). Cada patrón citado es una palabra/tema que REALMENTE
# se repite (frecuencia > 1) en el contenido real de las stars -- nunca una
# categoría inventada.
_PATTERN_TEMPLATES_ES = [
    "Reconozco un patrón recurrente alrededor de \"{w}\".",
    "Vuelvo una y otra vez sobre el tema de \"{w}\" en lo que he registrado.",
    "\"{w}\" aparece de forma consistente en varios de mis recuerdos propios.",
    "Hay una tendencia clara hacia \"{w}\" en lo que he ido guardando.",
    "\"{w}\" es algo a lo que he vuelto varias veces en mi memoria.",
]
_PATTERN_TEMPLATES_EN = [
    "I recognize a recurring pattern around \"{w}\".",
    "I keep coming back to \"{w}\" in what I've recorded.",
    "\"{w}\" shows up consistently across several of my own memories.",
    "There's a clear tendency toward \"{w}\" in what I've been storing.",
    "\"{w}\" is something I've returned to more than once in my memory.",
]

_MIN_PATTERNS = 3
_MAX_PATTERNS = 5


def _summarize_learned_patterns(channel: str, owner: str, lang: str) -> Tuple[str, Dict[str, Any]]:
    """Resume entre 3 y 5 patrones GENERALES propios, en lenguaje natural,
    respaldados por memoria real -- SIN cifras/IDs/conteos en el texto (esos
    quedan en el dict de evidencia, para trazabilidad interna/tests). Si no
    hay evidencia suficiente para al menos un patrón recurrente, lo dice
    explicitamente en vez de fabricar conclusiones."""
    try:
        from vectrax.db import get_all_stars
        stars = get_all_stars(channel=channel, owner=owner)
    except Exception as exc:
        logger.debug("_summarize_learned_patterns: get_all_stars failed: %s", exc)
        stars = []

    evidence: Dict[str, Any] = {
        "stars_total": len(stars), "core_count": 0, "mid_count": 0,
        "outer_count": 0, "top_words": [], "patterns_returned": 0,
    }

    if not stars:
        msg = (
            "Todavía no tengo evidencia suficiente en mi memoria para describir "
            "patrones propios."
            if lang != "en" else
            "I don't have enough evidence in my own memory yet to describe patterns."
        )
        return msg, evidence

    word_counter: Counter = Counter()
    layer_counter: Counter = Counter()
    for s in stars:
        layer_counter[s.layer] += 1
        word_counter.update(_content_words(s.content))

    evidence["core_count"] = layer_counter.get("core", 0)
    evidence["mid_count"] = layer_counter.get("mid", 0)
    evidence["outer_count"] = layer_counter.get("outer", 0)

    # Solo cuenta como "patrón" una palabra/tema que se repite REALMENTE
    # (frecuencia > 1) -- un evento único no es un patrón recurrente.
    recurring = [(w, c) for w, c in word_counter.most_common(20) if c > 1][:_MAX_PATTERNS]
    evidence["top_words"] = [w for w, _ in recurring]
    evidence["patterns_returned"] = len(recurring)

    if not recurring:
        msg = (
            "Todavía no veo un patrón que se repita con claridad en mi memoria "
            "propia -- lo que tengo registrado hasta ahora son eventos puntuales, "
            "no recurrencias."
            if lang != "en" else
            "I don't yet see a clearly recurring pattern in my own memory -- what "
            "I have recorded so far are one-off events, not recurrences."
        )
        return msg, evidence

    templates = _PATTERN_TEMPLATES_EN if lang == "en" else _PATTERN_TEMPLATES_ES
    sentences = [
        templates[i % len(templates)].format(w=word)
        for i, (word, _count) in enumerate(recurring)
    ]

    intro = (
        "Looking at my own memory, these are the patterns I keep recognizing: "
        if lang == "en" else
        "Mirando mi propia memoria, estos son los patrones que reconozco una y otra vez: "
    )
    msg = intro + " ".join(sentences)

    if len(recurring) < _MIN_PATTERNS:
        msg += (
            " Todavía tengo poca evidencia acumulada, así que esto podría "
            "ampliarse con más interacciones."
            if lang != "en" else
            " I still have limited accumulated evidence, so this could grow "
            "with more interactions."
        )

    return msg, evidence


# ---------------------------------------------------------------------------
# Overrides de autoconocimiento explícito
# ---------------------------------------------------------------------------
# Preguntas meta sobre el propio Vectrax que los clasificadores regex
# genéricos (vectrax.resolver, core.smart_router) NO reconocen hoy como
# self-reference (auditoría 2026-09-19) y que, sin este override, caerían
# en ONLINE (búsqueda web genérica) — exactamente el defecto que la
# reunificación corrige. Contenido citado de código REAL, nunca inventado.
# TODOS estos overrides se evalúan DESPUÉS de la consulta de memoria
# incondicional (`_query_memory()`) — nunca la reemplazan ni la saltan.

_UNKNOWN_FALLBACK_RE = re.compile(
    r"\bqu[eé]\s+haces\s+cuando\s+no\s+sabes\b"
    r"|\bwhat\s+do\s+you\s+do\s+when\s+you\s+don'?t\s+know\b",
    re.IGNORECASE,
)

_IDENTITY_SELF_RE = re.compile(
    r"\b(qui[eé]n\s+eres|qui[eé]n\s+te\s+(?:cre[oó]|hizo|dise[nñ][oó]|construy[oó])"
    r"|tu\s+creador|qu[eé]\s+eres)\b"
    r"|\bwho\s+are\s+you\b|\bwho\s+(?:created|made|built)\s+you\b",
    re.IGNORECASE,
)

_GRAVITY_SELF_RE = re.compile(
    r"\bgravedad\b.*\b(para\s+ti|tuya|tu\s+propia|significa\s+para\s+ti)\b"
    r"|\bwhat\s+does\s+gravity\s+mean\s+to\s+you\b",
    re.IGNORECASE,
)

_LEARNED_RE = re.compile(
    r"\bqu[eé]\s+has\s+aprendido\b|\bwhat\s+have\s+you\s+learned\b",
    re.IGNORECASE,
)
_LEARNED_DOMAIN_HINT_RE = re.compile(
    r"\b(sobre|de|acerca\s+de|about)\s+\w+",
    re.IGNORECASE,
)

_GRAVITY_SELF_KNOWLEDGE_ES = (
    "Gravedad, en mi propia estructura, es la métrica que decide qué tan "
    "cerca del núcleo vive cada star: gravity = repetition_count × "
    "coherence_score × success_rate. La masa gravitacional combina tres "
    "señales — conexiones entrantes (40%), coherencia semántica (35%) y "
    "frecuencia de activación (25%) — y la distancia al núcleo es "
    "simplemente 1 − masa. Con esa masa asigno la capa: core si gravity ≥ "
    "0.6, mid si ≥ 0.3, y outer por debajo de eso. No es una metáfora "
    "poética — es el mecanismo real que uso para decidir qué conocimiento "
    "está cerca de mi centro y cuál está en la periferia."
)
_GRAVITY_SELF_KNOWLEDGE_EN = (
    "Gravity, in my own structure, is the metric that decides how close to "
    "the nucleus each star lives: gravity = repetition_count × "
    "coherence_score × success_rate. Gravitational mass combines three "
    "signals — incoming connections (40%), semantic coherence (35%) and "
    "activation frequency (25%) — and distance to the core is simply "
    "1 − mass. With that mass I assign the layer: core if gravity ≥ 0.6, "
    "mid if ≥ 0.3, and outer below that. It is not a poetic metaphor — it "
    "is the real mechanism I use to decide which knowledge sits near my "
    "center and which sits at the periphery."
)


def _detect_lang(text: str) -> str:
    try:
        from vectrax.resolver import _detect_lang as _dl
        return _dl(text)
    except Exception:
        return "es"


def _try_self_knowledge_override(
    text: str, channel: str, owner: str, source: str, mem: MemoryEvidence,
) -> Optional[NucleusResponse]:
    """Resuelve preguntas meta sobre el propio Vectrax. Recibe `mem` (YA
    calculado por la consulta incondicional) para que TODOS los overrides
    reporten memory_consulted=True/memory_evidence real, nunca simulado.
    Devuelve `None` si el texto no matchea ningún override."""
    lang = _detect_lang(text)

    if _IDENTITY_SELF_RE.search(text):
        return NucleusResponse(
            final_action="IDENTITY",
            candidate_source="",
            confidence=0.95,
            reason="self-knowledge override: pregunta de identidad sobre el propio Vectrax",
            memory_consulted=True,
            memory_source="vectrax.db stars (incluye memoria histórica ARGOS/Sinapsis migrada)",
            memory_evidence=mem.to_dict(),
            capability_selected="",
            tool_executed="",
            evidence={},
            evidence_authorized=False,  # pendiente: _dispatch_executor decide
            answer="",
            channel=channel, owner=owner, source=source,
        )

    if _UNKNOWN_FALLBACK_RE.search(text):
        try:
            from core.self_observation.capability_context import build_capability_context
            from core.self_observation.capability_narrator import narrate

            class _Shim:
                domain = None
                task_type = None
                capability_query = True

            ctx = build_capability_context(_Shim())
            answer = narrate(ctx, lang=lang if lang in ("es", "en") else "es")
        except Exception as exc:
            logger.debug("capability self-knowledge override failed: %s", exc)
            answer = (
                "Cuando no tengo evidencia suficiente, lo digo explícitamente "
                "en vez de inventar una respuesta, y reviso qué herramientas "
                "tengo disponibles antes de intentar cualquier otra vía."
                if lang != "en" else
                "When I don't have enough evidence, I say so explicitly "
                "instead of making something up, and I check which tools "
                "are actually available before trying any other path."
            )
        return NucleusResponse(
            final_action="IDENTITY",
            candidate_source="",
            confidence=1.0,
            reason="self-knowledge override: pregunta meta sobre el propio fallback de Vectrax",
            memory_consulted=True,
            memory_source="capability_context (autoconocimiento) + consulta de memoria",
            memory_evidence=mem.to_dict(),
            capability_selected="",
            tool_executed="capability_narrator",
            evidence={"kind": "self_knowledge"},
            evidence_authorized=True,
            answer=answer,
            channel=channel, owner=owner, source=source,
        )

    if _GRAVITY_SELF_RE.search(text):
        answer = _GRAVITY_SELF_KNOWLEDGE_EN if lang == "en" else _GRAVITY_SELF_KNOWLEDGE_ES
        return NucleusResponse(
            final_action="IDENTITY",
            candidate_source="",
            confidence=1.0,
            reason="self-knowledge override: pregunta autorreferencial sobre 'gravedad'",
            memory_consulted=True,
            memory_source="vectrax.gravity (constantes y fórmulas reales del código) + consulta de memoria",
            memory_evidence=mem.to_dict(),
            capability_selected="",
            tool_executed="self_knowledge",
            evidence={"kind": "self_knowledge", "source_module": "vectrax.gravity"},
            evidence_authorized=True,
            answer=answer,
            channel=channel, owner=owner, source=source,
        )

    if _LEARNED_RE.search(text) and not _LEARNED_DOMAIN_HINT_RE.search(text):
        # Corrección 2026-09-19: YA NO se responde con una aclaración
        # automática — se consulta la memoria real y se resumen patrones
        # generales propios (ver _summarize_learned_patterns()).
        answer, learned_evidence = _summarize_learned_patterns(channel, owner, lang)
        return NucleusResponse(
            final_action="LOCAL",
            candidate_source="",
            confidence=0.9 if learned_evidence.get("stars_total") else 0.5,
            reason="'¿qué has aprendido?' sin dominio -> resumen de patrones propios desde memoria",
            memory_consulted=True,
            memory_source="vectrax.db stars (todas las del owner/channel)",
            memory_evidence=mem.to_dict(),
            capability_selected="",
            tool_executed="summarize_learned_patterns",
            evidence=learned_evidence,
            evidence_authorized=True,
            answer=answer,
            channel=channel, owner=owner, source=source,
        )

    return None


# ---------------------------------------------------------------------------
# Mapeo Strategy -> final_action (vocabulario ya existente en smart_router)
# ---------------------------------------------------------------------------

def _strategy_to_action(strategy) -> str:
    from core.smart_router import Strategy
    mapping = {
        Strategy.RESOLVE_MEMORY: "MEMORY",
        Strategy.RESOLVE_LOCAL: "LOCAL",
        Strategy.RESOLVE_ONLINE: "ONLINE",
        Strategy.RESOLVE_PLACES: "PLACES",
        Strategy.RESOLVE_IDENTITY: "IDENTITY",
        Strategy.RESOLVE_MARKET: "MARKET",
        Strategy.ANSWER_FROM_EVIDENCE: "LOCAL",
    }
    return mapping.get(strategy, "CLARIFICATION")


_CAPABILITY_BY_ACTION = {
    "ONLINE": "online_search",
    "PLACES": "places_search",
    "MARKET": "market_observer",
}


# ---------------------------------------------------------------------------
# NucleusAuthority
# ---------------------------------------------------------------------------

class NucleusAuthority:
    """Autoridad única de decisión. Ver docstring del módulo."""

    def resolve(
        self,
        text: str,
        *,
        channel: str = "user",
        owner: str = "",
        source: str = "unknown",
    ) -> NucleusResponse:
        """Punto de entrada COMPLETO: decide, despacha ejecutor y autoriza
        evidencia. Usado por el adaptador web (`services/core/routes/chat.py`).

        Ejecuta su PROPIO ciclo de convergencia internamente. NO usar desde
        un caller que YA corrió `run_convergence_cycle()` para el mismo
        mensaje (p.ej. `pipeline_worker.py`) — eso duplicaría fases de
        convergencia/ingesta. Para ese caso usar `decide_from_record()`.
        """
        text = text or ""
        override, decided, record = self._decide(
            text, channel=channel, owner=owner, source=source, record=None,
        )
        if override is not None:
            return override

        response = NucleusResponse(**decided)
        self._dispatch_executor(response, text, record)
        return response

    def resolve_from_record(
        self,
        text: str,
        *,
        channel: str = "user",
        owner: str = "",
        source: str = "telegram",
        record: Any,
    ) -> NucleusResponse:
        """Punto de entrada COMPLETO (decide + despacha ejecutor + autoriza
        evidencia), reutilizando un `ConvergenceRecord` YA CALCULADO por el
        caller — evita duplicar el ciclo de convergencia.

        Corrección de producción (2026-09-19, segunda pasada): inyectar solo
        un `NucleusDecision` en `ExternalGateway.receive_message()` NO
        garantiza consistencia — `ExternalGateway._do_receive_message()`
        tiene capas legacy PROPIAS (greeting intercept, intake_filter STORE,
        `vectrax.self_context.resolve_self_aware`, `vectrax.nucleus_resolver`,
        domain criterion gate) que producen `response_text` ANTES de llegar
        al punto donde se consulta esa candidata — confirmado en producción:
        Telegram y localhost daban respuestas DISTINTAS para la misma
        pregunta de identidad/gravedad. Este método es la fuente ÚNICA del
        TEXTO de respuesta para el adaptador de Telegram (igual que
        `resolve()` lo es para el adaptador web) — bypassa por completo la
        capa de decisión de `ExternalGateway` para el flujo conversacional.
        """
        override, decided, record = self._decide(
            text, channel=channel, owner=owner, source=source, record=record,
        )
        if override is not None:
            return override
        response = NucleusResponse(**decided)
        self._dispatch_executor(response, text, record)
        return response

    def decide_from_record(
        self,
        text: str,
        *,
        channel: str = "user",
        owner: str = "",
        source: str = "telegram",
        record: Any,
    ) -> "tuple[Any, NucleusResponse]":
        """Punto de entrada LIGERO: decide a partir de un `ConvergenceRecord`
        YA CALCULADO por el caller — nunca vuelve a correr el ciclo de
        convergencia. Usado por el adaptador de Telegram
        (`core/transport/pipeline_worker.py`)."""
        override, decided, _ = self._decide(
            text, channel=channel, owner=owner, source=source, record=record,
        )
        from core.nucleus.nucleus_decision import NucleusDecision
        from core.smart_router import Strategy

        if override is not None:
            nd = NucleusDecision(
                candidate_strategy=(
                    Strategy.ANSWER_FROM_EVIDENCE if override.evidence_authorized else None
                ),
                evidence={"sovereign_answer": override.answer, "kind": "self_knowledge_override"},
                capability={}, confidence=override.confidence,
                reason=override.reason, source="nucleus_authority",
            )
            return nd, override

        trace = NucleusResponse(**decided)
        candidate_strategy = None
        if trace.final_action != "CLARIFICATION":
            candidate_strategy = {
                "MEMORY": Strategy.RESOLVE_MEMORY, "LOCAL": Strategy.RESOLVE_LOCAL,
                "ONLINE": Strategy.RESOLVE_ONLINE, "PLACES": Strategy.RESOLVE_PLACES,
                "IDENTITY": Strategy.RESOLVE_IDENTITY, "MARKET": Strategy.RESOLVE_MARKET,
            }.get(trace.final_action)
        nd = NucleusDecision(
            candidate_strategy=candidate_strategy,
            evidence={"capability_selected": trace.capability_selected},
            capability={}, confidence=trace.confidence,
            reason=trace.reason, source="nucleus_authority",
        )
        return nd, trace

    def _decide(
        self, text: str, *, channel: str, owner: str, source: str, record: Any,
    ):
        """Lógica de decisión compartida. No ejecuta nada (salvo la consulta
        de memoria incondicional, que es de solo lectura).

        Returns (override_or_None, decided_kwargs_for_NucleusResponse, record).
        """
        text = text or ""

        # -- 1. Identidad canónica (alias owner -> mario) -------------------
        owner_raw = owner
        try:
            from vectrax.identity_aliases import resolve_owner
            canonical_owner = resolve_owner(owner)
        except Exception:
            canonical_owner = owner

        # -- 2. CONSULTA DE MEMORIA INCONDICIONAL ---------------------------
        # Se ejecuta SIEMPRE, para TODA entrada, sin excepción de ruta —
        # antes de cualquier override, antes de convergencia/SmartRouter.
        # El resultado puede ser suficiente, insuficiente, irrelevante o
        # desactualizado, pero la consulta ocurre siempre (requisito
        # explícito, reunificación 2026-09-19).
        mem = _query_memory(text, channel, canonical_owner)

        # -- 3. Overrides de autoconocimiento explícito ---------------------
        override = _try_self_knowledge_override(text, channel, canonical_owner, source, mem)
        if override is not None:
            override.owner_raw = owner_raw
            if override.evidence_authorized:
                return override, None, record
            decided = dict(
                final_action=override.final_action,
                candidate_source=override.candidate_source,
                confidence=override.confidence,
                reason=override.reason,
                memory_consulted=True,
                memory_source=override.memory_source,
                memory_evidence=mem.to_dict(),
                capability_selected=override.capability_selected,
                channel=channel, owner=canonical_owner, owner_raw=owner_raw,
                source=source,
            )
            return None, decided, record

        # -- 4. Ciclo de convergencia total (motor subordinado) -------------
        if record is None:
            try:
                from core.convergence_hook import run_convergence_cycle
                record = run_convergence_cycle(
                    text, source=source, channel=channel, owner=canonical_owner,
                )
            except Exception as exc:
                logger.warning("NucleusAuthority: convergence cycle failed (non-fatal): %s", exc)
                record = None

        nd = getattr(record, "nucleus_decision", None) if record is not None else None
        capability_snapshot = (
            getattr(record, "capability_snapshot", {}) if record is not None else {}
        ) or {}

        from core.smart_router import Strategy

        candidate_strategy = None
        candidate_source = ""
        confidence = 0.0
        reason = ""
        capability_selected = ""

        # -- 5. ¿El Núcleo (convergencia) ya decidió con evidencia propia? --
        if nd is not None and getattr(nd, "candidate_strategy", None) is not None:
            candidate_strategy = nd.candidate_strategy
            candidate_source = ""
            confidence = float(getattr(nd, "confidence", 0.0) or 0.0)
            reason = getattr(nd, "reason", "") or ""
            capability_selected = (getattr(nd, "evidence", {}) or {}).get(
                "capability_selected", "",
            )

            # --- Corrección de calidad (auditoría 2026-09-19) ---------------
            # ANSWER_FROM_EVIDENCE de total_convergence usa coherencia de
            # patrón/intent (CCTracker), NO relevancia léxica a la pregunta
            # concreta ni vigencia temporal. El Núcleo (esta fachada) aplica
            # aquí el veredicto de los 4 ejes de `mem` — un recuerdo
            # coherente NO es evidencia suficiente si es irrelevante o si el
            # dato exige actualidad. Si `mem` lo rechaza, se descarta esta
            # candidata y se cae al paso 6 (consulta auxiliar a SmartRouter)
            # exactamente como si convergencia no hubiera propuesto nada.
            if candidate_strategy == Strategy.ANSWER_FROM_EVIDENCE and not mem.sufficient:
                logger.info(
                    "NucleusAuthority: ANSWER_FROM_EVIDENCE rechazada por veredicto "
                    "de memoria (%s) — reevaluando via SmartRouter", mem.reason,
                )
                candidate_strategy = None
                candidate_source = ""
                reason = f"evidencia propia rechazada: {mem.reason}"

        if candidate_strategy is None:
            # -- 6. Sin candidata propia (o rechazada) -> SmartRouter AUXILIAR
            try:
                from core.smart_router import SmartRouter
                smart_route = SmartRouter().route(text, channel=channel, owner=canonical_owner)
                candidate_strategy = smart_route.strategy
                candidate_source = "smart_router"
                confidence = smart_route.confidence
                reason = (reason + " | " if reason else "") + smart_route.reason
            except Exception as exc:
                logger.debug("SmartRouter auxiliary classification failed: %s", exc)
                candidate_strategy = None
                reason = f"smart_router no disponible: {exc}"

        final_action = _strategy_to_action(candidate_strategy) if candidate_strategy else "CLARIFICATION"

        # -- 7. Forzar vigencia: dato cambiante sin evidencia fresca -> ONLINE
        # Regla GENERAL por categoría (ver _requires_freshness), nunca una
        # excepción para una entidad/frase concreta.
        if mem.requires_freshness and mem.temporal_validity != "fresh" and final_action != "ONLINE":
            cap_available = (capability_snapshot.get("capability_available") or {}).get("online_search")
            if cap_available is not False:
                final_action = "ONLINE"
                candidate_source = candidate_source or "smart_router"
                reason = (
                    f"{reason} | forzado a ONLINE: pregunta de dato cambiante sin "
                    f"evidencia reciente en memoria (vigencia)"
                )

        # -- 8. Validar capacidad requerida antes de aceptar la candidata ---
        cap_name = _CAPABILITY_BY_ACTION.get(final_action)
        if cap_name and candidate_source == "smart_router":
            cap_available = (capability_snapshot.get("capability_available") or {}).get(cap_name)
            if cap_available is False:
                final_action = "CLARIFICATION"
                reason = f"{reason} | capacidad '{cap_name}' no disponible/autorizada — degradado a CLARIFICATION"
            elif cap_name:
                capability_selected = capability_selected or cap_name

        decided = dict(
            final_action=final_action,
            candidate_source=candidate_source,
            confidence=confidence,
            reason=reason,
            memory_consulted=True,
            memory_source="vectrax.db stars (consulta incondicional, ver memory_evidence)",
            memory_evidence=mem.to_dict(),
            capability_selected=capability_selected,
            channel=channel,
            owner=canonical_owner,
            owner_raw=owner_raw,
            source=source,
        )
        return None, decided, record

    # -----------------------------------------------------------------
    # Ejecutores — solo ejecutan la acción ya decidida, no deciden nada.
    # -----------------------------------------------------------------

    def _dispatch_executor(
        self, response: NucleusResponse, text: str, record: Any,
    ) -> None:
        action = response.final_action
        mem_dict = response.memory_evidence or {}
        try:
            if action == "CLARIFICATION":
                if not response.answer:
                    lang = _detect_lang(text)
                    response.answer = (
                        "No tengo evidencia suficiente ni una herramienta clara para "
                        "responder eso con precisión. ¿Puedes darme más contexto?"
                        if lang != "en" else
                        "I don't have enough evidence or a clear tool to answer that "
                        "precisely. Can you give me more context?"
                    )
                response.evidence_authorized = True
                return

            if action in ("LOCAL", "IDENTITY"):
                response.tool_executed = "resolve_local"
                response.memory_source = response.memory_source or (
                    "vectrax.db stars (incluye memoria histórica ARGOS/Sinapsis migrada)"
                )
                # Reutiliza la respuesta ya calculada por la consulta de
                # memoria INCONDICIONAL (paso 2 de _decide) — evita repetir
                # la misma búsqueda por embeddings dos veces.
                sovereign_answer = mem_dict.get("sovereign_answer") or ""
                if not sovereign_answer:
                    from vectrax.resolver import resolve_local
                    result = resolve_local(text, response.channel, response.owner)
                    sovereign_answer = result.sovereign_answer
                    response.evidence = {
                        "context_stars": result.context_stars,
                        "top_score": result.top_score,
                    }
                else:
                    response.evidence = {
                        "context_stars": mem_dict.get("context_stars", 0),
                        "top_score": mem_dict.get("coherence", 0.0),
                        "relevance": mem_dict.get("relevance", 0.0),
                    }
                response.answer = sovereign_answer
                response.evidence_authorized = bool(sovereign_answer.strip())
                return

            if action == "MEMORY":
                response.tool_executed = "ingest"
                from vectrax.engine import ingest
                star = ingest(text=text, channel=response.channel, owner=response.owner)
                response.evidence = {"star_id": star.id, "repetition_count": star.repetition_count}
                response.answer = (
                    "Registrado." if star.repetition_count == 1 else "Actualizado."
                )
                response.evidence_authorized = True
                return

            if action == "ONLINE":
                response.tool_executed = "resolve_online"
                response.capability_selected = response.capability_selected or "online_search"
                from vectrax.resolver import resolve_online
                result = resolve_online(text, response.channel, response.owner)
                response.evidence = {
                    "sources": [s.title for s in result.sources],
                    "engines_used": result.engines_used,
                }
                response.answer = result.sovereign_answer
                response.evidence_authorized = bool(result.sources) and bool(result.sovereign_answer.strip())
                return

            if action == "PLACES":
                response.tool_executed = "search_places"
                response.capability_selected = response.capability_selected or "places_search"
                # Ubicación: PRIMERO lo que la consulta de memoria incondicional
                # ya encontró (paso 6 del requisito) — solo se pide si no hay
                # NADA en ninguna de las dos fuentes (tabla estructurada o
                # memoria en texto libre).
                remembered = mem_dict.get("remembered_location")
                if not remembered:
                    remembered = _extract_remembered_location(response.channel, response.owner)

                if not remembered:
                    response.final_action = "CLARIFICATION"
                    lang = _detect_lang(text)
                    response.answer = (
                        "Necesito tu ubicación para buscar lugares cercanos. "
                        "¿Me la compartes?"
                        if lang != "en" else
                        "I need your location to search nearby places. Can you share it?"
                    )
                    response.evidence = {"remembered_location": None}
                    response.evidence_authorized = True
                    return

                from vectrax.integrations.place_search import search_places
                search_text = text
                user_loc = None
                if remembered.get("lat") is not None:
                    user_loc = {"lat": remembered["lat"], "lng": remembered["lng"]}
                elif remembered.get("text"):
                    # No hay lat/lng, pero sí un nombre de lugar recordado en
                    # texto libre — se incorpora a la consulta en vez de
                    # pedirla de nuevo al usuario.
                    search_text = f"{text} en {remembered['text']}"

                result = search_places(search_text, user_location=user_loc)
                response.evidence = {
                    "found": result.get("found", False),
                    "n_results": len(result.get("results", []) or []),
                    "remembered_location": remembered,
                }
                response.answer = result.get("message", "")
                response.evidence_authorized = bool(response.answer.strip())
                return

            if action == "MARKET":
                response.tool_executed = "market_observer"
                response.capability_selected = response.capability_selected or "market_observer"
                response.answer = (
                    "La consulta de mercado quedó registrada; el motor de mercado "
                    "responde por su propio canal especializado (fuera del alcance "
                    "de esta prueba de reunificación)."
                )
                response.evidence = {"gap": "market executor not wired in this branch yet"}
                response.evidence_authorized = False
                return

            response.final_action = "CLARIFICATION"
            response.answer = (
                "Esta ruta todavía no tiene un ejecutor conectado en la rama de "
                "reunificación (gap documentado, no fabrico una respuesta)."
            )
            response.evidence = {"gap": f"no executor wired for action={action}"}
            response.evidence_authorized = False

        except Exception as exc:
            logger.exception("NucleusAuthority executor failed for action=%s", action)
            response.final_action = "CLARIFICATION"
            response.tool_executed = response.tool_executed or "error"
            response.answer = (
                "Ocurrió un problema real al intentar resolver esto; lo registro "
                "en vez de inventar una respuesta."
            )
            response.evidence = {"error": str(exc)[:200]}
            response.evidence_authorized = False


# ---------------------------------------------------------------------------
# Singleton de conveniencia (mismo patrón que get_convergence_engine())
# ---------------------------------------------------------------------------

_authority: Optional[NucleusAuthority] = None


def get_nucleus_authority() -> NucleusAuthority:
    global _authority
    if _authority is None:
        _authority = NucleusAuthority()
    return _authority
