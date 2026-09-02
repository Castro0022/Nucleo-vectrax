"""
core/learn/verification_ledger.py — Memoria persistente de resultados VERIFICADOS.

Capa domain-agnostic del ciclo de aprendizaje (el "reaprendizaje"): acumula los
``Outcome`` verificados que produce CUALQUIER ``OutcomeAdapter``, para que el
criterio de un dominio pueda calcularse sobre desempeño REAL en vez de proxies.

Un archivo JSONL por dominio en ``<vault>/domain_verification/{domain}.jsonl``.
Portable, sin dependencia de DB, append-only. El path se resuelve en runtime
desde ``VECTRAX_VAULT_DIR`` (mismo convenio que el resto del vault), lo que lo
hace testeable con un vault temporal.

Es INVARIANTE: no sabe nada de trading, freight ni de ningún dominio; solo
persiste Outcomes y los agrega con el núcleo común (``score_outcomes``).

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Dict, List, Optional

from core.learn.outcome_adapter import (
    DomainScore,
    Outcome,
    OutcomeStatus,
    score_outcomes,
)

logger = logging.getLogger("vectrax.verification_ledger")

_lock = threading.Lock()

# Read-side cache: the full parsed outcome list per domain, invalidated by the
# file's mtime. Domains like cybersecurity can accumulate hundreds of thousands
# of append-only lines (one per (cve, subject-level) decisive outcome); without
# this, every call from the criterion/opinion engine (subject_scores /
# rank_domain_evidence) re-reads and re-parses the entire file from disk, even
# though writes only happen in bursts (learning cycle every ~6h, or a one-off
# backfill). Keyed by domain; a cheap os.stat() decides whether to reuse the
# cached list or re-parse. Never serves stale data: any write changes the
# file's mtime, which the next read detects.
_load_cache_lock = threading.Lock()
_load_cache: Dict[str, tuple] = {}  # domain -> (mtime, List[Outcome])


def _vault_dir() -> str:
    return os.environ.get(
        "VECTRAX_VAULT_DIR",
        os.path.join(os.path.expanduser("~"), "Vectrax", "vault"),
    )


def _dir() -> str:
    return os.path.join(_vault_dir(), "domain_verification")


def _path(domain: str) -> str:
    return os.path.join(_dir(), f"{domain}.jsonl")


# ── Escritura ──────────────────────────────────────────────────────────

def record_outcome(outcome: Outcome) -> bool:
    """Persiste un Outcome verificado (append JSONL). Ignora PENDING (nada
    verificado que guardar). Nunca lanza; devuelve True si escribió."""
    if outcome.status is OutcomeStatus.PENDING:
        return False
    try:
        with _lock:
            os.makedirs(_dir(), exist_ok=True)
            with open(_path(outcome.domain), "a", encoding="utf-8") as f:
                f.write(json.dumps(outcome.to_dict(), ensure_ascii=False) + "\n")
        return True
    except Exception as exc:  # nunca rompe el ciclo llamador
        logger.debug("verification_ledger record failed: %s", exc)
        return False


def record_many(outcomes) -> int:
    return sum(1 for o in outcomes if record_outcome(o))


# ── Lectura ────────────────────────────────────────────────────────────

def _parse_outcomes_file(path: str, domain: str) -> List[Outcome]:
    """Parse the full JSONL file (no filtering). Pure I/O + parse; caching and
    filtering live in ``load_outcomes``/``_load_all_outcomes_cached``."""
    out: List[Outcome] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                try:
                    status = OutcomeStatus(d.get("status", "neutral"))
                except ValueError:
                    status = OutcomeStatus.NEUTRAL
                out.append(Outcome(
                    prediction_id=d.get("prediction_id", ""),
                    domain=d.get("domain", domain),
                    subject=d.get("subject", ""),
                    status=status,
                    score=float(d.get("score", 0.0) or 0.0),
                    resolved_ts=float(d.get("resolved_ts", 0.0) or 0.0),
                    evidence=d.get("evidence", {}) or {},
                ))
    except Exception as exc:
        logger.debug("verification_ledger load failed: %s", exc)
    return out


def _load_all_outcomes_cached(domain: str) -> List[Outcome]:
    """Full, unfiltered outcome list for ``domain``, served from the in-memory
    cache when the file's mtime hasn't changed since it was last parsed.

    Keyed by the resolved file path (not just ``domain``) so tests/tools that
    point ``VECTRAX_VAULT_DIR`` at different directories within the same
    process never share a cache entry across different underlying files.
    """
    path = _path(domain)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        with _load_cache_lock:
            _load_cache.pop(path, None)
        return []
    with _load_cache_lock:
        cached = _load_cache.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]
    outcomes = _parse_outcomes_file(path, domain)
    with _load_cache_lock:
        _load_cache[path] = (mtime, outcomes)
    return outcomes


def load_outcomes(domain: str, subject: Optional[str] = None,
                  limit: Optional[int] = None) -> List[Outcome]:
    """Carga Outcomes verificados de un dominio (opcionalmente filtrados por
    subject, y limitados a los ``limit`` más recientes)."""
    out = _load_all_outcomes_cached(domain)
    if subject is not None:
        out = [o for o in out if o.subject == subject]
    if limit and limit > 0:
        return out[-limit:]
    return list(out)


# ── Agregación (núcleo invariante) ─────────────────────────────────────

def domain_score(domain: str, baseline: float = 0.5) -> DomainScore:
    """DomainScore real del dominio a partir de sus Outcomes verificados."""
    return score_outcomes(domain, load_outcomes(domain), baseline=baseline)


def subject_scores(domain: str, min_decisive: int = 1,
                   baseline: float = 0.5) -> Dict[str, DomainScore]:
    """DomainScore por subject (p. ej. lane/carrier), para ver QUÉ entidades
    tienen criterio validado. Solo devuelve las que superan ``min_decisive``."""
    by_subject: Dict[str, List[Outcome]] = {}
    for o in load_outcomes(domain):
        by_subject.setdefault(o.subject, []).append(o)
    scores: Dict[str, DomainScore] = {}
    for subj, outs in by_subject.items():
        sc = score_outcomes(domain, outs, baseline=baseline)
        if sc.n_decisive >= min_decisive:
            scores[subj] = sc
    return scores


def clear_domain(domain: str) -> None:
    """Solo para tests / reinicio controlado."""
    try:
        path = _path(domain)
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
