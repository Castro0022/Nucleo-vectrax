"""
core/gravity/decay.py — Decay de memorias frías.

Degrada gradualmente la `gravity_score` de records que llevan tiempo
sin recuperarse, mientras protege estrictamente la identidad y los
principios. El ruido debe morir; la identidad debe persistir.

Reglas:

  AFECTA SI:
    - memory_type NO está en PROTECTED_MEMORY_TYPES
    - tags NO contienen 'person:*'
    - tags NO contienen 'principle' ni 'decision'
    - last_retrieved_at < now - DECAY_AFTER_DAYS (default 30d)
      O record es noise textual

  NO AFECTA:
    - DEEP / PERSON_RECOGNIZED / PRINCIPLE / DECISION (governor)
    - context_identities y essential_memories (tablas separadas)
    - principios del user (tag 'principle')

Cada ejecución reduce gravity_score por delta proporcional al tiempo
sin retrieval, con un piso configurable. Cuando gravity_score baja de
ARCHIVE_THRESHOLD el record pasa a memory_status='ARCHIVED' y deja
de aparecer en queries activas.

API:
    MemoryDecay(store, decay_after_days=30, decay_per_cycle=0.2,
                archive_threshold=0.05)
    .decay_cold_memories(user_id) -> int  (records afectados)
    .calculate_decay(record, now)  -> float (delta a aplicar, negativo)
    .apply_decay(record_id, delta) -> float (nuevo gravity_score)
"""

from __future__ import annotations

import datetime
import logging
import math
from typing import Any, Dict, Optional

from core.gravity.governor import PROTECTED_MEMORY_TYPES

logger = logging.getLogger("vectrax.gravity.decay")


# ===========================================================================
# Defaults
# ===========================================================================

DECAY_AFTER_DAYS = 30
DECAY_PER_CYCLE = 0.2          # delta base por ciclo
ARCHIVE_THRESHOLD = 0.05        # bajo este umbral, ARCHIVE
DECAY_MAX_DELTA = -1.0          # no más de -1.0 por aplicación (cap)


def _parse_iso(ts: str) -> Optional[datetime.datetime]:
    if not ts:
        return None
    try:
        clean = ts.rstrip("Z")
        return datetime.datetime.fromisoformat(clean)
    except Exception:
        return None


def _ts_to_dt(ts: float) -> datetime.datetime:
    try:
        return datetime.datetime.utcfromtimestamp(float(ts))
    except Exception:
        return datetime.datetime.utcnow()


# ===========================================================================
# MemoryDecay
# ===========================================================================

class MemoryDecay:
    """Aplicador de decay sobre memorias frías. No-destructivo."""

    def __init__(
        self,
        store,
        decay_after_days: int = DECAY_AFTER_DAYS,
        decay_per_cycle: float = DECAY_PER_CYCLE,
        archive_threshold: float = ARCHIVE_THRESHOLD,
    ) -> None:
        self.store = store
        self.decay_after_days = int(decay_after_days)
        self.decay_per_cycle = float(decay_per_cycle)
        self.archive_threshold = float(archive_threshold)

    # ----------------------------------------------------- batch entrypoint
    def decay_cold_memories(
        self,
        user_id: str,
        now: Optional[datetime.datetime] = None,
    ) -> int:
        """Aplica decay a los records elegibles del user.

        Devuelve el número de records afectados.
        """
        if not user_id:
            return 0
        now_dt = now or datetime.datetime.utcnow()
        affected = 0
        for record in self.store.list_active_for_user(user_id, limit=2000):
            if not self._is_eligible(record):
                continue
            delta = self.calculate_decay(record, now_dt)
            if delta >= 0:
                continue
            new_score = self.apply_decay(record["id"], delta)
            # Auto-archive si cae por debajo del umbral
            if new_score <= self.archive_threshold:
                self.store.set_status(record["id"], "ARCHIVED")
                logger.info(
                    "decay: archived %s (score=%.4f)", record["id"], new_score,
                )
            affected += 1
        return affected

    # --------------------------------------------------------- per-record
    def calculate_decay(
        self,
        record: Dict[str, Any],
        now: datetime.datetime,
    ) -> float:
        """Calcula el delta NEGATIVO a aplicar a gravity_score.

        Devuelve 0.0 si el record no debe degradarse en este ciclo.
        """
        # Determina cuándo fue la última actividad relevante.
        last_dt: Optional[datetime.datetime] = None
        if record.get("last_retrieved_at"):
            last_dt = _parse_iso(record["last_retrieved_at"])
        if last_dt is None:
            ts = record.get("ts")
            if ts:
                last_dt = _ts_to_dt(ts)
        if last_dt is None:
            return 0.0

        days_cold = (now - last_dt).days
        if days_cold < self.decay_after_days:
            return 0.0

        # Decay logarítmico: cuanto más tiempo sin retrieval, más fuerte
        # el delta, pero capped por DECAY_MAX_DELTA.
        excess_days = days_cold - self.decay_after_days
        factor = 1.0 + math.log1p(excess_days) * 0.4
        delta = -self.decay_per_cycle * factor
        return max(DECAY_MAX_DELTA, delta)

    def apply_decay(self, record_id: str, delta: float) -> float:
        """Aplica el delta directamente al gravity_score del record.

        Devuelve el nuevo gravity_score (>= 0). Si delta >= 0, no-op:
        devuelve el score actual sin modificarlo.
        """
        if delta >= 0:
            current = self.store.get_record(record_id) or {}
            return float(current.get("gravity_score", 0.0))
        return self.store.add_gravity_delta(record_id, delta)

    # --------------------------------------------------------- elegibilidad
    def _is_eligible(self, record: Dict[str, Any]) -> bool:
        """¿El record es elegible para decay?"""
        # Tipos protegidos
        if record.get("memory_type") in PROTECTED_MEMORY_TYPES:
            return False
        # Tags protegidos
        tags = [str(t) for t in (record.get("tags") or [])]
        if any(t.startswith("person:") for t in tags):
            return False
        if "principle" in tags or "decision" in tags:
            return False
        # FUSED ya no está activo y vive como evidencia de identity
        if record.get("memory_status") == "FUSED":
            return False
        return True
