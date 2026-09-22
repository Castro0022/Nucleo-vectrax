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


def _query_memory(text: str, channel: str, owner: str, owner_raw: str = "") -> MemoryEvidence:
    """Consulta de memoria INCONDICIONAL — se ejecuta para TODA entrada, sin
    excepciones de ruta (requisito explícito de la reunificación, segunda
    pasada 2026-09-19). El resultado puede ser suficiente, insuficiente,
    irrelevante o desactualizado, pero la consulta SIEMPRE ocurre.

    `owner_raw`: identidad cruda pre-alias (p.ej. "tg:<id>"), propagada tal
    cual a `resolve_local()` para que la memoria CANÓNICA
    (`vectrax.core_memory`, ver `vectrax/resolver.py`) pueda localizarse
    bajo la clave real con la que fue absorbida — nunca cambia qué stars
    se leen (eso sigue aislado por `channel`/`owner` canónico, sin excepción).
    """
    ev = MemoryEvidence(consulted=True)
    ev.requires_freshness = _requires_freshness(text)

    try:
        from vectrax.resolver import resolve_local
        result = resolve_local(text, channel, owner, owner_raw=owner_raw)
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

# Corrección 2026-09-20 (incidente real, correlation_id 25653679824e): una
# pregunta sobre la identidad del PROPIO usuario ("¿Quién soy yo?") -- NUNCA
# confundir con `_IDENTITY_SELF_RE` de arriba, que es sobre la identidad de
# VECTRAX -- caía sin override en `is_personal_memory_query()` ->
# `Strategy.RESOLVE_PERSONAL_MEMORY` -> `retrieve_personal_memory()`, una
# recuperación DIFUSA (fragmentos + síntesis) que agrega cualquier hecho
# RECIENTE del owner vía un "contexto reciente" genérico
# (`resolve_local()::_select_profile_summary_stars()`), sin filtrar por
# relevancia real a "quién soy". En producción esto mezcló un dato no
# relacionado ("Mi socio se llama Beltrán") como si fuera la identidad del
# propio usuario que preguntaba. Esta pregunta debe resolverse SIEMPRE de
# forma determinística -- canal -> identity_aliases -> owner canónico (ya
# resuelto en el paso 1/1a de `_decide()`, recibido aquí como `owner`) --
# nunca vía fragmentos mezclados. Tolerante a un "¿" inicial opcional (el
# anclaje `^...$` de `vectrax.resolver._PROFILE_SUMMARY_RE` NO lo es, gap
# preexistente ajeno a este incidente, no corregido aquí).
_USER_IDENTITY_SELF_RE = re.compile(
    r"^[\s¿]*qui[eé]n\s+soy(?:\s+yo)?\s*\??\s*$"
    r"|^[\s¿]*who\s+am\s+i\s*\??\s*$"
    r"|\bc[oó]mo\s+me\s+llamo\b|\bcu[aá]l\s+es\s+mi\s+nombre\b"
    r"|\bwhat'?s\s+my\s+name\b|\bwhat\s+is\s+my\s+name\b",
    re.IGNORECASE,
)

# Corrección 2026-09-20 (incidente real, correlation_id 8c667c0b9f4a-b): el
# creador CORRIGIENDO una identidad equivocada en la misma frase ("No, yo
# soy Mario, tú eres Vectrax") caía en clasificación de INGESTA (STORE) --
# el pipeline lo trataba como un hecho nuevo a guardar y respondía
# "Registrado." en vez de reconocer la corrección de identidad. Esta es una
# afirmación de identidad (símil a `_USER_IDENTITY_SELF_RE`), no una nota
# para memoria -- debe resolverse como IDENTITY, nunca como STORE.
# Acotado al creador (única identidad que este override puede afirmar sin
# ambigüedad); cualquier otro texto sigue su camino normal.
_IDENTITY_CORRECTION_RE = re.compile(
    r"\byo\s+soy\s+mario\b[^.!?\n]*\bvectrax\b"
    r"|\bi'?m\s+mario\b[^.!?\n]*\bvectrax\b",
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

# Corrección 2026-09-20 (regresión reportada: "Háblame de mercado" / "¿Cuántos
# dominios tienes?" / "¿Qué capacidades tienes?" dejaron de responder desde
# el conocimiento interno de Vectrax). Causa raíz: `core/operator/
# external_gateway.py` tiene DOS capacidades reales -- el "DOMAIN CRITERION
# GATE" (STEP 4.2a3, `core.learn.criterion.build_criterion_result()`, el
# "mercado aprendido por Vectrax") y el conteo de dominios/capacidades vía
# self-context -- que NUNCA se migraron a `NucleusAuthority`. Desde que este
# módulo se convirtió en "autoridad única" (2026-09-19) y suprime
# incondicionalmente el pipeline legacy de `ExternalGateway` en cuanto
# produce CUALQUIER respuesta (ver `core/transport/pipeline_worker.py`,
# bloque "NÚCLEO: AUTORIDAD ÚNICA"), esas dos consultas nunca volvían a
# alcanzar el gate legacy y caían al fallback genérico de SmartRouter
# (MEMORY/ONLINE). Estos tres overrides son ADITIVOS -- reutilizan las
# MISMAS funciones que ya usa `external_gateway.py`, sin tocar la prioridad
# de memoria personal (paso 5.5) ni de ninguna otra ruta.

_CAPABILITIES_SELF_RE = re.compile(
    r"\bqu[eé]\s+capacidades\s+tienes\b|\bqu[eé]\s+puedes\s+hacer\b|"
    r"\bcu[aá]les\s+son\s+tus\s+capacidades\b"
    r"|\bwhat\s+capabilities\s+do\s+you\s+have\b|\bwhat\s+can\s+you\s+do\b",
    re.IGNORECASE,
)

_DOMAINS_COUNT_RE = re.compile(
    r"\bcu[aá]ntos\s+dominios\s+tienes\b|\bcu[aá]ntos\s+dominios\b|"
    r"\bqu[eé]\s+dominios\s+(?:tienes|conoces|manejas)\b"
    r"|\bhow\s+many\s+domains\s+do\s+you\s+have\b|\bwhat\s+domains\s+do\s+you\s+(?:know|have)\b",
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


_NAME_STATEMENT_RE = re.compile(
    r"(?:me\s+llamo|mi\s+nombre\s+es|my\s+name\s+is|call\s+me)\s+"
    r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s]{1,40}?)(?:[.,;\n]|$)",
    re.IGNORECASE,
)


def _creator_owner_const() -> str:
    from vectrax.identity import CREATOR_OWNER
    return CREATOR_OWNER


def _resolve_creator_canonical_name(user_id: str) -> str:
    """Identidad canónica del creador -- SIEMPRE "Mario Bravo Castro",
    NUNCA reemplazable por un nombre encontrado en memoria/perfil ni por un
    valor YA CACHEADO (posiblemente contaminado, p.ej. "Beltrán") de antes
    de esta corrección. Usa `identity_anchor.refresh_anchor()` (invalida el
    caché de sesión y recarga) en vez de `get_anchored_identity()` --
    `get_anchored_identity()` devuelve directamente cualquier `IdentityAnchor`
    YA cacheado sin volver a verificarlo, así que un anchor contaminado
    cacheado ANTES de este fix sobreviviría indefinidamente si se usara esa
    función aquí. `refresh_anchor()` fuerza una recarga real cada vez,
    garantizando que la protección aplicada en
    `identity_anchor.get_anchored_identity()` (el nombre semilla del creador
    SIEMPRE gana sobre `user_memory`) se re-evalúe en cada pregunta de
    identidad, nunca confiando en un estado en memoria potencialmente viejo.
    """
    try:
        from vectrax.identity_anchor import refresh_anchor
        anchor = refresh_anchor(user_id)
        if anchor and anchor.is_creator and anchor.name:
            return anchor.name
    except Exception as exc:
        logger.debug("_resolve_creator_canonical_name: identity_anchor lookup failed: %s", exc)
    return "Mario Bravo Castro"


def _resolve_user_own_identity(
    owner: str, channel: str, lang: str, owner_raw: str = "",
) -> str:
    """Resuelve DETERMINÍSTICAMENTE quién es el usuario que pregunta --
    nunca vía fragmentos difusos ni síntesis LLM. Fuente única: la
    identidad canónica ya resuelta (`owner`, ver `_decide()` paso 1/1a) más,
    si el usuario NO es el creador, el nombre que él mismo declaró
    explícitamente (categoría 'identity', entidad 'name' en
    `star_provenance` -- la MISMA derivación que `star_deriver.py` usa para
    'me llamo X'/'mi nombre es X', ver `core/memory/star_deriver.py`).
    Nunca inventa un nombre ni agrega memoria no relacionada.

    Para el creador, la fuente de verdad es `vectrax.identity_anchor
    .get_anchored_identity()` -- el mismo módulo cuyo contrato es "una vez
    conocida la identidad, jamás se pierde ni se contradice" (corrección
    2026-09-20: ese módulo ahora garantiza que el nombre del creador es
    canónico y no puede quedar contaminado por caché/memoria; ver
    `identity_anchor.get_anchored_identity()`)."""
    from vectrax.identity import CREATOR_OWNER

    if owner == CREATOR_OWNER:
        canonical_name = _resolve_creator_canonical_name(owner_raw or owner)
        return (
            f"Eres {canonical_name}, mi creador." if lang != "en"
            else f"You are {canonical_name}, my creator."
        )

    try:
        from vectrax import db
        from vectrax.models import STAR_CATEGORY_IDENTITY

        rows = db.list_star_provenance(
            tenant_id="default", channel=channel, owner=owner,
            category=STAR_CATEGORY_IDENTITY, status=None, limit=20,
        )
        name = ""
        for row in rows:
            if row.get("status") == "superseded" or row.get("entity") != "name":
                continue
            literal_fragments = row.get("literal_fragments") or []
            candidate_text = literal_fragments[-1] if literal_fragments else (
                row.get("normalized_assertion", "")
            )
            m = _NAME_STATEMENT_RE.search(candidate_text or "")
            if m:
                name = m.group(1).strip()
                break
        if name:
            return (
                f"Te llamas {name}." if lang != "en" else f"Your name is {name}."
            )
    except Exception as exc:
        logger.debug("_resolve_user_own_identity: lookup failed: %s", exc)

    return (
        "Todavía no tengo tu nombre registrado. Si quieres, dime cómo te llamas."
        if lang != "en" else
        "I don't have your name registered yet. If you'd like, tell me your name."
    )


def _try_self_knowledge_override(
    text: str, channel: str, owner: str, source: str, mem: MemoryEvidence,
    owner_raw: str = "",
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

    if _IDENTITY_CORRECTION_RE.search(text) and owner == _creator_owner_const():
        canonical_name = _resolve_creator_canonical_name(owner_raw or owner)
        answer = (
            f"Correcto: tú eres {canonical_name} y yo soy Vectrax Core."
            if lang != "en" else
            f"Correct: you are {canonical_name} and I am Vectrax Core."
        )
        return NucleusResponse(
            final_action="IDENTITY",
            candidate_source="",
            confidence=1.0,
            reason=(
                "deterministic override: corrección de identidad del "
                "creador en la misma frase -- IDENTITY, nunca STORE"
            ),
            memory_consulted=True,
            memory_source="identity_anchor (creador canónico)",
            memory_evidence=mem.to_dict(),
            capability_selected="",
            tool_executed="user_identity_resolver",
            evidence={"kind": "identity_correction", "canonical_owner": owner},
            evidence_authorized=True,
            answer=answer,
            channel=channel, owner=owner, source=source,
        )

    if _USER_IDENTITY_SELF_RE.search(text):
        answer = _resolve_user_own_identity(owner, channel, lang, owner_raw=owner_raw)
        return NucleusResponse(
            final_action="IDENTITY",
            candidate_source="",
            confidence=1.0,
            reason=(
                "deterministic override: pregunta sobre la identidad del "
                "PROPIO usuario (channel -> identity_aliases -> owner "
                "canónico) -- nunca vía retrieve_personal_memory"
            ),
            memory_consulted=True,
            memory_source=(
                "identity_aliases (owner canónico) + star_provenance "
                "(categoría identity, entidad name)"
            ),
            memory_evidence=mem.to_dict(),
            capability_selected="",
            tool_executed="user_identity_resolver",
            evidence={"kind": "user_identity", "canonical_owner": owner},
            evidence_authorized=True,
            answer=answer,
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

    # Las tres nuevas rutas de abajo (capacidades/dominios/criterio) NUNCA
    # deben desplazar una consulta de memoria personal -- si el texto ya
    # califica como memoria personal (mismo detector genérico del paso 5.5),
    # se saltan por completo y se deja que el flujo normal la resuelva.
    _is_personal = False
    try:
        from vectrax.resolver import is_personal_memory_query as _is_pmq
        _is_personal = _is_pmq(text)
    except Exception:
        pass

    if not _is_personal and _CAPABILITIES_SELF_RE.search(text):
        # Mismo mecanismo que _UNKNOWN_FALLBACK_RE (capability_narrator) --
        # aquí disparado por "qué capacidades tienes" en vez de "qué haces
        # cuando no sabes". Restaura la respuesta de autoconocimiento que
        # antes solo daba `vectrax.self_context._is_capability_query()` en
        # el pipeline legacy de ExternalGateway.
        try:
            from core.self_observation.capability_context import build_capability_context
            from core.self_observation.capability_narrator import narrate

            class _CapShim:
                domain = None
                task_type = None
                capability_query = True

            ctx = build_capability_context(_CapShim())
            answer = narrate(ctx, lang=lang if lang in ("es", "en") else "es")
        except Exception as exc:
            logger.debug("capabilities self-knowledge override failed: %s", exc)
            answer = ""
        if answer:
            return NucleusResponse(
                final_action="IDENTITY",
                candidate_source="",
                confidence=1.0,
                reason="self-knowledge override: '¿qué capacidades tienes?'",
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

    if not _is_personal and _DOMAINS_COUNT_RE.search(text):
        # Conteo real de dominios de conocimiento (gravity + domain_knowledge
        # + verification_ledger) -- misma fuente que `core.learn.criterion
        # .known_domains()` usa internamente para el gate de criterio.
        try:
            from core.learn.criterion import known_domains
            doms = known_domains()
        except Exception as exc:
            logger.debug("known_domains failed: %s", exc)
            doms = []
        if lang == "en":
            answer = (
                f"I have {len(doms)} active knowledge domains: {', '.join(doms)}."
                if doms else
                "I don't have any knowledge domains with accumulated evidence yet."
            )
        else:
            answer = (
                f"Tengo {len(doms)} dominios de conocimiento activos: {', '.join(doms)}."
                if doms else
                "Todavía no tengo dominios de conocimiento con evidencia acumulada."
            )
        return NucleusResponse(
            final_action="IDENTITY",
            candidate_source="",
            confidence=1.0,
            reason="self-knowledge override: '¿cuántos dominios tienes?'",
            memory_consulted=True,
            memory_source="core.learn.criterion.known_domains() (gravity + domain_knowledge + verification_ledger)",
            memory_evidence=mem.to_dict(),
            capability_selected="",
            tool_executed="domain_census",
            evidence={"kind": "self_knowledge", "domains": doms},
            evidence_authorized=True,
            answer=answer,
            channel=channel, owner=owner, source=source,
        )

    # MARKET DATA RESOLVE -- consultas de datos de mercado EN VIVO (precio,
    # snapshot, tendencia) reconocidas por `intents.market_intents
    # .detect_market_intent()`. Reutiliza el MISMO ejecutor REAL que ya
    # usaba `ExternalGateway._try_market_resolve()` (market_vigilance +
    # market_intents) en vez del stub "no wired" que tenía `_dispatch_executor`
    # -- esto es lo que responde con "el mercado aprendido por Vectrax" para
    # "Háblame de mercado"/"cómo está el mercado"/"precio de btc", etc.
    # Desambiguación medida (2026-09-22): `detect_market_intent()` clasifica
    # como `watchlist_review` frases que preguntan por las PROPUESTAS del
    # propio sistema ("enséñame las propuestas que siguen sin revisar",
    # "listado de ideas sin aprobar") — comprobado llamándolo directamente.
    # Sin esta guarda, esas preguntas se respondían con precios de mercado en
    # vivo. No se toca `market_intents` (su heurística sirve a su dominio):
    # se cede el turno al override de evidencia interna SOLO cuando el texto
    # trae un ancla léxica de observación propia. Las consultas de mercado
    # reales ("cómo está el mercado", "precio de btc", "háblame de mercado",
    # "muéstrame mi watchlist") no traen ninguna y siguen igual.
    if not _is_personal:
        try:
            from core.nucleus import evidence_intent as _ev_intent
            _defers_to_internal_evidence = _ev_intent.classify(text).detected
        except Exception:
            _defers_to_internal_evidence = False

        try:
            from intents.market_intents import detect_market_intent
            if not _defers_to_internal_evidence and detect_market_intent(text) is not None:
                from core.operator.external_gateway import ExternalGateway
                _market_answer = ExternalGateway._try_market_resolve(text, user_id=owner)
                if _market_answer and not _market_answer.lower().startswith(("error", "error:")):
                    return NucleusResponse(
                        final_action="MARKET",
                        candidate_source="",
                        confidence=0.9,
                        reason="self-knowledge override: consulta de datos de mercado (market_intents)",
                        memory_consulted=True,
                        memory_source="intents.market_intents / services.market_vigilance",
                        memory_evidence=mem.to_dict(),
                        capability_selected="market_observer",
                        tool_executed="market_resolve",
                        evidence={"kind": "market_data"},
                        evidence_authorized=True,
                        answer=_market_answer,
                        channel=channel, owner=owner, source=source,
                    )
        except Exception as exc:
            logger.debug("market resolve override failed: %s", exc)

    # DOMAIN CRITERION GATE (mirrors core/operator/external_gateway.py STEP
    # 4.2a3, `core.learn.criterion.build_criterion_result()`) -- restaurado
    # aquí porque `NucleusAuthority` suprime incondicionalmente el pipeline
    # legacy de `ExternalGateway` en cuanto produce cualquier respuesta (ver
    # nota más arriba), y ese pipeline nunca vuelve a correr para dejarlo
    # actuar. Precedencia: solo si NO es una consulta de precio/dato puntual
    # (esas se resuelven vía MARKET/ONLINE, no vía criterio aprendido) --
    # mismo guard `_price_only` que usa el gate legacy. Guard adicional
    # (no presente en el gate legacy, necesario aquí porque este override
    # corre ANTES de la clasificación memory/local/online): solo aplica a
    # preguntas/pedidos de opinión, nunca a un enunciado declarativo (p.ej.
    # "vivo en Miami" NO debe convertirse en "mi opinión sobre real estate").
    _looks_like_question = (
        "?" in text or "¿" in text
        or bool(re.search(
            r"^\s*(qu[ei]|qu[eé]|c[oó]mo|cu[aá]l|cu[aá]ndo|d[oó]nde|h[aá]blame|"
            r"cu[eé]ntame|what|who|how|tell\s+me)\b",
            text, re.IGNORECASE,
        ))
    )
    try:
        from core.learn.criterion import (
            detect_criterion_request, detect_domain as _detect_domain_kw,
            build_criterion_result,
        )
        _crit_req = detect_criterion_request(text)
        _dom = _detect_domain_kw(text) if (_is_personal is False and _looks_like_question) else None
        _price_only = False
        if _dom == "market" and not _crit_req:
            try:
                from intents.market_intents import detect_market_intent
                _price_only = detect_market_intent(text) is not None
            except Exception:
                _price_only = False
        if _dom and not _price_only and not _is_personal:
            _crit = build_criterion_result(_dom, text)
            if _crit and _crit.text:
                return NucleusResponse(
                    final_action="LOCAL",
                    candidate_source="",
                    confidence=0.85,
                    reason=f"self-knowledge override: criterio aprendido sobre dominio '{_dom}'",
                    memory_consulted=True,
                    memory_source=f"core.learn.criterion ({_dom}: gravity + domain_knowledge)",
                    memory_evidence=mem.to_dict(),
                    capability_selected="",
                    tool_executed="domain_criterion",
                    evidence={"kind": "self_knowledge", "domain": _dom, "origin": _crit.origin},
                    evidence_authorized=True,
                    answer=_crit.text,
                    channel=channel, owner=owner, source=source,
                )
    except Exception as exc:
        logger.debug("domain criterion override failed: %s", exc)

    return None


# ---------------------------------------------------------------------------
# Override de evidencia interna — la observación propia de Vectrax
# ---------------------------------------------------------------------------

def _try_internal_evidence_override(
    text: str, channel: str, owner: str, source: str, mem: MemoryEvidence,
    owner_raw: str = "",
) -> Optional[NucleusResponse]:
    """Resuelve preguntas sobre la OBSERVACIÓN INTERNA de Vectrax consultando
    las fuentes reales que el sistema ya produce.

    Devuelve `None` si la pregunta no es sobre el estado interno — en ese caso
    el Núcleo sigue su camino normal (convergencia, capacidades, herramientas).

    Invariantes que esta función garantiza, y que
    `tests/test_internal_evidence_nucleus.py` verifica:

    * El texto de la respuesta lo produce `evidence_intent.build_answer()`,
      que es determinista y solo usa campos del `EvidenceResult`. El LLM no
      participa: no puede inventar un diagnóstico ni convertir `pending` en
      resuelto.
    * `final_action="LOCAL"` (valor ya existente del contrato) porque la
      respuesta se resuelve con datos propios — nunca escala a herramienta
      externa.
    * La autorización sale de la identidad canónica ya resuelta en
      `_decide()` (pasos 1/1a/1b), no del canal de transporte.
    * Una consulta no autorizada devuelve igualmente un override (con el
      texto de rechazo) para que ninguna otra capa pueda filtrar el dato.
    """
    try:
        from core.nucleus import evidence_intent, internal_evidence as _ie
        from core.nucleus.internal_evidence import EvidenceStatus
    except Exception as exc:  # pragma: no cover - defensa de import
        logger.debug("internal evidence unavailable: %s", exc)
        return None

    intent = evidence_intent.classify(text)
    if not intent.detected:
        return None

    # Se resuelve por la FÁBRICA del módulo (no instanciando la clase aquí)
    # para que una prueba pueda sustituir la FUENTE DE DATOS por una fixture
    # aislada sin falsear el recorrido: identidad, clasificación de intención,
    # autorización, redacción y adaptación de canal siguen ejecutándose de
    # verdad. Es el único punto de inyección, y es deliberado.
    evidence = _ie.get_internal_evidence(owner, owner_raw=owner_raw)
    result = evidence_intent.fetch(intent, evidence)
    answer = evidence_intent.build_answer(result)

    authorized = result.status is not EvidenceStatus.UNAUTHORIZED
    confidence = {
        EvidenceStatus.OK: 0.95,
        EvidenceStatus.STALE: 0.70,
        EvidenceStatus.EMPTY: 0.60,
        EvidenceStatus.UNAVAILABLE: 0.40,
        EvidenceStatus.UNAUTHORIZED: 1.0,   # certeza total sobre el rechazo
    }.get(result.status, 0.5)

    return NucleusResponse(
        final_action="LOCAL",
        candidate_source="",
        confidence=confidence,
        reason=(
            f"internal-evidence override: la pregunta apunta a la observación "
            f"interna (familia={intent.family}, estado={result.status.value}). "
            f"Respuesta construida desde la fuente real, sin LLM."
        ),
        memory_consulted=True,
        memory_source="vectrax.db stars + core.nucleus.internal_evidence",
        memory_evidence=mem.to_dict(),
        capability_selected="internal_evidence",
        tool_executed="core.nucleus.internal_evidence",
        evidence=evidence_intent.describe_for_evidence_field(intent, result),
        evidence_authorized=authorized,
        answer=answer,
        channel=channel, owner=owner, owner_raw=owner_raw, source=source,
    )


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
        Strategy.RESOLVE_PERSONAL_MEMORY: "PERSONAL_MEMORY",
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
                "PERSONAL_MEMORY": Strategy.RESOLVE_PERSONAL_MEMORY,
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

        # -- 1a. UID de Telegram del creador -> identidad canónica -----------
        # Segundo bug real de producción (2026-09-20, tercera pasada): los
        # mensajes reales de Telegram traen `owner="tg:<id>"` (ver
        # `vectrax/telegram_gateway.py::_handle()`, `tg_uid = f"tg:{uid}"`),
        # NUNCA literalmente "mario" u "owner" — `identity_aliases
        # .resolve_owner()` no tiene fila para ese valor crudo, así que
        # `canonical_owner` seguía siendo "tg:<id>" y el paso 1b de abajo
        # clasificaba SIEMPRE los mensajes de Telegram del propio Mario como
        # CHANNEL_USER (nunca CHANNEL_CREATOR). Se replica aquí la MISMA
        # comprobación que ya usa `external_gateway.py::_is_creator_uid()` y
        # `vectrax/relational_identity.py::_is_creator()` (despojar el
        # prefijo "tg:" y comparar contra VX_CREATOR_ID, default
        # "2030762343") — no se inventa una identidad nueva, se reconoce la
        # MISMA identidad que el resto del sistema ya trata como creador.
        try:
            import os as _os
            from vectrax.identity import CREATOR_OWNER as _CREATOR_OWNER_CHK
            _creator_uid = _os.environ.get("VX_CREATOR_ID", "2030762343")
            _owner_norm = (owner_raw or "").replace("tg:", "")
            if _owner_norm and _owner_norm == _creator_uid:
                canonical_owner = _CREATOR_OWNER_CHK
        except Exception:
            pass

        # -- 1b. Canal INTERNO (creator/user) — NUNCA el canal externo crudo --
        # Bug real de producción (2026-09-19): `pipeline_worker.py` pasa el
        # canal de TRANSPORTE ("telegram") como `channel=`, pero
        # `vectrax.engine.ingest()`/`validate_channel()` SOLO aceptan
        # "creator"|"user" y lanzan `ChannelViolation` con cualquier otro
        # valor — esto tumbó `core_api` (excepción no capturada). El canal
        # interno se deriva SIEMPRE de la identidad canónica, igual que ya
        # hace `external_gateway.py::_resolve_via_pipeline()`
        # (`internal_channel = "creator" if _is_creator_uid(...) else "user"`).
        # `channel` deja de usarse para lecturas/escrituras en vectrax.db a
        # partir de aquí — se sobrescribe con el valor interno correcto.
        try:
            from vectrax.identity import CREATOR_OWNER, CHANNEL_CREATOR, CHANNEL_USER
            channel = CHANNEL_CREATOR if canonical_owner == CREATOR_OWNER else CHANNEL_USER
        except Exception:
            channel = "user"

        # -- 2. CONSULTA DE MEMORIA INCONDICIONAL ---------------------------
        # Se ejecuta SIEMPRE, para TODA entrada, sin excepción de ruta —
        # antes de cualquier override, antes de convergencia/SmartRouter.
        # El resultado puede ser suficiente, insuficiente, irrelevante o
        # desactualizado, pero la consulta ocurre siempre (requisito
        # explícito, reunificación 2026-09-19).
        mem = _query_memory(text, channel, canonical_owner, owner_raw=owner_raw)

        # -- 3. Overrides de autoconocimiento explícito ---------------------
        override = _try_self_knowledge_override(
            text, channel, canonical_owner, source, mem, owner_raw=owner_raw,
        )
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

        # -- 3b. Evidencia interna (observación propia de Vectrax) ----------
        # Reunificación del Núcleo con su observación interna (2026-09-22):
        # hasta aquí el Núcleo sabía responder QUIÉN es, pero no QUÉ ha
        # observado, diagnosticado, propuesto o ejecutado — esa evidencia
        # existía (IdeaStore, audit_ledger, universe_census,
        # universe_observer, domain_knowledge) y alimentaba el Dashboard,
        # pero NINGUNA línea de este módulo la consultaba. Ese era el defecto
        # real: no un problema de canal ni de routing, sino una fuente sin
        # cablear.
        #
        # PRECEDENCIA (medida, no supuesta): va DESPUÉS del override de
        # autoconocimiento, que conserva íntegra su prioridad. Esto importa:
        # una primera versión lo puso antes y ensombreció la ruta
        # `_DOMAINS_COUNT_RE` -> `domain_census` ("¿Cuántos dominios
        # tienes?"), que ya respondía bien — lo detectó
        # `test_incident_20260920_identity_and_capacity_regression.py
        # ::test_domains_and_capabilities_keep_self_knowledge_routes`. La
        # regla es reutilizar el contrato existente cuando ya es equivalente,
        # no reemplazarlo: identidad, capacidades, gravedad, patrones
        # aprendidos y censo de dominios siguen resolviéndose por sus rutas
        # de siempre, y esta rama solo atiende lo que ninguna cubría.
        #
        # La ÚNICA excepción es el solape con `detect_market_intent()`, que
        # se resuelve de forma quirúrgica dentro del propio override de
        # mercado (ver `_try_self_knowledge_override`), no invirtiendo aquí
        # el orden general.
        evidence_override = _try_internal_evidence_override(
            text, channel, canonical_owner, source, mem, owner_raw=owner_raw,
        )
        if evidence_override is not None:
            evidence_override.owner_raw = owner_raw
            return evidence_override, None, record

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
            # -- 5.5 CONTRATO DEFINITIVO DE MEMORIA PERSONAL -----------------
            # El Núcleo decide RESOLVE_PERSONAL_MEMORY ÚNICA VEZ y de forma
            # DIRECTA -- sin invocar `SmartRouter.route()`/`classify_intent()`
            # -- en cuanto detecta genéricamente (vectrax.resolver
            # .is_personal_memory_query(): historial, relaciones, decisiones
            # o experiencia previa del propio usuario -- sin lista cerrada de
            # frases) que esta es una consulta de memoria personal. Esto
            # elimina la raíz del incidente real ("¿Quién es mi novia?" ->
            # route=online): SmartRouter NUNCA vuelve a clasificar esta
            # consulta, así que nunca puede proponer ONLINE/PLACES/MARKET
            # para ella en primer lugar (el guardrail de la sección 7b sigue
            # como última puerta de respaldo para cualquier otro camino).
            try:
                from vectrax.resolver import is_personal_memory_query
                if is_personal_memory_query(text):
                    candidate_strategy = Strategy.RESOLVE_PERSONAL_MEMORY
                    candidate_source = "nucleus"
                    confidence = 0.9
                    reason = (
                        (reason + " | " if reason else "")
                        + "detección genérica de memoria personal -> "
                        "RESOLVE_PERSONAL_MEMORY (sin reinterpretación de SmartRouter)"
                    )
            except Exception as exc:
                logger.debug("is_personal_memory_query check failed: %s", exc)

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
        if (
            mem.requires_freshness and mem.temporal_validity != "fresh"
            and final_action not in ("ONLINE", "PERSONAL_MEMORY")
        ):
            cap_available = (capability_snapshot.get("capability_available") or {}).get("online_search")
            if cap_available is not False:
                final_action = "ONLINE"
                candidate_source = candidate_source or "smart_router"
                reason = (
                    f"{reason} | forzado a ONLINE: pregunta de dato cambiante sin "
                    f"evidencia reciente en memoria (vigencia)"
                )

        # -- 7b. GUARDRAIL DE PRIVACIDAD: consultas relacionales personales
        # NUNCA resuelven vía ONLINE/PLACES/MARKET -- ver caso real "¿Quién
        # es mi novia?" (2026-09-20): el enrutamiento (semántico o regex)
        # eligió ONLINE, el sistema buscó en internet y "descubrió" una
        # persona que no existía, presentándola como respuesta verificada.
        # Esta es la ÚLTIMA puerta antes de despachar: sin importar qué
        # propuso convergencia o SmartRouter, una pregunta "quién es mi X"
        # (ver vectrax.resolver.is_personal_relationship_query -- vocabulario
        # genérico, sin nombres propios, compartido con el clasificador
        # semántico y el router) se fuerza de vuelta a LOCAL. La única
        # fuente lícita para esa pregunta es la memoria propia del usuario;
        # si no hay evidencia, resolve_local() ya declara honestamente que
        # no la tiene (nunca inventa ni sale a buscarla afuera).
        if final_action in ("ONLINE", "PLACES", "MARKET"):
            try:
                from vectrax.resolver import is_personal_memory_query
                if is_personal_memory_query(text):
                    logger.info(
                        "NucleusAuthority: privacy guard blocked %s -> PERSONAL_MEMORY "
                        "(consulta de memoria personal nunca sale a internet)",
                        final_action,
                    )
                    reason = (
                        f"{reason} | bloqueado por privacidad: consulta de memoria "
                        f"personal nunca resuelve vía {final_action}"
                    )
                    final_action = "PERSONAL_MEMORY"
                    candidate_source = candidate_source or "privacy_guard"
            except Exception:
                pass

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
                    result = resolve_local(
                        text, response.channel, response.owner,
                        owner_raw=response.owner_raw,
                    )
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

            if action == "PERSONAL_MEMORY":
                # PARTE 3 (2026-09-20): recuperación universal -- combina
                # fecha + tema/proyecto/persona + keyword + similitud
                # semántica + decisiones + orden temporal en UNA sola
                # capacidad (core/memory/personal_memory_retrieval.py), en
                # vez de reutilizar el mero resultado genérico de
                # resolve_local() (que solo hace similitud por embeddings).
                # NUNCA reemplaza LOCAL/IDENTITY arriba -- accion distinta,
                # dispatch distinto, sin tocar su comportamiento.
                response.tool_executed = "retrieve_personal_memory"
                response.memory_source = (
                    "conversation_ledger (evidencia primaria) + star_provenance "
                    "(índice por categoría) + resolve_local (semántico)"
                )
                from core.memory.personal_memory_retrieval import retrieve_personal_memory
                pm_result = retrieve_personal_memory(
                    text, channel=response.channel, owner=response.owner,
                    owner_raw=response.owner_raw,
                )
                response.answer = pm_result.sovereign_answer
                response.evidence = {
                    "context_stars": len(pm_result.fragments),
                    "trace": pm_result.trace.to_dict(),
                }
                response.evidence_authorized = pm_result.sufficient
                return

            if action == "MEMORY":
                # PARTE 2 (2026-09-20): pasa por el mismo derivador selectivo
                # que la ingesta pasiva de fondo, con explicit_user_intent=True
                # -- el usuario pidio explicitamente guardar esto (p.ej.
                # "guardar"/"recuerdame"), asi que nunca se descarta por
                # insignificante, pero SI gana categoria/entidad/procedencia
                # consistentes con el resto del sistema.
                response.tool_executed = "ingest"
                from core.memory.star_deriver import derive_and_store_star
                star = derive_and_store_star(
                    text=text, channel=response.channel, owner=response.owner,
                    explicit_user_intent=True,
                )
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
                # Corrección 2026-09-20: reemplaza el stub "not wired" por el
                # ejecutor REAL ya existente en ExternalGateway (market_vigilance
                # + intents.market_intents) -- cubre el caso en que SmartRouter
                # (no el override de arriba) propuso MARKET directamente
                # (p.ej. "precio de btc").
                response.tool_executed = "market_resolve"
                response.capability_selected = response.capability_selected or "market_observer"
                try:
                    from core.operator.external_gateway import ExternalGateway
                    _market_answer = ExternalGateway._try_market_resolve(text, user_id=response.owner)
                except Exception as exc:
                    logger.debug("market executor failed: %s", exc)
                    _market_answer = ""
                if _market_answer and not _market_answer.lower().startswith(("error", "error:")):
                    response.answer = _market_answer
                    response.evidence = {"kind": "market_data"}
                    response.evidence_authorized = True
                else:
                    response.answer = (
                        "No pude obtener datos de mercado en este momento; "
                        "lo digo explícitamente en vez de inventar una cifra."
                    )
                    response.evidence = {"gap": _market_answer or "market executor returned nothing"}
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
