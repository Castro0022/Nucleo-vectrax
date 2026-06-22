"""
core/self_observation/quality_observer.py — Quality / change / failure
self-observation contract (Architecture Pillar C).

Vectrax must observe not only its cognitive universe (gravity, market, users)
but also its OWN engineering health: which tests fail, which recover, what
changes land in the code, and what breaks at runtime. This module is the
SINGLE SOURCE OF TRUTH for that observability contract. It records STRUCTURED
events into the existing observation ledger
(``core/self_observation/observation_ledger.py``) under four new domains:

    test     — test-suite signals (suite_start, suite_end, failed_test, recovered_test)
    code     — source changes (code_change: files, counts, scope, branch, commit)
    quality  — derived quality signals (gate results, regressions, ...)
    runtime  — runtime faults observed in production (runtime_error, ...)

PRIVACY / ABSTRACTION (project governance):
  Only ABSTRACT metadata is ever stored: file paths (repo-relative), counts,
  cause CLASSES (exception type names — never the message/values), durations,
  pass/fail, branch/scope. Raw prompts, secrets, tokens, personal data, diff
  contents and assertion values are NEVER persisted. A defensive sanitizer
  (key denylist + type allowlist + length caps + home-dir stripping) enforces
  this even if a caller passes something richer.

This module is a CONTRACT layer: producers (the pytest ledger plugin and the
code-change observer) call into it rather than touching the raw ledger, so the
event schemas stay centralized and consistent.

Public API:
    DOMAINS                                  -> frozenset of valid domains
    record_event(domain, obs_type, summary, *, star_id=None,
                 evidence=None, severity="info") -> int
    bind_vault_dir(vault_dir=None) -> str|None   (honor VECTRAX_VAULT_DIR)
    # test-domain producers
    record_suite_start(run_id, *, collected=None, **extra) -> int
    record_suite_end(run_id, *, total, passed, failed, skipped, errors,
                     duration_s, exit_status=None, **extra) -> int
    record_failed_test(nodeid, run_id, *, cause_class="unknown",
                       phase="call", duration_s=0.0) -> int
    record_recovered_test(nodeid, run_id, *, prev_run_id=None) -> int
    # code-domain producer
    record_code_change(*, files, insertions, deletions, scope, branch,
                       commit=None, source="commit", **extra) -> int
    # quality / runtime producers
    record_quality_event(obs_type, summary, *, severity="info", **evidence) -> int
    record_runtime_event(obs_type, summary, *, severity="info", **evidence) -> int
    record_runtime_error(cause_class, *, where=None, severity="warning", **evidence) -> int
    # helpers
    nodeid_to_file(nodeid) -> str
    path_scope(path) -> str
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, List, Optional

from core.self_observation import observation_ledger as _ol

logger = logging.getLogger("vectrax.quality_observer")

# ---------------------------------------------------------------------------
# Contract: domains + severities
# ---------------------------------------------------------------------------

DOMAIN_TEST = "test"
DOMAIN_CODE = "code"
DOMAIN_QUALITY = "quality"
DOMAIN_RUNTIME = "runtime"

DOMAINS = frozenset({DOMAIN_TEST, DOMAIN_CODE, DOMAIN_QUALITY, DOMAIN_RUNTIME})
SEVERITIES = frozenset({"info", "warning", "critical"})

# ---------------------------------------------------------------------------
# Privacy / abstraction guards
# ---------------------------------------------------------------------------

# Keys whose (lowercased) name contains any of these are dropped entirely —
# they may carry secrets or personal/raw content.
_DENY_KEY_PARTS = (
    "secret", "token", "password", "passwd", "credential", "api_key",
    "apikey", "authorization", "cookie", "prompt", "raw", "content",
    "payload", "email", "phone", "session_id", "access", "private",
)
_MAX_STR = 500          # cap any stored string
_MAX_LIST = 200         # cap any stored list
_MAX_DEPTH = 6          # cap nesting depth
_HOME = os.path.expanduser("~")


def _abstract_str(s: str) -> str:
    """Strip the home dir (username leak) and cap length."""
    if _HOME and len(_HOME) > 3 and _HOME in s:
        s = s.replace(_HOME, "~")
    return s[:_MAX_STR]


def _is_denied_key(key: str) -> bool:
    k = str(key).lower()
    return any(part in k for part in _DENY_KEY_PARTS)


def _sanitize(value: Any, _depth: int = 0) -> Any:
    """Recursively coerce ``value`` into abstract, JSON-safe metadata.

    - dict: drop denied keys, sanitize the rest.
    - list/tuple: sanitize items (capped).
    - str: home-stripped + length-capped.
    - bool/int/float/None: kept as-is.
    - anything else: replaced by a ``"<type>"`` placeholder (never its content).
    """
    if _depth > _MAX_DEPTH:
        return "<max_depth>"
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _abstract_str(value)
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            if _is_denied_key(k):
                out[str(k)] = "<redacted>"
                continue
            out[str(k)[:80]] = _sanitize(v, _depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        items = list(value)[:_MAX_LIST]
        return [_sanitize(v, _depth + 1) for v in items]
    # Unknown / complex object: never store its content.
    return "<%s>" % type(value).__name__


# ---------------------------------------------------------------------------
# Ledger binding + initialization
# ---------------------------------------------------------------------------

_initialized_paths: set = set()


def bind_vault_dir(vault_dir: Optional[str] = None) -> Optional[str]:
    """Point the ledger DB at ``vault_dir`` (or ``$VECTRAX_VAULT_DIR``).

    The ledger computes its DB path once at import time. Long-running or
    out-of-test producers (e.g. the opt-in pytest plugin, which runs at session
    scope outside the autouse hermetic fixture) must call this so writes land
    in the CONFIGURED/temp vault rather than the default real vault. This uses
    the same ``_DB_PATH`` redirection mechanism the hermetic conftest relies on.

    Returns the bound DB path, or None if no vault dir is available.
    """
    vault = vault_dir or os.environ.get("VECTRAX_VAULT_DIR")
    if not vault:
        return None
    db_path = os.path.join(vault, "observation_ledger.db")
    try:
        _ol._DB_PATH = db_path  # sanctioned isolation hook (see conftest)
        _initialized_paths.discard(db_path)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("bind_vault_dir failed: %s", exc)
        return None
    return db_path


def _ensure_init() -> None:
    """Create the ledger table for the current DB path (cached, idempotent)."""
    path = getattr(_ol, "_DB_PATH", None)
    if path in _initialized_paths:
        return
    try:
        _ol.init_ledger()
        _initialized_paths.add(path)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("quality_observer init failed: %s", exc)


# ---------------------------------------------------------------------------
# Core recorder
# ---------------------------------------------------------------------------

def record_event(
    domain: str,
    obs_type: str,
    summary: str,
    *,
    star_id: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
    severity: str = "info",
) -> int:
    """Record one structured quality/observability event. Returns the row id.

    Enforces the domain contract (``domain`` must be in :data:`DOMAINS`) and
    sanitizes ``evidence`` to abstract metadata only. Never raises on ledger
    failure (returns -1) so observation can never break product flow.
    """
    if domain not in DOMAINS:
        raise ValueError(
            "quality_observer only records domains %s (got %r)"
            % (sorted(DOMAINS), domain)
        )
    if severity not in SEVERITIES:
        logger.debug("coercing unknown severity %r -> info", severity)
        severity = "info"

    safe_evidence = _sanitize(evidence) if evidence else None
    _ensure_init()
    return _ol.record(
        domain,
        str(obs_type)[:80],
        _abstract_str(str(summary)),
        star_id=star_id,
        evidence=safe_evidence,
        severity=severity,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def nodeid_to_file(nodeid: str) -> str:
    """Extract the file path from a pytest node id (``file.py::Test::case``)."""
    if not nodeid:
        return ""
    return _abstract_str(str(nodeid).split("::", 1)[0])


def path_scope(path: str) -> str:
    """Top-level scope (first path segment) of a repo-relative path."""
    if not path:
        return ""
    norm = str(path).replace("\\", "/").lstrip("./")
    return norm.split("/", 1)[0] if "/" in norm else norm


def _scopes(paths: Iterable[str]) -> List[str]:
    seen: List[str] = []
    for p in paths:
        s = path_scope(p)
        if s and s not in seen:
            seen.append(s)
    return seen


# ---------------------------------------------------------------------------
# Producers — domain 'test'
# ---------------------------------------------------------------------------

def record_suite_start(run_id: str, *, collected: Optional[int] = None, **extra: Any) -> int:
    ev: Dict[str, Any] = {"run_id": run_id}
    if collected is not None:
        ev["collected"] = int(collected)
    ev.update(extra)
    n = "" if collected is None else " (%d tests)" % collected
    return record_event(
        DOMAIN_TEST, "suite_start",
        "Suite de tests iniciada%s [run %s]" % (n, run_id),
        evidence=ev,
    )


def record_suite_end(
    run_id: str,
    *,
    total: int,
    passed: int,
    failed: int,
    skipped: int = 0,
    errors: int = 0,
    duration_s: float = 0.0,
    exit_status: Optional[int] = None,
    **extra: Any,
) -> int:
    success = (failed == 0 and errors == 0)
    # suite_end is a run SUMMARY (quality-style), NOT a failure star. Per the
    # universe reader contract, severity warning|critical turns an event into a
    # failure star; using that here would double-count the per-test failures
    # (each failed_test is already its own failure star). Keep it 'info' — run
    # health lives in evidence (success / failed / errors).
    sev = "info"
    ev: Dict[str, Any] = {
        "run_id": run_id,
        "total": int(total),
        "passed": int(passed),
        "failed": int(failed),
        "skipped": int(skipped),
        "errors": int(errors),
        "duration_s": round(float(duration_s), 3),
        "success": success,
    }
    if exit_status is not None:
        ev["exit_status"] = int(exit_status)
    ev.update(extra)
    summary = (
        "Suite finalizada [run %s]: %d ok, %d fallos, %d errores, "
        "%d omitidos de %d en %.2fs"
        % (run_id, passed, failed, errors, skipped, total, duration_s)
    )
    return record_event(DOMAIN_TEST, "suite_end", summary, evidence=ev, severity=sev)


def record_failed_test(
    nodeid: str,
    run_id: str,
    *,
    cause_class: str = "unknown",
    phase: str = "call",
    duration_s: float = 0.0,
    count: Optional[int] = None,
) -> int:
    file = nodeid_to_file(nodeid)
    ev: Dict[str, Any] = {
        "run_id": run_id,
        "nodeid": _abstract_str(str(nodeid)),
        # 'file' is the reader's component anchor (module|file|path).
        "file": file,
        "scope": path_scope(file),
        "cause_class": _abstract_str(str(cause_class))[:120],
        "phase": str(phase)[:20],
        "duration_s": round(float(duration_s), 3),
    }
    # Optional cross-run repetition count → reader brightness boost.
    if count is not None:
        ev["count"] = int(count)
    # Errors during setup/teardown are structurally worse than a plain assert.
    sev = "critical" if phase in ("setup", "teardown") else "warning"
    return record_event(
        DOMAIN_TEST, "failed_test",
        "Test fallido (%s) en %s [%s]" % (cause_class, nodeid, phase),
        evidence=ev, severity=sev,
    )


def record_recovered_test(
    nodeid: str,
    run_id: str,
    *,
    prev_run_id: Optional[str] = None,
) -> int:
    file = nodeid_to_file(nodeid)
    ev = {
        "run_id": run_id,
        "prev_run_id": prev_run_id,
        "nodeid": _abstract_str(str(nodeid)),
        "file": file,
        "scope": path_scope(file),
    }
    return record_event(
        DOMAIN_TEST, "recovered_test",
        "Test recuperado (antes fallaba, ahora pasa): %s" % nodeid,
        evidence=ev, severity="info",
    )


# ---------------------------------------------------------------------------
# Producer — domain 'code'
# ---------------------------------------------------------------------------

def record_code_change(
    *,
    files: Iterable[str],
    insertions: int = 0,
    deletions: int = 0,
    scope: Optional[Iterable[str]] = None,
    branch: str = "",
    commit: Optional[str] = None,
    source: str = "commit",
    **extra: Any,
) -> int:
    file_list = [_abstract_str(str(f)) for f in (files or [])][:_MAX_LIST]
    scope_list = list(scope) if scope is not None else _scopes(file_list)
    clean_scope = [str(s)[:60] for s in scope_list][:50]
    ev: Dict[str, Any] = {
        "source": str(source)[:20],
        "branch": _abstract_str(str(branch)),
        "commit": (_abstract_str(str(commit))[:40] if commit else None),
        "file_count": len(file_list),
        "files": file_list,
        "insertions": int(insertions),
        "deletions": int(deletions),
        "scope": clean_scope,
        # 'modules' = distinct components touched. Reader treats >1 module as a
        # cross-module convergence cluster.
        "modules": clean_scope,
    }
    ev.update(extra)
    label = commit[:8] if commit else source
    summary = (
        "Cambio de código [%s] en %s: %d archivo(s), +%d/-%d (%s)"
        % (label, branch or "?", len(file_list), insertions, deletions,
           ",".join(ev["scope"][:5]) or "?")
    )
    return record_event(DOMAIN_CODE, "code_change", summary, evidence=ev)


# ---------------------------------------------------------------------------
# Producers — domains 'quality' and 'runtime'
# ---------------------------------------------------------------------------

def record_quality_event(
    obs_type: str,
    summary: str,
    *,
    severity: str = "info",
    **evidence: Any,
) -> int:
    return record_event(
        DOMAIN_QUALITY, obs_type, summary,
        evidence=evidence or None, severity=severity,
    )


def record_runtime_event(
    obs_type: str,
    summary: str,
    *,
    severity: str = "info",
    **evidence: Any,
) -> int:
    return record_event(
        DOMAIN_RUNTIME, obs_type, summary,
        evidence=evidence or None, severity=severity,
    )


def record_runtime_error(
    cause_class: str,
    *,
    where: Optional[str] = None,
    severity: str = "warning",
    **evidence: Any,
) -> int:
    ev: Dict[str, Any] = {"cause_class": _abstract_str(str(cause_class))[:120]}
    if where:
        ev["where"] = _abstract_str(str(where))[:120]
    ev.update(evidence)
    summary = "Fallo en runtime (%s)%s" % (
        cause_class, (" en %s" % where) if where else "",
    )
    return record_event(DOMAIN_RUNTIME, "runtime_error", summary,
                        evidence=ev, severity=severity)


__all__ = [
    "DOMAINS", "SEVERITIES",
    "DOMAIN_TEST", "DOMAIN_CODE", "DOMAIN_QUALITY", "DOMAIN_RUNTIME",
    "record_event", "bind_vault_dir",
    "record_suite_start", "record_suite_end",
    "record_failed_test", "record_recovered_test",
    "record_code_change",
    "record_quality_event", "record_runtime_event", "record_runtime_error",
    "nodeid_to_file", "path_scope",
]
