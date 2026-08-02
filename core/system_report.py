"""
core/system_report.py — Reporte de estado global de Vectrax (determinista).

Capa de PRESENTACIÓN/AGREGACIÓN read-only: compone en un solo lugar las piezas
que ya existen por separado y las devuelve como un snapshot estructurado
(`get_global_state`) y como un texto compacto y determinista
(`build_global_report`) apto para Telegram (HTML).

Fuentes (todas read-only, cada una en su propio try/except — si una falla, las
demás siguen; NUNCA lanza):
  • core.universe_census.get_census()            → estrellas, convergencias
                                                    cross-domain, dominios, masa
  • core.orchestration.get_engine_status()       → motores (total/disponibles/tier)
  • core.learn.gravity_engine.growth_trends()    → crecimiento (7d)
  • core.learn.convergence_history               → convergencias persistidas
  • core.domain_knowledge.list_domains()         → dominios de la librería de criterio

NO calcula nada nuevo ni persiste: reutiliza el SSOT (censo) y los motores ya
construidos. `scope="public"` omite conteos privados (usuarios/interacciones/
hechos/equipos) para respetar la política de no volcar métricas sensibles.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("vectrax.system_report")


# ---------------------------------------------------------------------------
# Detección estricta de petición de estado global
# ---------------------------------------------------------------------------
# Solo se usa para el acceso no-creador (gated por VECTRAX_PUBLIC_UNIVERSE_STATUS,
# default OFF). Debe ser estricta para NO secuestrar conversación normal.
_STATUS_RE = re.compile(
    r"(?:"
    r"estado\s+(?:global|del\s+sistema|del\s+universo|general)"
    r"|reporte\s+(?:del\s+)?(?:sistema|universo|global)"
    r"|resumen\s+(?:del\s+)?(?:sistema|universo|global)"
    r"|c[oó]mo\s+(?:est[aá]|va)\s+(?:el|tu)\s+(?:sistema|universo)"
    r"|c[uú][aá]nt[oa]s?\s+(?:estrellas|convergencias|motores|dominios)"
    r"|crecimiento\s+(?:del\s+)?(?:sistema|universo)"
    r"|system\s+status|global\s+status|universe\s+status"
    r"|how\s+many\s+(?:stars|convergences|engines|domains)"
    r")",
    re.IGNORECASE,
)


def is_global_status_request(text: str) -> bool:
    """True si el usuario pide EXPLÍCITAMENTE el estado global del sistema."""
    if not text:
        return False
    return bool(_STATUS_RE.search(text))


# ---------------------------------------------------------------------------
# Snapshot estructurado (agregación read-only)
# ---------------------------------------------------------------------------

def get_global_state() -> Dict[str, Any]:
    """Compone el estado global desde las fuentes existentes.

    Devuelve un dict con secciones: stars, convergences, engines, domains,
    growth, extra y private. Cada fuente es defensiva: si falla, esa sección
    queda con defaults y las demás siguen. Nunca lanza.
    """
    state: Dict[str, Any] = {"timestamp": time.time()}

    stars = {"total": 0, "gravitational": 0, "knowledge": 0, "users": 0}
    domains_gravity: Dict[str, int] = {}
    convergences: Dict[str, Any] = {
        "cross_domain": 0,
        "history": {"births": 0, "dissolutions": 0, "active": 0},
    }
    extra = {"patterns": 0, "constellations": 0, "mass_total": 0.0, "word_gravity_count": 0}
    private = {"users_total": 0, "interactions": 0, "user_facts": 0, "teams": 0}

    # 1) Censo — SSOT de conteos
    try:
        from core.universe_census import get_census
        c = get_census()
        stars = {
            "total": c.total,
            "gravitational": c.gravitational,
            "knowledge": c.knowledge,
            "users": c.users,
        }
        domains_gravity = dict(c.domains or {})
        convergences["cross_domain"] = c.convergences
        extra = {
            "patterns": c.patterns,
            "constellations": c.constellations,
            "mass_total": round(c.mass_total, 4),
            "word_gravity_count": c.word_gravity_count,
        }
        private = {
            "users_total": c.users_total,
            "interactions": c.interactions,
            "user_facts": c.user_facts,
            "teams": c.teams,
        }
    except Exception as exc:
        logger.debug("global_state census failed: %s", exc)

    # 2) Convergencias persistidas (historial de nacimientos/disoluciones)
    try:
        from core.learn.convergence_history import count_events, get_active
        ev = count_events() or {}
        convergences["history"] = {
            "births": int(ev.get("birth", 0)),
            "dissolutions": int(ev.get("dissolution", 0)),
            "active": len(get_active()),
        }
    except Exception as exc:
        logger.debug("global_state convergence_history failed: %s", exc)

    # 3) Motores (capa de orquestación)
    engines: Dict[str, Any] = {
        "total": 0, "available": 0, "by_tier": {}, "gated": [], "external": [],
    }
    try:
        from core.orchestration import get_engine_status
        # Asegura el registro poblado aunque el bootstrap no haya corrido en este
        # proceso (p.ej. el gateway de Telegram). Idempotente.
        try:
            from core.orchestration import engine_registry as _reg
            _reg._register_defaults()
        except Exception:
            pass
        st = get_engine_status()
        engines["total"] = st.get("total", 0)
        engines["available"] = st.get("available", 0)
        by_tier: Dict[str, int] = {}
        for e in st.get("engines", []):
            t = e.get("tier", "?")
            by_tier[t] = by_tier.get(t, 0) + 1
            if t == "gated_internal":
                engines["gated"].append(e.get("name", "?"))
            elif t == "external":
                engines["external"].append(e.get("name", "?"))
        engines["by_tier"] = by_tier
    except Exception as exc:
        logger.debug("global_state engines failed: %s", exc)

    # 4) Dominios (gravitacionales del censo + librería de criterio)
    domains: Dict[str, Any] = {"gravity": domains_gravity, "criterion_library": []}
    try:
        from core.domain_knowledge import list_domains
        domains["criterion_library"] = list_domains()
    except Exception as exc:
        logger.debug("global_state domains failed: %s", exc)

    # 5) Crecimiento (ventana 7d)
    growth = {
        "window_days": 7, "new_stars": 0, "active_stars": 0,
        "growing_stars": 0, "new_by_domain": {},
    }
    try:
        from core.learn.gravity_engine import get_gravity_index
        gt = get_gravity_index().growth_trends(days=7) or {}
        growth = {
            "window_days": gt.get("window_days", 7),
            "new_stars": gt.get("new_stars", 0),
            "active_stars": gt.get("active_stars", 0),
            "growing_stars": gt.get("growing_stars", 0),
            "new_by_domain": gt.get("new_by_domain", {}) or {},
        }
    except Exception as exc:
        logger.debug("global_state growth failed: %s", exc)

    state["stars"] = stars
    state["convergences"] = convergences
    state["engines"] = engines
    state["domains"] = domains
    state["growth"] = growth
    state["extra"] = extra
    state["private"] = private
    return state


# ---------------------------------------------------------------------------
# Render determinista (texto compacto, HTML-friendly para Telegram)
# ---------------------------------------------------------------------------

def build_global_report(
    lang: str = "es",
    scope: str = "full",
    state: Optional[Dict[str, Any]] = None,
) -> str:
    """Texto determinista del estado global.

    scope="full"   → incluye conteos privados (usuarios/interacciones/…).
    scope="public" → omite conteos privados; deja la vista del universo.

    `state` puede inyectarse (tests); si no, se calcula con get_global_state().
    """
    if state is None:
        state = get_global_state()
    if lang not in ("es", "en"):
        lang = "es"
    public = scope == "public"

    stars = state.get("stars", {}) or {}
    conv = state.get("convergences", {}) or {}
    hist = conv.get("history", {}) or {}
    eng = state.get("engines", {}) or {}
    dom = state.get("domains", {}) or {}
    grav_dom = dom.get("gravity", {}) or {}
    lib_dom = dom.get("criterion_library", []) or []
    growth = state.get("growth", {}) or {}
    extra = state.get("extra", {}) or {}
    priv = state.get("private", {}) or {}

    dom_parts = [
        f"{d}={n}" for d, n in sorted(grav_dom.items(), key=lambda x: -x[1]) if n > 0
    ][:8]
    by_tier = eng.get("by_tier", {}) or {}
    tier_str = ", ".join(f"{k}={v}" for k, v in sorted(by_tier.items())) or "n/d"

    # Antigüedad por dominio (desde cuándo) — reutiliza trend_reader (defensivo,
    # solo gravity; no toca convergencias ni verification para no encarecer).
    age_line = ""
    try:
        from core.trend_reader import domain_observing_since_days
        _noise = {"unknown", "tests", "user_interest"}
        _age = []
        for _d in [k for k, _v in sorted(grav_dom.items(), key=lambda kv: -(kv[1] or 0))
                   if k not in _noise][:4]:
            _s = domain_observing_since_days(_d)
            if _s is not None:
                _age.append(f"{_d} {int(round(_s))}d")
        if _age:
            _lbl = "🕒 <b>Antigüedad:</b> " if lang == "es" else "🕒 <b>Age:</b> "
            age_line = _lbl + " · ".join(_age)
    except Exception as _age_exc:
        logger.debug("global report age failed: %s", _age_exc)

    if lang == "es":
        lines = [
            "🌌 <b>VECTRAX — Estado global</b>",
            "",
            (
                f"⭐ <b>Estrellas:</b> {stars.get('total', 0)} "
                f"(grav {stars.get('gravitational', 0)} · "
                f"conocimiento {stars.get('knowledge', 0)} · "
                f"usuarios {stars.get('users', 0)})"
            ),
            (
                f"🌀 <b>Convergencias:</b> {conv.get('cross_domain', 0)} cross-domain · "
                f"activas {hist.get('active', 0)} "
                f"(nac. {hist.get('births', 0)} / disol. {hist.get('dissolutions', 0)})"
            ),
            (
                f"🧭 <b>Dominios:</b> {len(grav_dom)} — "
                f"{', '.join(dom_parts) if dom_parts else 'n/d'}"
                + (f" · librería criterio: {len(lib_dom)}" if lib_dom else "")
            ),
            (
                f"⚙️ <b>Motores:</b> {eng.get('available', 0)}/{eng.get('total', 0)} "
                f"disponibles ({tier_str})"
            ),
            (
                f"📈 <b>Crecimiento ({growth.get('window_days', 7)}d):</b> "
                f"+{growth.get('new_stars', 0)} nuevas · "
                f"{growth.get('active_stars', 0)} activas · "
                f"{growth.get('growing_stars', 0)} creciendo"
            ),
            (
                f"🧱 Patrones {extra.get('patterns', 0)} · "
                f"constelaciones {extra.get('constellations', 0)} · "
                f"masa {extra.get('mass_total', 0.0)} · "
                f"palabras {extra.get('word_gravity_count', 0)}"
            ),
        ]
        if age_line:
            lines.append(age_line)
        if not public:
            lines += [
                "",
                (
                    f"👥 <b>Usuarios:</b> {priv.get('users_total', 0)} · "
                    f"interacciones {priv.get('interactions', 0)} · "
                    f"hechos {priv.get('user_facts', 0)} · "
                    f"equipos {priv.get('teams', 0)}"
                ),
            ]
        return "\n".join(lines)

    # English
    lines = [
        "🌌 <b>VECTRAX — Global state</b>",
        "",
        (
            f"⭐ <b>Stars:</b> {stars.get('total', 0)} "
            f"(grav {stars.get('gravitational', 0)} · "
            f"knowledge {stars.get('knowledge', 0)} · "
            f"users {stars.get('users', 0)})"
        ),
        (
            f"🌀 <b>Convergences:</b> {conv.get('cross_domain', 0)} cross-domain · "
            f"active {hist.get('active', 0)} "
            f"(births {hist.get('births', 0)} / dissol. {hist.get('dissolutions', 0)})"
        ),
        (
            f"🧭 <b>Domains:</b> {len(grav_dom)} — "
            f"{', '.join(dom_parts) if dom_parts else 'n/a'}"
            + (f" · criterion library: {len(lib_dom)}" if lib_dom else "")
        ),
        (
            f"⚙️ <b>Engines:</b> {eng.get('available', 0)}/{eng.get('total', 0)} "
            f"available ({tier_str})"
        ),
        (
            f"📈 <b>Growth ({growth.get('window_days', 7)}d):</b> "
            f"+{growth.get('new_stars', 0)} new · "
            f"{growth.get('active_stars', 0)} active · "
            f"{growth.get('growing_stars', 0)} growing"
        ),
        (
            f"🧱 Patterns {extra.get('patterns', 0)} · "
            f"constellations {extra.get('constellations', 0)} · "
            f"mass {extra.get('mass_total', 0.0)} · "
            f"words {extra.get('word_gravity_count', 0)}"
        ),
    ]
    if age_line:
        lines.append(age_line)
    if not public:
        lines += [
            "",
            (
                f"👥 <b>Users:</b> {priv.get('users_total', 0)} · "
                f"interactions {priv.get('interactions', 0)} · "
                f"facts {priv.get('user_facts', 0)} · "
                f"teams {priv.get('teams', 0)}"
            ),
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Narrativa por-dominio (grounded) para la vía de lenguaje natural
# ---------------------------------------------------------------------------

def build_domain_observations(
    max_domains: int = 4,
    per_domain: int = 2,
    lang: str = "es",
) -> str:
    """Digest grounded por dominio: MATERIAL real para que Vectrax comente de
    forma casual QUÉ está observando en cada dominio (NO es un reporte formal;
    es insumo para el prompt de auto-referencia).

    Por cada dominio con más masa (gravity ``domain_stats``, saltando ruido):
    conteo de estrellas, activaciones totales, los intents más activos
    (``top_stars``) y cuántas estrellas nuevas aparecieron esta semana
    (``growth_trends``). Read-only y defensivo: nunca lanza.
    """
    if lang not in ("es", "en"):
        lang = "es"
    _skip = {"unknown", "tests", "user_interest"}
    try:
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
        stats = gi.domain_stats() or {}
        if not stats:
            return ""
        try:
            new_by_domain = (gi.growth_trends(days=7) or {}).get("new_by_domain", {}) or {}
        except Exception:
            new_by_domain = {}

        doms = sorted(
            ((d, s) for d, s in stats.items() if d and d not in _skip),
            key=lambda x: -(x[1].get("count", 0) or 0),
        )[:max_domains]
        if not doms:
            return ""

        header = (
            "[LO QUE ESTOY OBSERVANDO POR DOMINIO — datos reales, no inventar ni redondear]"
            if lang == "es" else
            "[WHAT I'M OBSERVING PER DOMAIN — real data, do not invent or round]"
        )
        lines = [header]
        for d, s in doms:
            count = s.get("count", 0) or 0
            hits = s.get("total_hits", 0) or 0
            tops = []
            try:
                for r in gi.top_stars(n=per_domain, domain=d):
                    label = (
                        getattr(r, "intent", "") or getattr(r, "fingerprint", "") or ""
                    ).strip()
                    if label:
                        tops.append(f"{label[:24]} (hits {getattr(r, 'hits', 0)})")
            except Exception:
                pass
            newn = new_by_domain.get(d, 0) or 0
            if lang == "es":
                seg = f"- {d}: {count} estrellas, {hits} activaciones"
                if tops:
                    seg += "; lo más activo: " + ", ".join(tops)
                if newn:
                    seg += f"; +{newn} nuevas esta semana"
            else:
                seg = f"- {d}: {count} stars, {hits} activations"
                if tops:
                    seg += "; most active: " + ", ".join(tops)
                if newn:
                    seg += f"; +{newn} new this week"
            lines.append(seg)
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("build_domain_observations failed: %s", exc)
        return ""
