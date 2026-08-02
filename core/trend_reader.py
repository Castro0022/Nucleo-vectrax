"""
core/trend_reader.py — Lector de duración (deltas temporales verificables).

Segunda mitad del "autoconocimiento temporal": convierte las **anclas ya
persistidas** en DURACIONES concretas, para que Vectrax pueda decir "llevo
observando esto desde hace X días" fundamentándose SOLO en datos históricos
reales — el LLM nunca infiere el tiempo, recibe el número ya calculado.

Anclas reutilizadas (todas read-only, ninguna nueva persistencia):
  • GravityRecord.first_seen / last_seen / freq   (core/learn/schemas.py:50-54)
        → antigüedad por patrón y por dominio, ritmo (hits/día).
  • convergence_events.timestamp (event='birth')  (core/learn/convergence_history.py)
        → edad de cada convergencia activa.
  • Outcome.resolved_ts por dominio               (core/learn/verification_ledger.py)
        → span (primer/último) de resultados verificados por dominio.

Detalle importante: gravity guarda timestamps ISO (con tz), mientras que
convergencias y verification usan epoch (float, time.time()). `days_since`
acepta AMBOS formatos.

Read-only y defensivo: cada acceso va en su try/except; si una fuente falla,
devuelve None/[]/'' y nunca lanza.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.trend_reader")

# Dominios de ruido operativo (no son dominios de conocimiento).
_SKIP_DOMAINS = {"unknown", "tests", "user_interest"}


# ---------------------------------------------------------------------------
# Primitivas de tiempo (aceptan epoch float o ISO string)
# ---------------------------------------------------------------------------

def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _to_epoch(ts: Any) -> Optional[float]:
    """Normaliza un timestamp (epoch float o ISO string) a epoch UTC.
    Devuelve None si es inválido/ausente. Nunca lanza."""
    if ts is None:
        return None
    try:
        if isinstance(ts, (int, float)):
            return float(ts) if ts > 0 else None
        s = str(ts).strip()
        if not s:
            return None
        s = s.replace("Z", "+00:00")            # py3.9 no acepta 'Z' en fromisoformat
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:                    # asume UTC si viene naive
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def days_since(ts: Any) -> Optional[float]:
    """Días transcurridos desde `ts` (epoch o ISO) hasta ahora. 1 decimal.
    None si el timestamp es inválido; nunca negativo."""
    ep = _to_epoch(ts)
    if ep is None:
        return None
    d = (_now().timestamp() - ep) / 86400.0
    return round(d, 1) if d > 0 else 0.0


# ---------------------------------------------------------------------------
# Duración por patrón (GravityRecord.first_seen)
# ---------------------------------------------------------------------------

def _record_to_duration(r: Any) -> Dict[str, Any]:
    """Extrae la duración de un GravityRecord (o namespace equivalente)."""
    return {
        "fingerprint": getattr(r, "fingerprint", ""),
        "domain": getattr(r, "domain", ""),
        "intent": getattr(r, "intent", ""),
        "first_seen": getattr(r, "first_seen", ""),
        "last_seen": getattr(r, "last_seen", ""),
        "days_observed": days_since(getattr(r, "first_seen", "")),
        "days_since_last": days_since(getattr(r, "last_seen", "")),
        "hits": getattr(r, "hits", 0),
        "freq": getattr(r, "freq", 0.0),
    }


def get_pattern_duration(fingerprint: str) -> Optional[Dict[str, Any]]:
    """Duración observada de UN patrón por su fingerprint. None si no existe."""
    try:
        from core.learn.gravity_engine import get_gravity_index
        rec = get_gravity_index().get(fingerprint)
        if rec is None:
            return None
        return _record_to_duration(rec)
    except Exception as exc:
        logger.debug("get_pattern_duration failed: %s", exc)
        return None


def top_pattern_durations(domain: str, n: int = 5) -> List[Dict[str, Any]]:
    """Duración de los patrones de mayor peso en un dominio (los más activos)."""
    try:
        from core.learn.gravity_engine import get_gravity_index
        recs = get_gravity_index().top_stars(n=n, domain=domain) or []
        return [_record_to_duration(r) for r in recs]
    except Exception as exc:
        logger.debug("top_pattern_durations failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Edad de convergencias activas (convergence_events.timestamp)
# ---------------------------------------------------------------------------

def _parse_domains(raw: Any) -> List[str]:
    def _clean(seq: Any) -> List[str]:
        return [str(x) for x in seq if str(x).strip()]
    if isinstance(raw, list):
        return _clean(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            val = json.loads(raw)
            if isinstance(val, list):
                return _clean(val)
        except Exception:
            return [raw.strip()]
    return []


def active_convergence_ages(
    domain: Optional[str] = None, limit: int = 20,
) -> List[Dict[str, Any]]:
    """Convergencias actualmente activas con su antigüedad (días desde el
    nacimiento). Si `domain` se indica, filtra las que lo incluyen. Orden:
    más antiguas primero."""
    try:
        from core.learn.convergence_history import get_active
        rows = get_active() or []
    except Exception as exc:
        logger.debug("active_convergence_ages failed: %s", exc)
        return []

    out: List[Dict[str, Any]] = []
    for row in rows:
        try:
            doms = _parse_domains(row.get("domains"))
            if domain and domain not in doms:
                continue
            out.append({
                "key": row.get("key", ""),
                "intent": row.get("intent", "") or "",
                "domains": doms,
                "age_days": days_since(row.get("timestamp")),
                "combined_cc": row.get("combined_cc", 0),
                "combined_hits": row.get("combined_hits", 0),
            })
        except Exception:
            continue
    out.sort(key=lambda x: (x["age_days"] is None, -(x["age_days"] or 0)))
    return out[:limit]


# ---------------------------------------------------------------------------
# Span de resultados verificados por dominio (Outcome.resolved_ts)
# ---------------------------------------------------------------------------

def domain_outcome_span(domain: str) -> Optional[Dict[str, Any]]:
    """Primer/último resultado VERIFICADO de un dominio y su ventana en días.
    None si el dominio no tiene outcomes registrados."""
    try:
        from core.learn.verification_ledger import load_outcomes
        outs = load_outcomes(domain) or []
    except Exception as exc:
        logger.debug("domain_outcome_span failed: %s", exc)
        return None
    ts = [_to_epoch(getattr(o, "resolved_ts", None)) for o in outs]
    ts = [t for t in ts if t is not None]
    if not ts:
        return None
    first, last = min(ts), max(ts)
    return {
        "n_outcomes": len(ts),
        "first_days_ago": days_since(first),
        "last_days_ago": days_since(last),
        "span_days": round((last - first) / 86400.0, 1),
    }


# ---------------------------------------------------------------------------
# Duración a nivel de dominio (compone todas las anclas)
# ---------------------------------------------------------------------------

def get_domain_duration(domain: str) -> Dict[str, Any]:
    """Anclas temporales de un dominio, ya calculadas:
      - observing_since_days: desde el primer first_seen de sus patrones.
      - days_since_last_activity: desde el last_seen más reciente.
      - n_patterns: patrones en el dominio.
      - outcomes: span de resultados verificados (o None).
      - active_convergences / oldest_convergence_days.
    Nunca lanza; los campos ausentes quedan en None/0.
    """
    result: Dict[str, Any] = {
        "domain": domain,
        "observing_since_days": None,
        "days_since_last_activity": None,
        "n_patterns": 0,
        "outcomes": None,
        "active_convergences": 0,
        "oldest_convergence_days": None,
    }

    # Gravity: earliest first_seen / most recent last_seen del dominio.
    try:
        from core.learn.gravity_engine import get_gravity_index
        recs = get_gravity_index().by_domain(domain) or []
        result["n_patterns"] = len(recs)
        firsts = [e for e in (_to_epoch(getattr(r, "first_seen", None)) for r in recs) if e]
        lasts = [e for e in (_to_epoch(getattr(r, "last_seen", None)) for r in recs) if e]
        if firsts:
            result["observing_since_days"] = days_since(min(firsts))
        if lasts:
            result["days_since_last_activity"] = days_since(max(lasts))
    except Exception as exc:
        logger.debug("get_domain_duration gravity failed: %s", exc)

    # Verification ledger: span de outcomes verificados.
    result["outcomes"] = domain_outcome_span(domain)

    # Convergencias activas que tocan el dominio.
    convs = active_convergence_ages(domain=domain)
    result["active_convergences"] = len(convs)
    if convs and convs[0].get("age_days") is not None:
        result["oldest_convergence_days"] = convs[0]["age_days"]

    return result


def domain_observing_since_days(domain: str) -> Optional[float]:
    """Días desde el primer `first_seen` de los patrones del dominio (SOLO
    gravity). Ligero: no toca convergencias ni verification. None si no hay
    patrones. Nunca lanza."""
    try:
        from core.learn.gravity_engine import get_gravity_index
        recs = get_gravity_index().by_domain(domain) or []
        firsts = [e for e in (_to_epoch(getattr(r, "first_seen", None)) for r in recs) if e]
        return days_since(min(firsts)) if firsts else None
    except Exception as exc:
        logger.debug("domain_observing_since_days failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Digest grounded para inyección (números precomputados, el LLM no calcula)
# ---------------------------------------------------------------------------

def _fmt_days(d: Optional[float], lang: str) -> str:
    if d is None:
        return "n/d" if lang == "es" else "n/a"
    n = int(round(d))
    if lang == "es":
        return "hoy" if n <= 0 else ("1 día" if n == 1 else f"{n} días")
    return "today" if n <= 0 else ("1 day" if n == 1 else f"{n} days")


def build_duration_digest(
    domain: Optional[str] = None,
    max_domains: int = 4,
    top_patterns: int = 1,
    lang: str = "es",
) -> str:
    """Texto grounded con la ANTIGÜEDAD real de lo observado, listo para
    inyectar en el prompt de auto-referencia. Números ya calculados; el LLM
    solo los enuncia, nunca infiere el tiempo. '' si no hay datos."""
    if lang not in ("es", "en"):
        lang = "es"

    # Selección de dominios.
    domains: List[str] = []
    if domain:
        domains = [domain]
    else:
        try:
            from core.learn.gravity_engine import get_gravity_index
            stats = get_gravity_index().domain_stats() or {}
            domains = [
                d for d, _ in sorted(
                    ((d, s) for d, s in stats.items()
                     if d and d not in _SKIP_DOMAINS),
                    key=lambda x: -(x[1].get("count", 0) or 0),
                )
            ][:max_domains]
        except Exception as exc:
            logger.debug("build_duration_digest domains failed: %s", exc)
            return ""

    if not domains:
        return ""

    header = (
        "[DESDE CUÁNDO — antigüedad real de lo observado, datos verificables, no inventar]"
        if lang == "es" else
        "[SINCE WHEN — real observed age, verifiable data, do not invent]"
    )
    lines = [header]
    for d in domains:
        dur = get_domain_duration(d)
        if not dur.get("observing_since_days") and not dur.get("outcomes") \
                and not dur.get("active_convergences"):
            continue
        since = _fmt_days(dur.get("observing_since_days"), lang)
        if lang == "es":
            seg = f"- {d}: observando desde hace {since}"
        else:
            seg = f"- {d}: observing for {since}"
        # patrón más activo con su antigüedad
        tops = top_pattern_durations(d, n=top_patterns)
        if tops:
            t = tops[0]
            label = (t.get("intent") or t.get("fingerprint") or "")[:24]
            if label:
                td = _fmt_days(t.get("days_observed"), lang)
                seg += (f"; «{label}» hace {td}" if lang == "es"
                        else f"; \"{label}\" for {td}")
        # convergencias activas
        nconv = dur.get("active_convergences", 0)
        if nconv:
            oldest = _fmt_days(dur.get("oldest_convergence_days"), lang)
            seg += (f"; {nconv} convergencia(s) activa(s) (la más antigua hace {oldest})"
                    if lang == "es" else
                    f"; {nconv} active convergence(s) (oldest {oldest})")
        # outcomes verificados
        oc = dur.get("outcomes")
        if oc and oc.get("first_days_ago") is not None:
            fd = _fmt_days(oc.get("first_days_ago"), lang)
            seg += (f"; resultados verificados desde hace {fd} (n={oc.get('n_outcomes', 0)})"
                    if lang == "es" else
                    f"; verified outcomes since {fd} (n={oc.get('n_outcomes', 0)})")
        lines.append(seg)

    return "\n".join(lines) if len(lines) > 1 else ""
