"""
Vectrax — Recuperación Universal de Memoria Personal
=========================================================================
PARTE 3 del contrato de memoria conversacional multiusuario (2026-09-20).

Una ÚNICA capacidad de recuperación (`retrieve_personal_memory()`) que
responde CUALQUIER consulta de memoria personal genérica — por fecha,
tema, proyecto, persona, keyword, similitud semántica, decisiones, orden
temporal, o combinaciones de lo anterior — sin listas cerradas de frases
ni casos hardcodeados de ningún usuario/tema/fecha concreto.

Fuentes combinadas (aditivo sobre lo ya construido, sin duplicar nada):
  - `core.memory.conversation_ledger` (PARTE 1): evidencia PRIMARIA —
    mensajes literales originales, con filtro por rango temporal/keyword.
  - `vectrax.db.star_provenance` (PARTE 2): ÍNDICE estructurado por
    categoría/entidad/confianza/estado, para localizar rápidamente
    decisiones, relaciones, proyectos, preferencias, etc.
  - `vectrax.resolver.resolve_local()` (ya existente): similitud
    semántica por embeddings sobre `vectrax.db stars` — reutilizada tal
    cual como componente, nunca reimplementada.

Traza interna completa (`RetrievalTrace`): memoria consultada, eventos
del ledger recuperados, estrellas usadas, rango temporal aplicado,
categoría/keyword detectados, scores, fuente de cada fragmento, y
confianza combinada. Abstención honesta (`sufficient=False`) cuando no
hay evidencia real — nunca fabrica una respuesta.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from vectrax.models import (
    STAR_CATEGORY_COMMITMENT,
    STAR_CATEGORY_DECISION,
    STAR_CATEGORY_GOAL,
    STAR_CATEGORY_PREFERENCE,
    STAR_CATEGORY_PROJECT,
    STAR_CATEGORY_RELATION,
)

logger = logging.getLogger("vectrax.memory.personal_memory_retrieval")

_DEFAULT_TENANT = "default"

_STOPWORDS = {
    "que", "qué", "quien", "quién", "como", "cómo", "cuando", "cuándo",
    "donde", "dónde", "cual", "cuál", "cuales", "cuáles", "cuanto", "cuánto",
    "el", "la", "los", "las", "del", "al", "un", "una", "unos", "unas",
    "es", "son", "fue", "era", "eran", "ser", "estar", "está", "actualmente",
    "actual", "para", "ti", "tu", "tú", "tus", "mi", "mí", "mis", "sobre",
    "con", "por", "y", "o", "en", "a", "de", "se", "lo", "le", "les",
    "me", "nos", "te", "recuerdas", "recuérdame", "acuerdas", "sabes",
    "the", "is", "are", "was", "were", "of", "for", "what", "who", "how",
    "when", "where", "which", "currently", "current", "to", "about",
    "you", "your", "do", "does", "did", "have", "has", "had", "remember",
}


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def _content_words(text: str) -> List[str]:
    """Palabras significativas de un texto (sin stopwords) — usado para
    keyword search y solapamiento léxico. Preserva orden, sin duplicados."""
    words = re.findall(r"[a-záéíóúñü]+", (text or "").lower())
    seen = set()
    out = []
    for w in words:
        if len(w) > 2 and w not in _STOPWORDS and w not in seen:
            seen.add(w)
            out.append(w)
    return out


# ---------------------------------------------------------------------------
# 1. Detección de rango temporal — genérica, ES/EN, sin fechas hardcodeadas
# ---------------------------------------------------------------------------

_MONTHS_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
_MONTHS_EN = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}

_EXPLICIT_DATE_ES_RE = re.compile(
    r"\b(\d{1,2})\s+de\s+(" + "|".join(_MONTHS_ES.keys()) + r")(?:\s+de\s+(\d{4}))?\b",
    re.IGNORECASE,
)
_EXPLICIT_DATE_EN_RE = re.compile(
    r"\b(" + "|".join(_MONTHS_EN.keys()) + r")\s+(\d{1,2})(?:,?\s+(\d{4}))?\b",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_SLASH_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def _day_bounds(dt: datetime) -> Tuple[float, float]:
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1) - timedelta(microseconds=1)
    return start.timestamp(), end.timestamp()


def parse_temporal_range(text: str, *, now: Optional[float] = None) -> Optional[Tuple[float, float]]:
    """Detecta un rango temporal (since, until) mencionado genéricamente en
    `text` -- relativo ("ayer", "la semana pasada", "el mes pasado") o
    explícito ("12 de septiembre", "2026-09-12", "12/09/2026"). Devuelve
    `None` si no se detecta ningún marcador temporal (la consulta no está
    acotada por fecha)."""
    if not text:
        return None
    t = _strip_accents(text.strip().lower())
    now_dt = datetime.fromtimestamp(now if now is not None else time.time())

    # -- Relativos genéricos (ES/EN) --
    if re.search(r"\bhoy\b|\btoday\b", t):
        return _day_bounds(now_dt)
    if re.search(r"\banteayer\b|\bday before yesterday\b", t):
        return _day_bounds(now_dt - timedelta(days=2))
    if re.search(r"\bayer\b|\byesterday\b", t):
        return _day_bounds(now_dt - timedelta(days=1))
    if re.search(r"\b(esta\s+semana|this\s+week)\b", t):
        monday = now_dt - timedelta(days=now_dt.weekday())
        start, _ = _day_bounds(monday)
        return start, now_dt.timestamp()
    if re.search(r"\b(la\s+semana\s+pasada|semana\s+pasada|last\s+week)\b", t):
        this_monday = now_dt - timedelta(days=now_dt.weekday())
        last_monday = this_monday - timedelta(days=7)
        last_sunday_end = this_monday - timedelta(microseconds=1)
        start, _ = _day_bounds(last_monday)
        return start, last_sunday_end.timestamp()
    if re.search(r"\b(este\s+mes|this\s+month)\b", t):
        start_dt = now_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start_dt.timestamp(), now_dt.timestamp()
    if re.search(r"\b(el\s+mes\s+pasado|mes\s+pasado|last\s+month)\b", t):
        first_this_month = now_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month_end = first_this_month - timedelta(microseconds=1)
        last_month_start = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return last_month_start.timestamp(), last_month_end.timestamp()

    # -- Explícitos --
    m = _ISO_DATE_RE.search(t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return _day_bounds(datetime(y, mo, d))
        except ValueError:
            pass
    m = _SLASH_DATE_RE.search(t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return _day_bounds(datetime(y, mo, d))
        except ValueError:
            pass
    m = _EXPLICIT_DATE_ES_RE.search(t)
    if m:
        day = int(m.group(1))
        month = _MONTHS_ES[m.group(2)]
        year = int(m.group(3)) if m.group(3) else now_dt.year
        try:
            candidate = datetime(year, month, day)
            # Sin año explícito y la fecha cae en el futuro -> año anterior
            # (genérico: "12 de septiembre" dicho en enero se refiere al
            # septiembre YA PASADO, no a uno futuro).
            if not m.group(3) and candidate > now_dt:
                candidate = candidate.replace(year=year - 1)
            return _day_bounds(candidate)
        except ValueError:
            pass
    m = _EXPLICIT_DATE_EN_RE.search(t)
    if m:
        month = _MONTHS_EN[m.group(1)]
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else now_dt.year
        try:
            candidate = datetime(year, month, day)
            if not m.group(3) and candidate > now_dt:
                candidate = candidate.replace(year=year - 1)
            return _day_bounds(candidate)
        except ValueError:
            pass

    return None


# ---------------------------------------------------------------------------
# 2. Detección de categoría — reutiliza el vocabulario ya existente de
#    star_deriver.py (mismas categorías, sin duplicar el detector completo
#    de PARTE 2 -- aquí solo se necesita una pista de INTENCIÓN de consulta,
#    no la extracción de afirmación/entidad que hace derive_and_store_star).
# ---------------------------------------------------------------------------

_CATEGORY_HINT_PATTERNS: Tuple[Tuple[str, "re.Pattern"], ...] = (
    (
        STAR_CATEGORY_DECISION,
        re.compile(
            r"\b(decidimos|decidí|decision|decisión|acordamos|qued[aá]mos\s+en)\b"
            r"|\bwhat\s+did\s+we\s+decide\b|\bour\s+decision\b",
            re.IGNORECASE,
        ),
    ),
    (
        STAR_CATEGORY_COMMITMENT,
        re.compile(
            r"\b(compromiso|promet[ií]|me\s+comprometí)\b|\bpromise[d]?\b",
            re.IGNORECASE,
        ),
    ),
    (
        STAR_CATEGORY_GOAL,
        re.compile(
            r"\b(objetivo|meta)\b|\bgoal\b",
            re.IGNORECASE,
        ),
    ),
    (
        STAR_CATEGORY_PROJECT,
        re.compile(
            r"\bproyecto\b|\bproject\b",
            re.IGNORECASE,
        ),
    ),
    (
        STAR_CATEGORY_RELATION,
        re.compile(
            r"\b(qui[eé]n\s+es\s+mi|mi\s+(novia|novio|esposa|esposo|pareja|socia?|"
            r"jefe|jefa|amig[ao]))\b|\bwho\s+is\s+my\b",
            re.IGNORECASE,
        ),
    ),
    (
        STAR_CATEGORY_PREFERENCE,
        re.compile(
            r"\b(prefiero|preferencia|me\s+gusta)\b|\bprefer\b|\bfavorite\b",
            re.IGNORECASE,
        ),
    ),
)


def detect_category_hint(text: str) -> Optional[str]:
    """Pista de categoría (decisión/proyecto/relación/preferencia/objetivo/
    compromiso) mencionada en la consulta, o `None` si es genérica."""
    stripped = (text or "").strip()
    for category, pattern in _CATEGORY_HINT_PATTERNS:
        if pattern.search(stripped):
            return category
    return None


# ---------------------------------------------------------------------------
# 3. Resultado + traza
# ---------------------------------------------------------------------------

@dataclass
class RetrievedFragment:
    """Un fragmento de evidencia unificado, con procedencia explícita."""
    source: str            # "ledger" | "star_provenance" | "semantic_star"
    id: str
    content: str
    timestamp: float
    score: float
    category: str = ""
    status: str = ""


@dataclass
class RetrievalTrace:
    """Traza interna completa -- exigida por PARTE 3, nunca se muestra
    cruda al usuario, pero queda disponible para auditoría/tests/dashboard."""
    memory_consulted: bool = True
    events_retrieved: int = 0
    stars_used: List[str] = field(default_factory=list)
    time_range: Optional[Tuple[float, float]] = None
    category_hint: Optional[str] = None
    keywords: List[str] = field(default_factory=list)
    sources_used: List[str] = field(default_factory=list)
    scores: List[float] = field(default_factory=list)
    confidence: float = 0.0
    abstained: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_consulted": self.memory_consulted,
            "events_retrieved": self.events_retrieved,
            "stars_used": self.stars_used,
            "time_range": list(self.time_range) if self.time_range else None,
            "category_hint": self.category_hint,
            "keywords": self.keywords,
            "sources_used": self.sources_used,
            "scores": [round(s, 4) for s in self.scores],
            "confidence": round(self.confidence, 4),
            "abstained": self.abstained,
            "reason": self.reason,
        }


@dataclass
class PersonalMemoryResult:
    sovereign_answer: str = ""
    sufficient: bool = False
    fragments: List[RetrievedFragment] = field(default_factory=list)
    trace: RetrievalTrace = field(default_factory=RetrievalTrace)


# ---------------------------------------------------------------------------
# 4. Recuperación por fuente
# ---------------------------------------------------------------------------

def _retrieve_from_ledger(
    *, tenant_id: str, owner: str,
    since: Optional[float], until: Optional[float], keywords: List[str],
    limit: int = 50,
) -> List[RetrievedFragment]:
    """Evidencia PRIMARIA: mensajes literales del ledger canónico (PARTE 1),
    filtrados por rango temporal y/o keyword (se prueba cada keyword por
    separado y se fusionan resultados -- OR semántico, ya que el usuario
    puede recordar solo una palabra de una frase más larga)."""
    try:
        from core.memory.conversation_ledger import get_conversation_ledger
    except Exception as exc:
        logger.debug("conversation_ledger unavailable: %s", exc)
        return []

    repo = get_conversation_ledger()
    seen_ids: set = set()
    out: List[RetrievedFragment] = []

    keyword_list = keywords[:5] if keywords else [""]
    for kw in keyword_list:
        try:
            rows = repo.search(
                tenant_id, owner, since=since, until=until,
                keyword=kw, role="user", limit=limit,
            )
        except Exception as exc:
            logger.debug("ledger search failed (kw=%r): %s", kw, exc)
            continue
        for row in rows:
            eid = row.get("event_id", "")
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            content = row.get("content", "") or ""
            # Score: +peso por cada keyword presente + recencia leve.
            hits = sum(1 for w in keywords if w in content.lower()) if keywords else 0
            base = 0.55 + 0.1 * min(hits, 3)
            out.append(RetrievedFragment(
                source="ledger", id=eid, content=content,
                timestamp=float(row.get("timestamp", 0.0) or 0.0),
                score=min(base, 0.95),
            ))
    return out


def _retrieve_from_star_provenance(
    *, tenant_id: str, channel: str, owner: str,
    category_hint: Optional[str], keywords: List[str],
    since: Optional[float], until: Optional[float],
) -> List[RetrievedFragment]:
    """Índice estructurado (PARTE 2): busca por categoría (si se detectó
    una pista) y filtra por solapamiento de keyword contra entidad/
    afirmación normalizada. Nunca incluye registros 'superseded' (ya
    reemplazados) salvo que se pida explícitamente -- 'active' y
    'contradicted' se muestran (esto último para declarar el conflicto
    honestamente en vez de ocultarlo)."""
    try:
        from vectrax import db
    except Exception:
        return []

    try:
        rows = db.list_star_provenance(
            tenant_id=tenant_id, channel=channel, owner=owner,
            category=category_hint, status=None, limit=200,
        )
    except Exception as exc:
        logger.debug("list_star_provenance failed: %s", exc)
        return []

    out: List[RetrievedFragment] = []
    for row in rows:
        if row.get("status") == "superseded":
            continue
        entity = (row.get("entity") or "").lower()
        assertion = (row.get("normalized_assertion") or "").lower()
        ts = float(row.get("last_seen", 0.0) or 0.0)
        if since is not None and ts < since:
            continue
        if until is not None and ts > until:
            continue
        kw_hits = sum(1 for w in keywords if w in entity or w in assertion) if keywords else 0
        if keywords and kw_hits == 0 and not category_hint:
            continue  # sin pista de categoría, exige al menos 1 keyword real
        confidence = float(row.get("confidence", 0.0) or 0.0)
        score = min(0.5 + 0.15 * kw_hits + 0.2 * confidence, 0.98)
        literal_fragments = row.get("literal_fragments") or []
        content = literal_fragments[-1] if literal_fragments else assertion
        out.append(RetrievedFragment(
            source="star_provenance", id=row.get("star_id", row.get("id", "")),
            content=content, timestamp=ts, score=score,
            category=row.get("category", ""), status=row.get("status", ""),
        ))
    return out


def _retrieve_semantic(
    text: str, channel: str, owner: str, owner_raw: str,
) -> Tuple[List[RetrievedFragment], float]:
    """Componente semántico -- reutiliza `resolve_local()` TAL CUAL (nunca
    reimplementa la búsqueda por embeddings). Devuelve fragmentos + el
    top_score reportado por resolve_local, para blend de confianza."""
    try:
        from vectrax.resolver import resolve_local
    except Exception:
        return [], 0.0
    try:
        result = resolve_local(text, channel, owner, owner_raw=owner_raw)
    except Exception as exc:
        logger.debug("semantic resolve_local failed: %s", exc)
        return [], 0.0
    if not result.context_stars:
        return [], 0.0
    # resolve_local ya sintetiza una respuesta; la usamos como UN fragmento
    # (no releemos stars crudas de nuevo -- evita doble trabajo/embeds).
    frag = RetrievedFragment(
        source="semantic_star", id="resolve_local", content=result.sovereign_answer,
        timestamp=time.time(), score=min(max(result.top_score, 0.0), 1.0),
    )
    return [frag], result.top_score


# ---------------------------------------------------------------------------
# 5. Orquestador único
# ---------------------------------------------------------------------------

def retrieve_personal_memory(
    text: str,
    *,
    tenant_id: str = _DEFAULT_TENANT,
    channel: str,
    owner: str,
    owner_raw: str = "",
    execution_context: Any = None,
) -> PersonalMemoryResult:
    """Punto único de recuperación universal de memoria personal.

    Combina fecha + tema/proyecto/persona + keyword + similitud semántica +
    decisiones + orden temporal en una sola pasada, usando SIEMPRE las
    mismas 3 fuentes ya existentes (ledger, star_provenance, resolve_local)
    -- nunca fabrica evidencia ni sale a buscarla afuera. Si no hay
    evidencia real, declara abstención honesta.
    """
    trace = RetrievalTrace(memory_consulted=True)

    time_range = parse_temporal_range(text)
    category_hint = detect_category_hint(text)
    keywords = _content_words(text)

    trace.time_range = time_range
    trace.category_hint = category_hint
    trace.keywords = keywords

    since, until = (time_range if time_range else (None, None))

    ledger_fragments = _retrieve_from_ledger(
        tenant_id=tenant_id, owner=owner, since=since, until=until,
        keywords=keywords,
    )
    # PARTE 8-9 (2026-09-20) -- corrección de un bug real detectado por
    # pruebas E2E ("procedencia, no coincidencia textual"): el mensaje del
    # usuario se registra en el ledger ANTES de resolver (Regla 1 del
    # contrato de memoria, `record_user_message()` en pipeline_worker.py),
    # así que la PROPIA pregunta actual (p.ej. "¿Quién es mi novia?") ya
    # está en el ledger cuando esta función busca por keyword -- y "novia"
    # coincide textualmente con su propio contenido. Sin este filtro, una
    # pregunta puede aparecer como "evidencia" de sí misma para un usuario
    # sin memoria real alguna (falso grounded=True). Una pregunta NUNCA es
    # evidencia válida para responderse a sí misma.
    _query_norm = (text or "").strip().casefold()
    ledger_fragments = [
        f for f in ledger_fragments
        if (f.content or "").strip().casefold() != _query_norm
    ]
    provenance_fragments = _retrieve_from_star_provenance(
        tenant_id=tenant_id, channel=channel, owner=owner,
        category_hint=category_hint, keywords=keywords,
        since=since, until=until,
    )

    structured_count = len(ledger_fragments) + len(provenance_fragments)

    semantic_fragments: List[RetrievedFragment] = []
    semantic_top_score = 0.0
    # El componente semántico se activa cuando la búsqueda estructurada no
    # basta (consulta genuinamente semántica, p.ej. "qué me preocupa
    # últimamente") -- o siempre que NO se detectó ni fecha ni categoría ni
    # keywords utilizables (consulta demasiado genérica para filtrar).
    if structured_count < 2:
        semantic_fragments, semantic_top_score = _retrieve_semantic(
            text, channel, owner, owner_raw,
        )

    all_fragments = ledger_fragments + provenance_fragments + semantic_fragments

    # -- Orden temporal: más reciente primero, salvo que el usuario haya
    #    pedido explícitamente un rango pasado (en cuyo caso cronológico
    #    ascendente narra mejor "qué pasó primero, qué pasó después").
    all_fragments.sort(key=lambda f: f.timestamp, reverse=(time_range is None))

    trace.events_retrieved = len(ledger_fragments)
    trace.stars_used = [f.id for f in provenance_fragments] + [
        f.id for f in semantic_fragments if f.source == "semantic_star"
    ]
    trace.sources_used = sorted({f.source for f in all_fragments})
    trace.scores = [f.score for f in all_fragments]

    if not all_fragments:
        trace.abstained = True
        trace.confidence = 0.0
        trace.reason = (
            "sin evidencia en ledger, star_provenance ni similitud semántica "
            "para el rango/keywords/categoría detectados"
        )
        lang = _detect_lang_safe(text)
        answer = (
            "No tengo información sobre eso en mi memoria."
            if lang != "en" else
            "I don't have information about that in my memory."
        )
        return PersonalMemoryResult(
            sovereign_answer=answer, sufficient=False,
            fragments=[], trace=trace,
        )

    trace.confidence = round(
        min(1.0, (sum(trace.scores) / len(trace.scores)) if trace.scores else 0.0),
        4,
    )

    # -- Síntesis: mismo sintetizador anti-alucinación ya usado por
    #    resolve_local()/resolve_online() (mode="memory") -- nunca se
    #    reimplementa un segundo camino de síntesis.
    literal_texts = [f.content for f in all_fragments[:12] if f.content]
    lang = _detect_lang_safe(text)
    sovereign = ""
    try:
        from vectrax.resolver import _interpret_with_llm, _synthesize_local
        sovereign = _interpret_with_llm(
            text, literal_texts, lang=lang,
            execution_context=execution_context, mode="memory",
        )
        if not sovereign:
            sovereign = _synthesize_local(literal_texts, lang=lang)
    except Exception as exc:
        logger.debug("personal memory synthesis failed: %s", exc)
        sovereign = literal_texts[0] if literal_texts else ""

    return PersonalMemoryResult(
        sovereign_answer=sovereign, sufficient=True,
        fragments=all_fragments, trace=trace,
    )


def _detect_lang_safe(text: str) -> str:
    try:
        from vectrax.resolver import _detect_lang
        return _detect_lang(text)
    except Exception:
        return "es"
