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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional

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
    domain: str, ranked: List[Dict[str, Any]], focus: str = "", note: str = "",
) -> str:
    # El criterio NO es una regla preprogramada ni una elección entre opciones:
    # es la POSICIÓN que emerge de todo lo observado hasta ahora. Se lee sobre el
    # conjunto de la evidencia y se ancla en su señal dominante (grounded).
    top = ranked[0]
    rest = ranked[1:3]
    n_pat = len(ranked)
    n_obs = sum(int(e.get("sample_size", 0) or 0) for e in ranked)
    anchor = (
        f"«{top['name']}» (expectancy {top['expectancy']:+.2f}, "
        f"WR {top['win_rate']:.0f}%, LB {top['wilson_lb']:.0f}% sobre "
        f"{top['sample_size']} obs, confianza {top['confidence'] or 'n/d'}"
    )
    if top.get("hits"):
        anchor += f", masa {top['hits']} activaciones"
    anchor += ")"
    if note:
        head = (
            f"{note}de lo observado en {domain} la posición que me emerge se "
            f"apoya en {anchor}."
        )
    else:
        head = (
            f"De todo lo que he observado en {domain} {focus}— {n_pat} patrones "
            f"sobre {n_obs} observaciones — la posición que me emerge se apoya "
            f"en {anchor}."
        )
    out = [head]
    if rest:
        field = "; ".join(
            f"«{o['name']}» (E {o['expectancy']:+.2f}, WR {o['win_rate']:.0f}%)"
            for o in rest
        )
        out.append(
            f"El resto de lo observado ({field}) pesa en esa misma lectura; "
            f"no son opciones que elija por separado."
        )
    out.append(
        "No es una regla que me programaron ni una elección de menú: es lo que "
        "emerge de la evidencia acumulada, y se reconfigura conforme sigo observando."
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


# Léxico de recomendación/consejo: el criterio es una POSICIÓN, no un consejo.
# RIESGO RESIDUAL CONOCIDO: es una blocklist léxica; cubre las formas frecuentes
# pero por definición no atrapa toda paráfrasis ("lo ideal sería…", "no estaría
# de más…"). Es la PRIMERA LÍNEA de defensa, no una garantía semántica completa;
# ante cualquier duda, build_criterion_result cae a la conclusión determinista.
# `recomi?end` / `sugi?er` cubren ambas raíces (recomiend-/recomend-, sugier-/
# suger-), p. ej. "recomendable", "recomendación", "sugerencia", "sugerir".
_RECOMMENDATION_RE = re.compile(
    r"\b(deber[ií]as?|deber[ií]amos|recomi?end\w*|aconsej\w*|sugi?er\w*|"
    r"te\s+conviene|compra\w*|vend\w*|mejor\s+opci[oó]n)\b",
    re.IGNORECASE,
)

# Marcadores de POLARIDAD: el determinista es una posición de APOYO. Si el render
# introduce valencia negativa o niega el apoyo (ausente en el determinista), es un
# giro de sentido → se rechaza. Determinista y no-circular; heurística con techo
# conocido (no captura toda negación semántica), primera línea de defensa.
_NEGATIVE_VALENCE_RE = re.compile(
    r"\b(peor(?:es)?|mal[oa]s?|d[eé]bil(?:es)?|floj[oa]s?|evita\w*|descarta\w*|"
    r"rechaza\w*|desaconseja\w*|contraindicad\w*|riesgos[oa]s?|pierde\w*|perder|"
    r"negativ[oa]s?|inferior(?:es)?|desfavorable\w*)\b",
    re.IGNORECASE,
)
# Negación de un verbo de APOYO ("no se apoya", "no sostiene", "nunca respalda"…).
_NEGATED_SUPPORT_RE = re.compile(
    r"\b(?:no|nunca|jam[aá]s|tampoco)\s+(?:\w+\s+){0,2}?"
    r"(?:apoy\w*|sostien\w*|respald\w*|favorec\w*|prefier\w*|emerg\w*|sustent\w*)",
    re.IGNORECASE,
)


def _introduces_polarity_flip(rendered: str, deterministic: str) -> bool:
    """True si el render introduce negación/antónimos AUSENTES en la conclusión
    determinista (una posición de apoyo). Determinista y heurístico: cierra el gap
    de 'negación que conserva ancla y cifras', con techo conocido de blocklist.
    """
    rlow, dlow = rendered.lower(), deterministic.lower()
    for m in _NEGATIVE_VALENCE_RE.finditer(rlow):
        if m.group(0) not in dlow:            # valencia negativa introducida
            return True
    if _NEGATED_SUPPORT_RE.search(rlow) and not _NEGATED_SUPPORT_RE.search(dlow):
        return True                            # negó el apoyo ("no se apoya"…)
    return False


def _preserves_conclusion(
    rendered: str,
    deterministic: str,
    ranked: List[Dict[str, Any]],
) -> bool:
    """La presentación del LLM debe PRESERVAR la conclusión determinista, no
    reinterpretarla. Conservadora y FAIL-CLOSED: ante la duda devuelve False
    (→ conclusión determinista).

    Exige: texto no vacío; ancla (top) presente; sin lenguaje de recomendación
    (el criterio es una posición, no un consejo); y sin introducir porcentajes ni
    identificadores ausentes de la conclusión determinista.

    LIMITACIÓN: verificación LÉXICA, no semántica (no detecta una negación que
    conserve ancla y cifras). Primera línea de defensa; el determinista siempre
    es el respaldo autorizado.
    """
    if not rendered.strip():
        return False
    # Fail-closed: sin evidencia/ancla no hay conclusión que preservar → rechaza.
    if not ranked:
        return False
    anchor = (ranked[0].get("name", "") or "").lower()
    low = rendered.lower()
    if not anchor or anchor not in low:
        return False
    if _RECOMMENDATION_RE.search(rendered):
        return False
    if _introduces_polarity_flip(rendered, deterministic):
        return False
    det_pct_vals = {
        round(float(p)) for p in re.findall(r"(\d+(?:\.\d+)?)\s*%", deterministic)
    }
    for pct in re.findall(r"(\d+(?:\.\d+)?)\s*%", rendered):
        if round(float(pct)) not in det_pct_vals:
            return False
    det_ids = {m.group(0).lower() for m in _SUBJECT_RE.finditer(deterministic)}
    for m in _SUBJECT_RE.finditer(rendered):
        if m.group(0).lower() not in det_ids:
            return False
    return True


def _presentation_prompt(
    domain: str, ranked: List[Dict[str, Any]], deterministic: str,
) -> str:
    """Prompt CERRADO: el LLM solo re-redacta la conclusión ya formada. No se le
    pide criterio ni posición; recibe una decisión cerrada y una orden estricta.
    """
    return (
        "Reescribe la siguiente CONCLUSIÓN de Vectrax con naturalidad, en 1-3 "
        "líneas y en primera persona. Es una decisión YA CERRADA: NO cambies "
        "ninguna cifra ni entidad, NO interpretes, NO recalcules, NO añadas "
        "recomendaciones ni datos nuevos. Usa EXCLUSIVAMENTE las cifras de la "
        "evidencia.\n\n"
        f"{_evidence_block(domain, ranked)}\n\n"
        f"CONCLUSIÓN:\n{deterministic}"
    )


def _call_llm(
    prompt: str, llm: Optional[Callable[[str], str]] = None,
) -> tuple[bool, Optional[str], Optional["FailureReason"]]:
    """Invoca la presentación. Devuelve (attempted, text, failure_reason).

    - attempted=False: no había LLM disponible (no se intentó presentar).
    - attempted=True, text=None: se intentó pero quedó vacío ('empty') o el
      proveedor falló ('llm_error').
    - attempted=True, text=str: render en crudo (aún sin verificar).
    """
    try:
        if llm is not None:
            out = llm(prompt)
            if not (out and out.strip()):
                return True, None, "empty"
            return True, out.strip(), None
        from vectrax.intelligence_bridge import is_ready, route_single
        if not is_ready():
            return False, None, None
        res = route_single(prompt)
        if not res.get("success"):
            return True, None, "llm_error"
        content = res.get("content")
        if not (content and content.strip()):
            return True, None, "empty"
        return True, content.strip(), None
    except Exception as exc:
        logger.debug("criterion render error: %s", exc)
        return True, None, "llm_error"


# ── Contrato de resultado: formación (determinista) vs presentación (LLM) ──

CriterionOrigin = Literal[
    "deterministic",
    "llm_rendered",
    "insufficient_evidence",
]

FailureReason = Literal[
    "empty",
    "llm_error",
    "not_grounded",
    "conclusion_altered",
]


@dataclass(frozen=True)
class RenderAttempt:
    """Qué ocurrió durante el intento de PRESENTACIÓN por el LLM (sin autoridad
    decisional). `None` en una verificación significa 'no se llegó a evaluar'.
    """
    attempted: bool
    grounding_passed: Optional[bool] = None
    preservation_passed: Optional[bool] = None
    failure_reason: Optional[FailureReason] = None
    # Texto del LLM cuando fue rechazado; NUNCA se expone como `.text`.
    rejected_text: Optional[str] = None

    def __post_init__(self) -> None:
        g, p, r = self.grounding_passed, self.preservation_passed, self.failure_reason
        if not self.attempted:
            if any(x is not None for x in (g, p, r, self.rejected_text)):
                raise ValueError("attempted=False exige el resto en None")
            return
        if g is None:                      # ni siquiera se pudo evaluar grounding
            if p is not None or r not in ("empty", "llm_error"):
                raise ValueError(
                    "sin grounding: reason∈{empty,llm_error} y preservation=None"
                )
        elif g is False:                   # grounding falló → no se evalúa preservation
            if p is not None or r != "not_grounded":
                raise ValueError(
                    "grounding_passed=False exige preservation=None y reason=not_grounded"
                )
        elif p is False:                   # grounding True, preservation falló
            if r != "conclusion_altered":
                raise ValueError(
                    "preservation_passed=False exige reason=conclusion_altered"
                )
        elif p is True:                    # ambas verificaciones pasaron
            if r is not None:
                raise ValueError("ambas verificaciones True no llevan failure_reason")
        else:
            raise ValueError(
                "grounding_passed=True exige preservation True/False, no None"
            )


@dataclass(frozen=True)
class CriterionResult:
    """Resultado del Motor de Criterio con origen trazable.

    `origin` describe QUÉ texto quedó autorizado; `deterministic_conclusion`
    conserva SIEMPRE la decisión interna; `render_attempt` describe el intento
    de presentación. `text` autoriza el render SOLO cuando origin==llm_rendered.
    """
    origin: CriterionOrigin
    deterministic_conclusion: str
    rendered_text: Optional[str]
    render_attempt: RenderAttempt
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def text(self) -> str:
        if self.origin == "llm_rendered":
            return self.rendered_text  # invariante garantiza no-None/no-vacío
        return self.deterministic_conclusion

    def __post_init__(self) -> None:
        if not self.deterministic_conclusion.strip():
            raise ValueError("deterministic_conclusion debe estar siempre presente")
        ra = self.render_attempt
        if self.origin == "llm_rendered":
            if not (self.rendered_text and self.rendered_text.strip()):
                raise ValueError("llm_rendered requiere rendered_text no vacío")
            if not (ra.attempted and ra.grounding_passed is True
                    and ra.preservation_passed is True and ra.failure_reason is None):
                raise ValueError("llm_rendered requiere ambas verificaciones en True")
            return
        # deterministic | insufficient_evidence: el texto autorizado NUNCA es el render
        if self.rendered_text is not None:
            raise ValueError(f"{self.origin} no debe contener rendered_text")
        if self.origin == "insufficient_evidence":
            if ra.attempted:
                raise ValueError("insufficient_evidence no representa un intento de render")
            return
        # origin == "deterministic"
        if ra.attempted and ra.failure_reason is None:
            raise ValueError("deterministic tras un intento exige failure_reason")


def build_criterion_result(
    domain: str,
    query: str = "",
    llm: Optional[Callable[[str], str]] = None,
) -> CriterionResult:
    """Criterio de Vectrax sobre `domain` con origen trazable.

    A) FORMACIÓN (determinista, desde evidencia): SIEMPRE ocurre y ANTES del LLM;
       es la conclusión cerrada y el texto por defecto.
    B) PRESENTACIÓN (LLM, opcional, sin autoridad): solo re-redacta la conclusión
       cerrada. Se acepta únicamente si pasa grounding Y preservación; en cualquier
       otro caso (ausente, vacío, error, no-grounded, alterado) manda el determinista.
    """
    ranked = rank_domain_evidence(domain)
    if not ranked:
        # Ausencia TOTAL de evidencia: no hay criterio que formar. Honesto, sin fabricar.
        return CriterionResult(
            origin="insufficient_evidence",
            deterministic_conclusion=(
                f"Todavía no tengo datos observados en {domain}, así que aún no puedo "
                f"formar un criterio propio aquí. En cuanto acumule evidencia te digo "
                f"lo que pienso."
            ),
            rendered_text=None,
            render_attempt=RenderAttempt(attempted=False),
        )

    # Entender el TEMA concreto y quedarnos con la experiencia RELACIONADA.
    topic = extract_topic_tokens(query)
    focus = ""
    note = ""
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
            # Sin experiencia DIRECTA sobre el tema: NO me callo. Doy mi posición
            # con lo que SÍ he aprendido del dominio, marcando la transparencia.
            related = ranked
            note = (
                f"Sobre «{label}» todavía no tengo evidencia directa, pero con lo "
                f"que he aprendido en {domain} te digo lo que pienso: "
            )

    # A) FORMACIÓN — conclusión cerrada, determinista, SIEMPRE y ANTES del LLM.
    deterministic = _deterministic_opinion(domain, related, focus, note=note)

    # B) PRESENTACIÓN — el LLM solo re-redacta la conclusión cerrada.
    attempted, rendered_raw, empty_reason = _call_llm(
        _presentation_prompt(domain, related, deterministic), llm
    )
    if not attempted:
        return CriterionResult(
            "deterministic", deterministic, None, RenderAttempt(attempted=False),
        )
    if rendered_raw is None:
        return CriterionResult(
            "deterministic", deterministic, None,
            RenderAttempt(True, failure_reason=empty_reason),
        )
    if not _verify_grounded(rendered_raw, related, domain):
        return CriterionResult(
            "deterministic", deterministic, None,
            RenderAttempt(True, grounding_passed=False,
                          failure_reason="not_grounded", rejected_text=rendered_raw),
        )
    if not _preserves_conclusion(rendered_raw, deterministic, related):
        return CriterionResult(
            "deterministic", deterministic, None,
            RenderAttempt(True, grounding_passed=True, preservation_passed=False,
                          failure_reason="conclusion_altered", rejected_text=rendered_raw),
        )
    return CriterionResult(
        "llm_rendered", deterministic, rendered_raw,
        RenderAttempt(True, grounding_passed=True, preservation_passed=True),
    )


def build_criterion(
    domain: str,
    query: str = "",
    llm: Optional[Callable[[str], str]] = None,
) -> str:
    """Compat: texto autorizado del criterio (str).

    Se mantiene la firma `-> str` (consumida por external_gateway y tests). La
    estructura completa con origen trazable está en `build_criterion_result`.
    """
    return build_criterion_result(domain, query, llm=llm).text
