"""
core/self_observation/quality_entities.py — Quality as gravitational phenomena.

Architecture (Pillar D): make changes/failures VISIBLE in the Vectrax Universe
as *living phenomena*, not an administrative table. This module is the single
SOURCE-OF-TRUTH that maps quality/test/code/runtime observations (already
persisted in the observation_ledger by the quality observers) into the
universe's gravitational vocabulary:

  - test/runtime FAILURES        -> stars whose brightness ~ risk/severity
  - RECOVERIES (green again)      -> cooling (a star that dims and fades)
  - CODE CHANGES (commits/edits)  -> events (transient flashes near a module)
  - ROOT-CAUSE CONVERGENCES       -> cross-module clusters (constellations of risk)

Design contract (so the writer side — quality_observer — and this reader stay
aligned without coupling):

  * Source: ``core.self_observation.observation_ledger`` rows.
  * Quality domains: ``quality`` | ``test`` | ``code`` | ``runtime``.
  * Classification is by ``obs_type`` keyword first, then ``severity`` — so the
    writer only needs to pick a sensible ``domain`` + ``obs_type`` + ``severity``;
    it never needs to know about the universe representation.
  * Optional ``evidence`` keys understood (all optional, all defensive):
        module / file / path  -> module name (which part of the system)
        modules               -> list[str] for cross-module convergences
        risk / score          -> 0..1 risk boost for brightness
        count / occurrences    -> repetition (also boosts brightness)

Everything here is READ-ONLY and DEFENSIVE: it never writes, never does network,
never raises. On any failure it returns empty structures so it can never block a
response, a worker, or a market cycle.

Public API:
    get_quality_entities(limit=...) -> dict   # detailed entities (for /v1/universe)
    get_quality_summary(limit=...)  -> dict   # counts only (for the census)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.self_observation.quality_entities")

# ---------------------------------------------------------------------------
# Contract constants (shared vocabulary with the writer side)
# ---------------------------------------------------------------------------

# Ledger domains that represent system quality/health phenomena.
QUALITY_DOMAINS = ("quality", "test", "code", "runtime")

# obs_type keyword classification (checked in priority order below).
_CONVERGENCE_KEYS = (
    "root_cause", "rootcause", "convergence", "cluster",
    "cross_module", "cross-module",
)
_RECOVERY_KEYS = (
    "recover", "recovery", "pass", "passed", "green", "fixed", "fix",
    "heal", "healed", "resolved", "cooling", "cooled", "stabil", "restored",
)
_CODE_KEYS = (
    "code_change", "code", "commit", "deploy", "edit", "refactor",
    "patch", "diff", "changeset",
)
_FAILURE_KEYS = (
    "fail", "error", "regression", "regress", "break", "broke", "broken",
    "red", "flaky", "crash", "exception", "timeout", "leak", "oom",
)

# severity -> base brightness (how bright/hot the failure star burns).
_SEVERITY_BRIGHTNESS = {"critical": 1.0, "warning": 0.65, "info": 0.4}

# How many recent observations to scan. Bounded so a read can never become a
# slow scan that blocks request/worker/market paths.
_DEFAULT_LIMIT = 400

# Max detailed entities returned per kind (the universe canvas needs bounded
# data; counts in the summary are unbounded/accurate).
_MAX_PER_KIND = 120


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _infer_module(obs: Dict[str, Any]) -> str:
    """Best-effort module/component name for a quality observation."""
    ev = obs.get("evidence")
    if isinstance(ev, dict):
        for key in ("module", "file", "path", "component", "target"):
            val = ev.get(key)
            if val:
                # Normalise a file path to a short module-ish token.
                token = str(val).strip()
                token = token.replace("\\", "/")
                if "/" in token:
                    token = token.rsplit("/", 1)[-1]
                return token[:48]
    star = obs.get("star_id")
    if star:
        return str(star)[:48]
    # Fall back to the domain so the entity is still placeable.
    return str(obs.get("domain") or "system")[:48]


def _classify(obs: Dict[str, Any]) -> Optional[str]:
    """Return the universe entity kind for a ledger observation.

    One of: ``convergence`` | ``recovery`` | ``code`` | ``failure``.
    Returns ``None`` for non-actionable info noise so the universe stays clean.
    """
    obs_type = str(obs.get("obs_type") or "").lower()
    domain = str(obs.get("domain") or "").lower()
    severity = str(obs.get("severity") or "info").lower()
    evidence = obs.get("evidence") if isinstance(obs.get("evidence"), dict) else {}

    def _has(keys) -> bool:
        return any(k in obs_type for k in keys)

    # Cross-module root cause is the most specific phenomenon.
    if _has(_CONVERGENCE_KEYS) or (isinstance(evidence.get("modules"), list)
                                   and len(evidence.get("modules") or []) > 1):
        return "convergence"
    if _has(_RECOVERY_KEYS):
        return "recovery"
    if domain == "code" or _has(_CODE_KEYS):
        return "code"
    if _has(_FAILURE_KEYS) or severity in ("warning", "critical"):
        return "failure"
    # Plain informational quality note with no risk -> not a phenomenon.
    return None


def _brightness(obs: Dict[str, Any]) -> float:
    """Brightness of a failure star ~ risk/severity (0.2 .. 1.0)."""
    severity = str(obs.get("severity") or "info").lower()
    base = _SEVERITY_BRIGHTNESS.get(severity, 0.4)
    ev = obs.get("evidence") if isinstance(obs.get("evidence"), dict) else {}
    boost = 0.0
    try:
        risk = ev.get("risk", ev.get("score"))
        if risk is not None:
            boost += _clamp(float(risk)) * 0.3
    except (TypeError, ValueError):
        pass
    try:
        count = ev.get("count", ev.get("occurrences"))
        if count is not None:
            # Repetition makes a failure burn brighter (log-ish, bounded).
            boost += _clamp((float(count) - 1.0) * 0.05, 0.0, 0.25)
    except (TypeError, ValueError):
        pass
    return round(_clamp(base + boost, 0.2, 1.0), 3)


def _entity_base(obs: Dict[str, Any]) -> Dict[str, Any]:
    """Common fields shared by every quality entity."""
    return {
        "id": f"q{obs.get('id')}" if obs.get("id") is not None else None,
        "domain": obs.get("domain"),
        "obs_type": obs.get("obs_type"),
        "module": _infer_module(obs),
        "severity": str(obs.get("severity") or "info").lower(),
        "summary": (obs.get("summary") or "")[:160],
        "timestamp": obs.get("timestamp"),
        "star_id": obs.get("star_id"),
    }


def _modules_of(obs: Dict[str, Any]) -> List[str]:
    ev = obs.get("evidence") if isinstance(obs.get("evidence"), dict) else {}
    mods = ev.get("modules")
    if isinstance(mods, list) and mods:
        return [str(m)[:48] for m in mods][:8]
    # Single module fallback so a convergence always has ≥1 anchor.
    return [_infer_module(obs)]


def _read_ledger(limit: int) -> List[Dict[str, Any]]:
    """Read recent quality-domain observations. Never raises."""
    try:
        from core.self_observation.observation_ledger import get_recent
    except Exception as exc:  # ledger module unavailable
        logger.debug("quality_entities: ledger import failed: %s", exc)
        return []
    out: List[Dict[str, Any]] = []
    try:
        for obs in get_recent(limit=limit) or []:
            if str(obs.get("domain") or "").lower() in QUALITY_DOMAINS:
                out.append(obs)
    except Exception as exc:
        logger.debug("quality_entities: ledger read failed: %s", exc)
        return []
    return out


def _empty() -> Dict[str, Any]:
    return {
        "failures": [],
        "recoveries": [],
        "events": [],
        "convergences": [],
        "counts": {
            "failures": 0,
            "active_failures": 0,
            "recoveries": 0,
            "code_changes": 0,
            "convergences": 0,
            "total": 0,
        },
    }


def get_quality_entities(limit: int = _DEFAULT_LIMIT) -> Dict[str, Any]:
    """Map recent quality observations into universe entities.

    Returns a dict with ``failures`` (stars), ``recoveries`` (cooling),
    ``events`` (code changes) and ``convergences`` (root-cause clusters),
    plus a ``counts`` block. Always returns a well-formed dict (empty on
    failure) — never raises, never blocks.
    """
    observations = _read_ledger(limit)
    if not observations:
        return _empty()

    failures: List[Dict[str, Any]] = []
    recoveries: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    convergences: List[Dict[str, Any]] = []

    # Track the most recent recovery time per module so we can mark failures
    # that have since cooled (recovered). Ledger rows arrive newest-first.
    recovered_modules: Dict[str, str] = {}

    for obs in observations:
        try:
            kind = _classify(obs)
            if kind is None:
                continue
            base = _entity_base(obs)

            if kind == "failure":
                module = base["module"]
                # A failure is "cooling" if a more recent recovery exists for it.
                cooled = module in recovered_modules
                failures.append({
                    **base,
                    "kind": "failure",
                    "brightness": _brightness(obs),
                    "cooling": cooled,
                    "recovered_at": recovered_modules.get(module),
                })
            elif kind == "recovery":
                module = base["module"]
                # First (newest) recovery seen for this module wins.
                recovered_modules.setdefault(module, base["timestamp"])
                recoveries.append({**base, "kind": "recovery", "cooling": True})
            elif kind == "code":
                events.append({**base, "kind": "code_event"})
            elif kind == "convergence":
                convergences.append({
                    **base,
                    "kind": "convergence",
                    "modules": _modules_of(obs),
                })
        except Exception as exc:  # one bad row must not break the rest
            logger.debug("quality_entities: row map failed: %s", exc)
            continue

    active_failures = sum(1 for f in failures if not f.get("cooling"))

    counts = {
        "failures": len(failures),
        "active_failures": active_failures,
        "recoveries": len(recoveries),
        "code_changes": len(events),
        "convergences": len(convergences),
        "total": len(observations),
    }

    return {
        "failures": failures[:_MAX_PER_KIND],
        "recoveries": recoveries[:_MAX_PER_KIND],
        "events": events[:_MAX_PER_KIND],
        "convergences": convergences[:_MAX_PER_KIND],
        "counts": counts,
    }


def get_quality_summary(limit: int = _DEFAULT_LIMIT) -> Dict[str, int]:
    """Lightweight counts for the census (single source of truth for totals).

    Mirrors ``get_quality_entities()['counts']`` but skips building the
    detailed entity lists when only totals are needed.
    """
    try:
        return dict(get_quality_entities(limit=limit)["counts"])
    except Exception as exc:
        logger.debug("quality_entities: summary failed: %s", exc)
        return dict(_empty()["counts"])
