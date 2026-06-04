"""
core/self_observation/autonomous_observer.py — Observador autónomo.

Compara el snapshot actual del universo con el anterior y registra
cada cambio detectado como una observación persistente en el ledger.

Detectores:
  - Gravity engine: nuevas estrellas, crecimiento de hits, tier changes
  - Market: nuevas señales, cambios en win rate
  - Convergences: nuevas convergencias cross-domain
  - Operator: cambios de estado del worker, cola, errores
  - Health: spikes de errores, señales de percepción
  - Users: nuevos usuarios, picos de actividad

Diseño:
  - Se ejecuta en cada ciclo del meta_loop (~60s)
  - Defensivo: si falla un detector, los demás siguen
  - No hace red, no manda mensajes — solo observa y registra
  - Mantiene el snapshot previo en memoria para comparar

API pública:
    observe_and_record() -> int   (número de observaciones registradas)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.autonomous_observer")

# Previous snapshot (module-level, survives across calls)
_prev_snapshot: Optional[Dict[str, Any]] = None
_prev_gravity: Optional[Dict[str, Any]] = None
_prev_mode: str = ""            # last active mode (market/memory)
_daily_reflection_date: str = ""  # date of last daily reflection (YYYY-MM-DD)


def observe_and_record() -> int:
    """Run one observation cycle. Returns count of observations recorded."""
    global _prev_snapshot, _prev_gravity

    from core.self_observation.observation_ledger import init_ledger, record
    init_ledger()

    recorded = 0

    # Collect current universe snapshot
    try:
        from core.self_observation.universe_observer import observe_universe
        snap = observe_universe()
        current = snap.to_dict()
    except Exception as exc:
        logger.error("autonomous_observer: snapshot failed: %s", exc)
        record("health", "snapshot_failure", f"No pude observar el universo: {exc}",
               severity="warning")
        return 1

    # Collect gravity engine data separately for detailed comparison
    current_gravity = _collect_gravity_detail()

    prev = _prev_snapshot
    prev_grav = _prev_gravity

    # First run — record initial baseline observation
    if prev is None:
        recorded += _record_baseline(current, current_gravity, record)
        _prev_snapshot = current
        _prev_gravity = current_gravity
        return recorded

    # Determine active mode
    try:
        from connectors.etoro.market_mode import get_active_mode
        mode = get_active_mode()
    except Exception:
        mode = "memory"

    # Compare and detect changes
    # -- Always active (both modes) --
    try:
        recorded += _detect_operator_changes(prev, current, record)
    except Exception as exc:
        logger.debug("operator detector failed: %s", exc)

    try:
        recorded += _detect_gravity_changes(prev_grav, current_gravity, record)
    except Exception as exc:
        logger.debug("gravity detector failed: %s", exc)

    try:
        recorded += _detect_convergence_changes(prev, current, record)
    except Exception as exc:
        logger.debug("convergence detector failed: %s", exc)

    try:
        recorded += _detect_health_changes(prev, current, record)
    except Exception as exc:
        logger.debug("health detector failed: %s", exc)

    try:
        recorded += _detect_user_changes(prev, current, record)
    except Exception as exc:
        logger.debug("user detector failed: %s", exc)

    # -- Market Mode only (silenced when markets closed) --
    if mode == "market":
        try:
            recorded += _detect_market_changes(prev_grav, current_gravity, record)
        except Exception as exc:
            logger.debug("market detector failed: %s", exc)

    # -- Mode transition detection --
    try:
        recorded += _detect_mode_transition(mode, current, current_gravity, record)
    except Exception as exc:
        logger.debug("mode transition detector failed: %s", exc)

    # -- Daily reflection (runs once per day after 22:00 UTC, regardless of crypto) --
    try:
        recorded += _daily_memory_reflection(current, current_gravity, record)
    except Exception as exc:
        logger.debug("daily reflection failed: %s", exc)

    # Update previous state
    _prev_snapshot = current
    _prev_gravity = current_gravity

    if recorded > 0:
        logger.info("autonomous_observer: %d observations recorded", recorded)

    return recorded


# ---------------------------------------------------------------------------
# Gravity detail collector
# ---------------------------------------------------------------------------

def _collect_gravity_detail() -> Dict[str, Any]:
    """Collect detailed gravity engine state for comparison."""
    data: Dict[str, Any] = {"total": 0, "stars": {}, "domains": {}}
    try:
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
        records = gi.all_records()
        data["total"] = len(records)
        data["domains"] = gi.domain_stats()
        for r in records:
            data["stars"][r.fingerprint] = {
                "domain": r.domain,
                "hits": r.hits,
                "tier": r.tier,
                "cc": round(r.cc_score, 3),
                "intent": r.intent,
                "summary": (r.summary or "")[:60],
            }
    except Exception as exc:
        logger.debug("gravity detail collection failed: %s", exc)
    return data


# ---------------------------------------------------------------------------
# Baseline (first observation)
# ---------------------------------------------------------------------------

def _record_baseline(current: dict, grav: dict, record) -> int:
    n = 0
    total_stars = current.get("gravity_total", 0) + current.get("star_count", 0)
    record(
        "gravity", "baseline",
        f"Observación inicial del universo: {total_stars} estrellas totales, "
        f"{grav.get('total', 0)} gravitacionales, "
        f"{len(current.get('convergences', []))} convergencias",
        evidence={"total_stars": total_stars, "gravity": grav.get("total", 0),
                  "domains": list(grav.get("domains", {}).keys())},
    )
    n += 1

    worker = "activo" if current.get("worker_alive") else "inactivo"
    record(
        "operator", "baseline",
        f"Estado operacional inicial: worker {worker}, "
        f"cola {current.get('queue_pending', 0)} pendientes, "
        f"{current.get('recent_error_count_24h', 0)} errores 24h",
        evidence={"worker_alive": current.get("worker_alive"),
                  "queue_pending": current.get("queue_pending", 0)},
    )
    n += 1
    return n


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def _detect_operator_changes(prev: dict, curr: dict, record) -> int:
    n = 0
    # Worker state change
    if prev.get("worker_alive") != curr.get("worker_alive"):
        state = "activó" if curr.get("worker_alive") else "detuvo"
        sev = "info" if curr.get("worker_alive") else "warning"
        record("operator", "worker_state",
               f"Worker se {state}",
               evidence={"was": prev.get("worker_alive"), "now": curr.get("worker_alive")},
               severity=sev)
        n += 1

    # Queue pressure change (significant delta)
    prev_q = prev.get("queue_pending", 0)
    curr_q = curr.get("queue_pending", 0)
    if curr_q > prev_q + 5:
        record("operator", "queue_pressure",
               f"Cola creció: {prev_q} → {curr_q} pendientes",
               evidence={"prev": prev_q, "now": curr_q},
               severity="warning" if curr_q > 10 else "info")
        n += 1
    elif curr_q == 0 and prev_q > 3:
        record("operator", "queue_cleared",
               f"Cola se vació: {prev_q} → 0",
               evidence={"prev": prev_q})
        n += 1

    return n


def _detect_gravity_changes(prev_grav: Optional[dict], curr_grav: dict, record) -> int:
    if not prev_grav:
        return 0
    n = 0

    # New stars
    prev_ids = set(prev_grav.get("stars", {}).keys())
    curr_ids = set(curr_grav.get("stars", {}).keys())
    new_stars = curr_ids - prev_ids
    for sid in list(new_stars)[:10]:  # cap at 10 per cycle
        star = curr_grav["stars"][sid]
        record("gravity", "new_star",
               f"Nueva estrella: {star['domain']}/{star['intent']} — {star['summary'][:50]}",
               star_id=sid,
               evidence={"domain": star["domain"], "tier": star["tier"],
                          "hits": star["hits"], "intent": star["intent"]})
        n += 1

    # Star growth (hits increased)
    for sid in curr_ids & prev_ids:
        prev_star = prev_grav["stars"].get(sid, {})
        curr_star = curr_grav["stars"].get(sid, {})
        if curr_star.get("hits", 0) > prev_star.get("hits", 0) + 2:
            record("gravity", "star_growth",
                   f"Estrella creció: {curr_star.get('domain', '?')}/{sid[:20]} "
                   f"hits {prev_star.get('hits',0)} → {curr_star.get('hits',0)}",
                   star_id=sid,
                   evidence={"prev_hits": prev_star.get("hits"),
                             "now_hits": curr_star.get("hits"),
                             "domain": curr_star.get("domain")})
            n += 1

    # Total gravity count change
    prev_total = prev_grav.get("total", 0)
    curr_total = curr_grav.get("total", 0)
    if curr_total > prev_total:
        delta = curr_total - prev_total
        record("gravity", "universe_growth",
               f"Universo gravitacional creció: +{delta} estrellas ({prev_total} → {curr_total})",
               evidence={"prev": prev_total, "now": curr_total, "delta": delta})
        n += 1

    return n


def _detect_convergence_changes(prev: dict, curr: dict, record) -> int:
    n = 0
    prev_conv = len(prev.get("gravity_convergences", []))
    curr_conv = len(curr.get("gravity_convergences", []))
    if curr_conv > prev_conv:
        delta = curr_conv - prev_conv
        record("convergence", "convergence_detected",
               f"+{delta} nueva(s) convergencia(s) cross-domain detectada(s) "
               f"({prev_conv} → {curr_conv})",
               evidence={"prev": prev_conv, "now": curr_conv},
               severity="info")
        n += 1
    return n


def _detect_health_changes(prev: dict, curr: dict, record) -> int:
    n = 0
    prev_err = prev.get("recent_error_count_24h", 0)
    curr_err = curr.get("recent_error_count_24h", 0)

    # Error spike
    if curr_err > prev_err + 10:
        sev = "critical" if curr_err > 50 else "warning"
        record("health", "error_spike",
               f"Errores 24h aumentaron: {prev_err} → {curr_err} (+{curr_err - prev_err})",
               evidence={"prev": prev_err, "now": curr_err},
               severity=sev)
        n += 1
    elif curr_err < prev_err - 10:
        record("health", "error_recovery",
               f"Errores 24h disminuyeron: {prev_err} → {curr_err}",
               evidence={"prev": prev_err, "now": curr_err})
        n += 1

    # Perception signals
    prev_signals = set(prev.get("signals", []))
    curr_signals = set(curr.get("signals", []))
    new_signals = curr_signals - prev_signals
    for sig in new_signals:
        sev = "warning" if "critical" in sig or "unresponsive" in sig else "info"
        record("health", "signal",
               f"Nueva señal de percepción: {sig}",
               evidence={"signal": sig},
               severity=sev)
        n += 1

    return n


def _detect_user_changes(prev: dict, curr: dict, record) -> int:
    n = 0
    prev_users = prev.get("star_count", 0)
    curr_users = curr.get("star_count", 0)
    if curr_users > prev_users:
        delta = curr_users - prev_users
        record("user", "new_users",
               f"+{delta} nueva(s) estrella(s) de usuario ({prev_users} → {curr_users})",
               evidence={"prev": prev_users, "now": curr_users, "delta": delta})
        n += 1
    return n


def _detect_market_changes(prev_grav: Optional[dict], curr_grav: dict, record) -> int:
    """Only runs in Market Mode (gated by caller)."""
    if not prev_grav:
        return 0
    n = 0

    # Only observe symbols with open markets
    try:
        from connectors.etoro.market_mode import is_market_open
    except ImportError:
        is_market_open = lambda s: True  # fallback: always open

    prev_market = {k: v for k, v in prev_grav.get("stars", {}).items()
                   if v.get("domain") == "market"}
    curr_market = {k: v for k, v in curr_grav.get("stars", {}).items()
                   if v.get("domain") == "market"}

    new_market = set(curr_market.keys()) - set(prev_market.keys())
    for sid in new_market:
        star = curr_market[sid]
        sym = star.get("intent", sid.replace("market:", ""))
        if not is_market_open(sym):
            continue
        record("market", "new_market_star",
               f"Nuevo símbolo de mercado observado: {sym}",
               star_id=sid,
               evidence={"intent": sym, "hits": star.get("hits"), "mode": "market"})
        n += 1

    # Market star activity (only for open markets)
    for sid in set(curr_market.keys()) & set(prev_market.keys()):
        star = curr_market[sid]
        sym = star.get("intent", sid.replace("market:", ""))
        if not is_market_open(sym):
            continue
        prev_hits = prev_market[sid].get("hits", 0)
        curr_hits = star.get("hits", 0)
        if curr_hits > prev_hits:
            record("market", "market_activity",
                   f"Actividad de mercado: {sym} "
                   f"hits {prev_hits} → {curr_hits}",
                   star_id=sid,
                   evidence={"prev_hits": prev_hits, "now_hits": curr_hits, "mode": "market"})
            n += 1

    return n


# ---------------------------------------------------------------------------
# Mode transition + Daily memory reflection
# ---------------------------------------------------------------------------

def _detect_mode_transition(mode: str, current: dict, grav: dict, record) -> int:
    """Detect and record transitions between Market and Memory mode."""
    global _prev_mode
    n = 0
    if _prev_mode and mode != _prev_mode:
        if mode == "memory":
            record("memory", "mode_transition",
                   "Mercados cerrados → Modo Memoria activado. "
                   "Reflexionando sobre lo observado.",
                   evidence={"from": _prev_mode, "to": mode},
                   severity="info")
        else:
            record("market", "mode_transition",
                   "Mercados abiertos → Modo Mercado activado. "
                   "Observando precios, volumen, señales.",
                   evidence={"from": _prev_mode, "to": mode},
                   severity="info")
        n += 1
        logger.info("MODE TRANSITION: %s → %s", _prev_mode, mode)
    _prev_mode = mode
    return n


def _daily_memory_reflection(current: dict, grav: dict, record) -> int:
    """Generate a daily summary of what was observed.

    Runs once per day after 22:00 UTC (US market close).
    Crypto being 24/7 doesn't block the reflection — Wall Street
    closing is the trigger for daily memory consolidation.
    """
    global _daily_reflection_date
    import datetime
    now = datetime.datetime.utcnow()
    today = now.strftime("%Y-%m-%d")
    if _daily_reflection_date == today:
        return 0  # already reflected today
    if now.hour < 22:
        return 0  # wait until after 22:00 UTC (US market close)

    _daily_reflection_date = today
    n = 0

    # Gather day's stats
    total_stars = grav.get("total", 0)
    domains = grav.get("domains", {})
    convergences = len(current.get("gravity_convergences", []))
    errors_24h = current.get("recent_error_count_24h", 0)
    user_stars = current.get("star_count", 0)

    # Count today's observations from ledger
    day_obs = 0
    market_obs = 0
    try:
        from core.self_observation.observation_ledger import get_recent
        recent = get_recent(limit=200)
        for o in recent:
            ts = o.get("timestamp", "")
            if ts.startswith(today) or ts.startswith(today.replace("-", "")):
                day_obs += 1
                if o.get("domain") == "market":
                    market_obs += 1
    except Exception:
        pass

    # Count patterns
    patterns_total = 0
    patterns_usable = 0
    try:
        from connectors.etoro.pattern_memory import get_patterns
        all_p = get_patterns()
        patterns_total = len(all_p)
        patterns_usable = sum(1 for p in all_p if p.is_usable)
    except Exception:
        pass

    # Domain mass summary
    domain_summary = []
    for d, info in domains.items():
        count = info.get("count", 0) if isinstance(info, dict) else info
        domain_summary.append(f"{d}:{count}")

    summary = (
        f"Reflexión diaria ({today}): "
        f"{total_stars} estrellas gravitacionales, "
        f"{convergences} convergencias, "
        f"{user_stars} usuarios, "
        f"{patterns_total} patrones ({patterns_usable} usables), "
        f"{day_obs} observaciones hoy ({market_obs} de mercado), "
        f"{errors_24h} errores 24h"
    )

    record(
        "memory", "daily_reflection",
        summary[:200],
        evidence={
            "date": today,
            "total_stars": total_stars,
            "convergences": convergences,
            "user_stars": user_stars,
            "patterns_total": patterns_total,
            "patterns_usable": patterns_usable,
            "observations_today": day_obs,
            "market_observations": market_obs,
            "errors_24h": errors_24h,
            "domains": domain_summary,
        },
    )
    n += 1
    logger.info("DAILY REFLECTION recorded for %s", today)
    return n
