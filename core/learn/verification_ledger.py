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

def load_outcomes(domain: str, subject: Optional[str] = None,
                  limit: Optional[int] = None) -> List[Outcome]:
    """Carga Outcomes verificados de un dominio (opcionalmente filtrados por
    subject, y limitados a los ``limit`` más recientes)."""
    path = _path(domain)
    if not os.path.exists(path):
        return []
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
                if subject is not None and d.get("subject") != subject:
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
    if limit and limit > 0:
        return out[-limit:]
    return out


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
