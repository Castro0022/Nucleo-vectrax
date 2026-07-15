"""
core/learn/criterion.py — Motor de Criterio Aprendido (cross-dominio).

Vectrax forma y EXPRESA un criterio/opinión propio sobre CUALQUIER dominio de su
universo, a partir de su aprendizaje persistido (métricas reales) — no de
conocimiento general ni respuestas fijas, y sin fabricar.

Fuentes de evidencia por dominio (solo lectura):
  • core.domain_knowledge.get_domain_priors(domain)
        → win_rate, expectancy, sample_size, confidence, contributing_tenants
  • core.learn.gravity_engine.GravityIndex().by_domain(domain)
        → hits, cc_score, freq, tier

El criterio se puntúa de forma determinista desde esas métricas y luego se
FRASEA (opcional) con un LLM restringido EXCLUSIVAMENTE a la evidencia rankeada,
con un verificador que exige que toda entidad/porcentaje citado exista en la
evidencia; si falla, cae al texto determinista. Nunca inventa.

NO toca observación / ingesta / aprendizaje / maduración / thresholds ni datos.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import math
import re
import unicodedata
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("vectrax.criterion")

# Dominios de ruido operativo (no son dominios de conocimiento para opinar).
_EXCLUDE_DOMAINS = {"unknown", "tests", "user_interest"}

# Vocabulario → dominio (extensible). Además del vocabulario, la clave del
# dominio se matchea como substring (así "freight_logistics"/"market" cuentan).
_DOMAIN_VOCAB: Dict[str, tuple] = {
    "market": (
        "bolsa", "accion", "acción", "acciones", "mercado", "market", "ticker",
        "cripto", "crypto", "trading", "stock", "stocks", "nasdaq",
        "btc", "eth", "aapl", "tsla", "nvda", "msft", "spy", "qqq",
    ),
    "freight_logistics": (
        "freight", "flete", "fletes", "carga", "cargas", "logistica", "logística",
        "logistics", "load", "loads", "ruta", "rutas", "route", "routes",
        "camion", "camión", "camiones", "carrier", "transportista", "lane",
        "shipment", "envio", "envío",
    ),
}

_CRITERION_RE = re.compile(
    r"(?:"
    r"qu[eé]\s+opinas|opini[oó]n|opina[sr]?|"
    r"qu[eé]\s+(?:es|ser[ií]a)\s+mejor|cu[aá]l\s+(?:es\s+)?(?:mejor|preferir[ií]as|elegir[ií]as|recomiendas)|"
    r"prefieres|preferir[ií]as|recomiendas|recomendaci[oó]n|recomi[eé]ndame|"
    r"tu\s+criterio|seg[uú]n\s+(?:lo\s+)?(?:aprendido|observado|tu\s+experiencia)|"
    r"qu[eé]\s+aprendiste|qu[eé]\s+(?:ves|piensas|crees)|"
    r"compara[rs]?|comparaci[oó]n|"
    r"por\s+qu[eé]\s+.+\bmejor\b|mejor\s+opci[oó]n|qu[eé]\s+elegir[ií]as|"
    r"vale\s+la\s+pena|deber[ií]a"
    r")",
    re.IGNORECASE,
)

_SUBJECT_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9]+")
_CONF_W = {"HIGH": 1.0, "MEDIUM": 0.7, "LOW": 0.4}

# Palabras a descartar al extraer el TEMA concreto (stopwords + verbos de opinión).
_STOPWORDS = set((
    "a al algo ante bajo con contra de del desde durante en entre hacia hasta la "
    "las le les lo los me mi mis para por que se sin so su sus te ti tu tus un una "
    "uno unos unas y ya el es son ser era como cual cuales cuando donde porque "
    "tiene tienen hay dime opinas opinion opina opinar mejor peor prefieres "
    "preferirias recomiendas recomendacion criterio segun aprendido observado "
    "piensas crees compara comparar comparacion elegirias vale pena deberia "
    "sobre mas muy eso esto esa ese aquello quiero ver puede pueda "
    "the of to for and or is are what which why how do you think best your"
).split())

# Indicadores de DOMINIO (no son el tema concreto de la pregunta).
_DOMAIN_INDICATORS = {
    "bolsa", "mercado", "market", "logistica", "logística", "logistics",
    "freight", "dominio", "universo", "acciones", "trading",
}

# Sinónimos tópicos ES → tokens del esquema aprendido (relaciona idioma↔entidad).
_TOPIC_SYNONYMS = {
    "carga": ("load", "booking"), "cargas": ("load", "booking"),
    "cargamento": ("load", "booking"),
    "entrega": ("delivery",), "entregas": ("delivery",),
    "ruta": ("lane", "route"), "rutas": ("lane", "route"),
    "flete": ("rate",), "fletes": ("rate",), "tarifa": ("rate",),
    "cotizacion": ("quote",), "cotización": ("quote",),
    "capacidad": ("capacity",), "retraso": ("delay",), "demora": ("delay",),
    "vacio": ("empty", "miles"), "vacío": ("empty", "miles"),
}


def _strip_accents(s: str) -> str:
    """Quita acentos/diacríticos para comparar contra stopwords no acentuadas."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def extract_topic_tokens(text: str) -> List[str]:
    """Tokens del TEMA concreto de la pregunta (sin stopwords ni indicadores de
    dominio). Conserva tickers/identificadores; expande sinónimos ES→esquema."""
    if not text:
        return []
    out: List[str] = []
    seen = set()
    for raw in re.findall(r"\w+", text, re.UNICODE):
        if raw.isdigit():
            continue
        tl = _strip_accents(raw.lower())
        if tl in _STOPWORDS or tl in _DOMAIN_INDICATORS:
            continue
        if len(tl) < 3 and not raw.isupper():
            continue
        if tl not in seen:
            seen.add(tl)
            out.append(tl)
        for syn in _TOPIC_SYNONYMS.get(tl, ()):
            if syn not in seen:
                seen.add(syn)
                out.append(syn)
    return out


def _topic_relatedness(entity: Dict[str, Any], topic_tokens: List[str]) -> int:
    """Cuánta experiencia de `entity` se relaciona (léxico) con el tema."""
    if not topic_tokens:
        return 0
    name = (entity.get("name", "") or "").lower()
    name_toks = {t for t in re.split(r"[^\w]+", name, flags=re.UNICODE) if t}
    score = 0
    for t in topic_tokens:
        if t in name_toks:
            score += 3
        elif len(t) >= 4 and t in name:
            score += 1
    return score


# ── Detección ─────────────────────────────────────────────────────────

def detect_criterion_request(text: str) -> bool:
    """True si el usuario pide una opinión/criterio/comparación."""
    if not text:
        return False
    return bool(_CRITERION_RE.search(text))


def known_domains() -> List[str]:
    """Dominios reales con aprendizaje (gravity + domain_library), sin ruido."""
    doms = set()
    try:
        from core.learn.gravity_engine import get_gravity_index
        for d in get_gravity_index().domain_stats().keys():
            if d and d not in _EXCLUDE_DOMAINS:
                doms.add(d)
    except Exception as exc:
        logger.debug("known_domains gravity error: %s", exc)
    try:
        from core.domain_knowledge import list_domains
        for d in list_domains():
            if d and d not in _EXCLUDE_DOMAINS:
                doms.add(d)
    except Exception as exc:
        logger.debug("known_domains library error: %s", exc)
    return sorted(doms)


def detect_domain(text: str) -> Optional[str]:
    """Mapea el texto a un dominio conocido por clave o vocabulario."""
    if not text:
        return None
    low = text.lower()
    scored: List[tuple] = []
    for dom in known_domains():
        score = 0
        if dom.lower() in low:
            score += 5
        for term in _DOMAIN_VOCAB.get(dom, ()):
            if re.search(r"\b" + re.escape(term.lower()) + r"\b", low):
                score += 1
        if score:
            scored.append((score, dom))
    if not scored:
        return None
    scored.sort(reverse=True)
    return scored[0][1]


def strongest_domain() -> Optional[str]:
    """Dominio con más aprendizaje acumulado (observaciones/patrones/hits)."""
    best = None
    best_score = -1
    try:
        from core.domain_knowledge import list_domains, get_domain_summary
        for d in list_domains():
            if d in _EXCLUDE_DOMAINS:
                continue
            sc = int((get_domain_summary(d) or {}).get("total_observations", 0))
            if sc > best_score:
                best, best_score = d, sc
    except Exception as exc:
        logger.debug("strongest_domain library error: %s", exc)
    if best:
        return best
    try:
        from core.learn.gravity_engine import get_gravity_index
        for d, v in get_gravity_index().domain_stats().items():
            if d in _EXCLUDE_DOMAINS:
                continue
            sc = int(v.get("total_hits", 0))
            if sc > best_score:
                best, best_score = d, sc
    except Exception as exc:
        logger.debug("strongest_domain gravity error: %s", exc)
    return best


# ── Ranking (criterio desde métricas reales) ──────────────────────────

def _wilson_lb(k: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0.0, c - m) * 100, 1)


def rank_domain_evidence(domain: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Rankea los patrones aprendidos del dominio por un score derivado de sus
    métricas reales. Read-only. Devuelve entidades con métricas + score + fuentes.
    """
    entities: Dict[str, Dict[str, Any]] = {}

    # 1) domain_library priors (WR / E / N / confianza)
    try:
        from core.domain_knowledge import get_domain_priors
        for p in get_domain_priors(domain):
            name = (getattr(p, "pattern_type", "") or "").strip()
            if not name:
                continue
            wr = float(getattr(p, "win_rate", 0.0) or 0.0)
            n = int(getattr(p, "sample_size", 0) or 0)
            e = float(getattr(p, "expectancy", 0.0) or 0.0)
            conf = (getattr(p, "confidence", "") or "").upper()
            ent = entities.setdefault(name.lower(), {
                "name": name, "domain": domain, "sources": [],
                "win_rate": round(wr, 1), "expectancy": round(e, 3),
                "sample_size": n, "confidence": conf,
                "wilson_lb": _wilson_lb(round(wr / 100.0 * n), n),
                "hits": 0, "tier": "",
            })
            if "domain_library" not in ent["sources"]:
                ent["sources"].append("domain_library")
    except Exception as exc:
        logger.debug("rank priors error: %s", exc)

    # 2) gravity by_domain (hits / tier) — enriquece
    try:
        from core.learn.gravity_engine import get_gravity_index
        for r in get_gravity_index().by_domain(domain):
            intent = (getattr(r, "intent", "") or "").strip()
            key = intent.lower()
            if not key:
                continue
            hits = int(getattr(r, "hits", 0) or 0)
            ent = entities.get(key)
            if ent is None:
                ent = entities.setdefault(key, {
                    "name": intent, "domain": domain, "sources": [],
                    "win_rate": round(float(getattr(r, "cc_score", 0.0) or 0.0) * 100, 1),
                    "expectancy": 0.0, "sample_size": hits, "confidence": "",
                    "wilson_lb": 0.0, "hits": hits, "tier": getattr(r, "tier", "") or "",
                })
            ent["hits"] = max(ent.get("hits", 0), hits)
            ent["tier"] = ent.get("tier") or (getattr(r, "tier", "") or "")
            if "gravity_index" not in ent["sources"]:
                ent["sources"].append("gravity_index")
    except Exception as exc:
        logger.debug("rank gravity error: %s", exc)

    ranked = list(entities.values())
    for ent in ranked:
        conf_w = _CONF_W.get(ent.get("confidence", ""), 0.5)
        lb_frac = max((ent.get("wilson_lb", 0.0) or 0.0) / 100.0, 0.01)
        mass_w = 1.0 + math.log10(1 + max(ent.get("hits", 0), 0)) / 10.0
        ent["score"] = round(ent.get("expectancy", 0.0) * lb_frac * conf_w * mass_w, 4)
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked[:limit]


# ── Opinión: determinista (grounded) + LLM restringido (verificado) ───

def _deterministic_opinion(
    domain: str, ranked: List[Dict[str, Any]], focus: str = "",
) -> str:
    top = ranked[0]
    alts = ranked[1:3]
    out = [
        f"Por lo que he observado en {domain} {focus}mi criterio: me inclino por "
        f"«{top['name']}»."
    ]
    reason = (
        f"Razones (evidencia aprendida): expectancy {top['expectancy']:+.2f}, "
        f"WR {top['win_rate']:.0f}% (LB {top['wilson_lb']:.0f}% sobre "
        f"{top['sample_size']} obs), confianza {top['confidence'] or 'n/d'}"
    )
    if top.get("hits"):
        reason += f", masa {top['hits']} activaciones"
    out.append(reason + ".")
    if alts:
        comp = "; ".join(
            f"«{a['name']}» (E {a['expectancy']:+.2f}, WR {a['win_rate']:.0f}%)"
            for a in alts
        )
        out.append(f"Lo prefiero sobre {comp}.")
    out.append(
        "Es mi lectura desde lo aprendido, no una regla fija: cambia si cambia la evidencia."
    )
    return " ".join(out)


def _evidence_block(domain: str, ranked: List[Dict[str, Any]]) -> str:
    lines = [f"EVIDENCIA APRENDIDA — dominio {domain} (única fuente permitida):"]
    for e in ranked:
        lines.append(
            f"- {e['name']}: expectancy={e['expectancy']:+.3f} WR={e['win_rate']:.0f}% "
            f"wilson_lb={e['wilson_lb']:.0f}% N={e['sample_size']} "
            f"confianza={e['confidence'] or 'n/d'} hits={e.get('hits', 0)} "
            f"score={e['score']:.4f}"
        )
    return "\n".join(lines)


def _verify_grounded(text: str, ranked: List[Dict[str, Any]], domain: str) -> bool:
    """El texto solo puede citar entidades/porcentajes presentes en la evidencia."""
    allowed = {e["name"].lower() for e in ranked} | {domain.lower()}
    # (a) ninguna entidad identificador (tipo route_A/load_booking) fuera de evidencia
    for m in _SUBJECT_RE.finditer(text):
        if m.group(0).lower() not in allowed:
            return False
    # (b) los porcentajes citados deben coincidir (±2) con WR/wilson_lb aprendidos
    ev_pcts = set()
    for e in ranked:
        ev_pcts.add(round(e.get("win_rate", 0) or 0))
        ev_pcts.add(round(e.get("wilson_lb", 0) or 0))
    for pct in re.findall(r"(\d+(?:\.\d+)?)\s*%", text):
        val = round(float(pct))
        if not any(abs(val - ev) <= 2 for ev in ev_pcts):
            return False
    return True


def _phrase_with_llm(
    domain: str,
    ranked: List[Dict[str, Any]],
    query: str,
    llm: Optional[Callable[[str], str]] = None,
) -> Optional[str]:
    """Frasea el criterio en primera persona usando SOLO la evidencia rankeada.

    `llm` es una función inyectable (prompt -> texto) para tests. En producción,
    si no se inyecta, usa el Intelligence Bridge si está listo. Devuelve None si
    no hay LLM disponible.
    """
    prompt = (
        "Eres Vectrax. Expresa TU criterio en primera persona respondiendo a la "
        "pregunta, usando EXCLUSIVAMENTE la evidencia de abajo. NO inventes "
        "entidades, rutas ni números fuera de la evidencia. Cita las métricas que "
        "sustentan tu preferencia. Si te preguntan por algo que no está en la "
        "evidencia, dilo y da tu criterio sobre lo que sí observaste.\n\n"
        f"{_evidence_block(domain, ranked)}\n\n"
        f"PREGUNTA: {query}\n\nMI CRITERIO:"
    )
    try:
        if llm is not None:
            out = llm(prompt)
            return out.strip() if out else None
        from vectrax.intelligence_bridge import is_ready, route_single
        if is_ready():
            res = route_single(prompt)
            if res.get("success") and res.get("content"):
                return res["content"].strip()
    except Exception as exc:
        logger.debug("_phrase_with_llm error: %s", exc)
    return None


def build_criterion(
    domain: str,
    query: str = "",
    llm: Optional[Callable[[str], str]] = None,
) -> str:
    """Criterio/opinión de Vectrax sobre `domain`, grounded en lo aprendido.

    - Si no hay evidencia suficiente → lo dice (sin inventar).
    - Si el query nombra entidades ausentes de la evidencia → lo reconoce y AUN
      ASÍ da su criterio sobre lo observado (abstención constructiva).
    - Frasea con LLM restringido a la evidencia si está disponible y pasa el
      verificador; si no, texto determinista. Siempre grounded, nunca fijo/vacío.
    """
    ranked = rank_domain_evidence(domain)
    if not ranked:
        return (
            f"Todavía no tengo aprendizaje suficiente en el dominio {domain} "
            f"para formar un criterio. No voy a opinar sin evidencia observada."
        )

    # Entender el TEMA concreto y quedarnos con la experiencia RELACIONADA.
    topic = extract_topic_tokens(query)
    focus = ""
    related = ranked
    if topic:
        scored = sorted(
            ((_topic_relatedness(e, topic), e) for e in ranked),
            key=lambda x: -x[0],
        )
        rel = [e for s, e in scored if s > 0]
        label = " ".join(topic[:3])
        if rel:
            related = rel
            focus = f"sobre «{label}» "
        else:
            # Tema sin experiencia relacionada: no opino sobre eso; ofrezco lo
            # más cercano observado (sin fabricar).
            top = ranked[0]
            return (
                f"No tengo experiencia relacionada con «{label}» en {domain}, "
                f"así que no opino sobre eso. Lo más cercano que he observado es "
                f"«{top['name']}» (expectancy {top['expectancy']:+.2f}, WR "
                f"{top['win_rate']:.0f}% sobre {top['sample_size']} obs), pero no "
                f"es directamente «{label}»."
            )

    phrased = _phrase_with_llm(domain, related, query, llm=llm)
    if phrased and _verify_grounded(phrased, related, domain):
        return phrased
    return _deterministic_opinion(domain, related, focus)
