"""
Vectrax Failure Immunity — Nunca fallar por la misma razón dos veces
======================================================================
Registra cada fallo del sistema con su causa, genera una hipótesis
de protección, y cuando la hipótesis se confirma, crea una regla
que previene que ese fallo ocurra de nuevo.

Flujo:
  1. Algo falla → record_failure(category, cause, context)
  2. Si es la primera vez → registra + genera hipótesis de protección
  3. Si ya falló antes → incrementa contador + agrega evidencia a hipótesis
  4. Cuando hipótesis acumula suficiente evidencia → CONFIRMED → regla
  5. Regla activa → el sistema se protege automáticamente

Categorías de fallo:
  - gateway_stale: gateway perdió conexión
  - worker_hung: worker se colgó procesando
  - queue_overflow: cola se llenó
  - http_timeout: llamada HTTP excedió timeout
  - duplicate_flood: ráfaga de mensajes duplicados
  - memory_overflow: RAM excesiva
  - process_crash: proceso murió inesperadamente

Principio: Ley 6 (Causa y Efecto) + Ley 7 (Generación)

Creado: 2026-03-29
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.failure_immunity")

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "vault", "failure_immunity.db",
)

_CREATE = """
CREATE TABLE IF NOT EXISTS failures (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category        TEXT NOT NULL,
    cause           TEXT NOT NULL,
    context         TEXT DEFAULT '',
    count           INTEGER NOT NULL DEFAULT 1,
    first_seen      REAL NOT NULL,
    last_seen       REAL NOT NULL,
    hypothesis_id   TEXT DEFAULT '',
    rule_id         TEXT DEFAULT '',
    protected       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_failures_category ON failures(category);
CREATE UNIQUE INDEX IF NOT EXISTS idx_failures_unique ON failures(category, cause);
"""


def _conn():
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=3)
    conn.executescript(_CREATE)
    return conn


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def record_failure(
    category: str,
    cause: str,
    context: str = "",
) -> Dict[str, Any]:
    """
    Registra un fallo. Si es nuevo, genera hipótesis de protección.
    Si es repetido, incrementa contador y agrega evidencia.

    Returns dict with failure info + hypothesis_id if generated.
    """
    now = time.time()
    conn = _conn()

    try:
        # Check if we've seen this before
        row = conn.execute(
            "SELECT id, count, hypothesis_id, protected FROM failures "
            "WHERE category = ? AND cause = ?",
            (category, cause),
        ).fetchone()

        if row is None:
            # First time — register + generate hypothesis
            conn.execute(
                "INSERT INTO failures (category, cause, context, count, first_seen, last_seen) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (category, cause, context[:500], now, now),
            )
            conn.commit()
            conn.close()

            hyp_id = _generate_protection_hypothesis(category, cause, context)

            # Update with hypothesis ID
            if hyp_id:
                conn2 = _conn()
                conn2.execute(
                    "UPDATE failures SET hypothesis_id = ? WHERE category = ? AND cause = ?",
                    (hyp_id, category, cause),
                )
                conn2.commit()
                conn2.close()

            logger.warning(
                "NEW FAILURE: %s | %s | hypothesis=%s",
                category, cause[:60], hyp_id or "none",
            )

            return {
                "new": True,
                "category": category,
                "cause": cause,
                "count": 1,
                "hypothesis_id": hyp_id or "",
            }

        else:
            # Repeat failure — increment + add evidence
            failure_id, count, hyp_id, protected = row
            new_count = count + 1

            conn.execute(
                "UPDATE failures SET count = ?, last_seen = ?, context = ? "
                "WHERE id = ?",
                (new_count, now, context[:500], failure_id),
            )
            conn.commit()
            conn.close()

            # Add evidence to existing hypothesis
            if hyp_id:
                _add_failure_evidence(hyp_id, category, cause, new_count)

            logger.warning(
                "REPEAT FAILURE #%d: %s | %s | protected=%s",
                new_count, category, cause[:60], bool(protected),
            )

            return {
                "new": False,
                "category": category,
                "cause": cause,
                "count": new_count,
                "hypothesis_id": hyp_id or "",
                "protected": bool(protected),
            }

    except Exception as exc:
        logger.error("record_failure error: %s", exc)
        try:
            conn.close()
        except Exception:
            pass
        return {"error": str(exc)}


def is_protected(category: str, cause: str) -> bool:
    """Check if a specific failure type has an active protection rule."""
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT protected FROM failures WHERE category = ? AND cause = ?",
            (category, cause),
        ).fetchone()
        conn.close()
        return bool(row and row[0])
    except Exception:
        return False


def mark_protected(category: str, cause: str, rule_id: str) -> None:
    """Mark a failure type as protected (rule active)."""
    try:
        conn = _conn()
        conn.execute(
            "UPDATE failures SET protected = 1, rule_id = ? "
            "WHERE category = ? AND cause = ?",
            (rule_id, category, cause),
        )
        conn.commit()
        conn.close()
        logger.info("PROTECTED: %s | %s | rule=%s", category, cause[:60], rule_id)
    except Exception as exc:
        logger.warning("mark_protected failed: %s", exc)


# ---------------------------------------------------------------------------
# Hypothesis generation for failures
# ---------------------------------------------------------------------------

def _generate_protection_hypothesis(
    category: str, cause: str, context: str,
) -> Optional[str]:
    """Generate a hypothesis about how to protect against this failure."""
    try:
        from core.operator.hypothesis_engine import get_hypothesis_engine

        engine = get_hypothesis_engine()

        description = (
            f"El sistema experimentó un fallo de tipo '{category}': {cause}. "
            f"Se necesita una protección permanente que prevenga "
            f"que este fallo ocurra de nuevo."
        )

        hyp = engine.propose(
            description=description,
            context=f"Failure: {category} | {cause} | {context[:200]}",
            initial_confidence=0.4,
            tags=[
                f"failure:{category}",
                "failure_immunity",
                "learning_cycle",
            ],
            metadata={
                "failure_category": category,
                "failure_cause": cause,
                "source_pipeline": "failure_immunity",
            },
        )

        return hyp.id

    except Exception as exc:
        logger.warning("Failed to generate protection hypothesis: %s", exc)
        return None


def _add_failure_evidence(
    hypothesis_id: str, category: str, cause: str, count: int,
) -> None:
    """Add evidence of repeated failure to the hypothesis.

    Skips silently if the hypothesis is already resolved (CONFIRMED/REFUTED/
    ARCHIVED), avoiding the recurrent WARNING noise on stable failure patterns.
    """
    try:
        from core.operator.hypothesis_engine import (
            get_hypothesis_engine, Evidence, EvidenceType, HypothesisStatus,
        )

        engine = get_hypothesis_engine()

        # Idempotency guard: once a hypothesis is resolved we no longer push
        # repeat-failure evidence into it (the protection rule should be the
        # actor that gets revisited, not the original hypothesis).
        existing = engine._hypotheses.get(hypothesis_id) if hasattr(engine, "_hypotheses") else None
        if existing is not None and existing.status in (
            HypothesisStatus.CONFIRMED,
            HypothesisStatus.REFUTED,
            HypothesisStatus.ARCHIVED,
        ):
            logger.debug(
                "Skipping evidence for resolved hypothesis %s (status=%s, count=%d)",
                hypothesis_id, existing.status.value, count,
            )
            return

        engine.add_evidence(
            hypothesis_id,
            Evidence(
                evidence_type=EvidenceType.OBSERVATION,
                description=(
                    f"Fallo repetido #{count}: {category} — {cause}"
                ),
                weight=min(0.9, count * 0.15),
                supports=True,
                source="failure_immunity",
            ),
        )

        # Try to resolve (may confirm if enough evidence)
        engine.resolve(hypothesis_id)

    except Exception as exc:
        logger.debug("Failed to add failure evidence: %s", exc)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def failure_stats() -> Dict[str, Any]:
    """Get failure immunity statistics."""
    try:
        conn = _conn()
        total = conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
        protected = conn.execute(
            "SELECT COUNT(*) FROM failures WHERE protected = 1",
        ).fetchone()[0]
        repeat = conn.execute(
            "SELECT COUNT(*) FROM failures WHERE count > 1",
        ).fetchone()[0]
        top = conn.execute(
            "SELECT category, cause, count FROM failures "
            "ORDER BY count DESC LIMIT 5",
        ).fetchall()
        conn.close()

        return {
            "total_failure_types": total,
            "protected": protected,
            "unprotected": total - protected,
            "repeat_failures": repeat,
            "top_failures": [
                {"category": r[0], "cause": r[1][:60], "count": r[2]}
                for r in top
            ],
        }
    except Exception:
        return {}


def list_unprotected() -> List[Dict[str, Any]]:
    """List failure types that don't have protection yet."""
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT category, cause, count, hypothesis_id FROM failures "
            "WHERE protected = 0 ORDER BY count DESC",
        ).fetchall()
        conn.close()
        return [
            {"category": r[0], "cause": r[1], "count": r[2], "hypothesis_id": r[3]}
            for r in rows
        ]
    except Exception:
        return []
