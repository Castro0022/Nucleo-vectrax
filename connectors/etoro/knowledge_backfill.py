"""
connectors/etoro/knowledge_backfill.py — Releer la experiencia ya vivida.

Etapa 1 del plan aprobado: antes de capturar nada nuevo, Vectrax vuelve
sobre las señales que YA vivió (resueltas, con outcome conocido) y les
adjunta el conocimiento técnico formal (TA-Lib) del instante exacto en que
ocurrieron — "esto que estudié ahora corresponde a aquella experiencia que
ya tuve". El resultado (ganó/perdió/neutro) NO se toca ni se reinterpreta;
solo se describe técnicamente qué había en ese momento.

Deliberadamente NO hace en esta etapa:
  - no escribe en pattern_memory / gravity_engine / verification_ledger
  - no cambia entry_validator, gates, PAPER/LIVE ni ejecución
  - no decide relevancia de ningún feature (eso es la etapa 2, descubrimiento)

El conocimiento calculado se persiste en `connectors.etoro.knowledge_ledger`
(archivo append-only aparte, unido por `signal_id`) — nunca en el JSONL de
`signal_recorder`, que se reescribe entero en cada `update_signal()` y no
está pensado para cargar ~24KB de instantánea técnica por señal.

Es aditivo y reentrante: una señal cuyo `signal_id` ya tiene conocimiento en
el ledger no se recalcula salvo `force=True`. Nunca lanza — un fallo
puntual por señal se cuenta y se continúa con las demás.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger("vectrax.etoro.knowledge_backfill")


def _lived_experience_signals(force: bool) -> List[Any]:
    """Señales YA VIVIDAS: con outcome resuelto (no PENDING). Sin outcome
    no hay nada que releer todavía — esa señal sigue esperando su
    resultado, no su conocimiento."""
    from connectors.etoro.signal_recorder import load_signals, SignalStatus
    from connectors.etoro import knowledge_ledger

    resolved = [
        s for s in load_signals()
        if s.status != SignalStatus.PENDING.value
    ]
    if force:
        return resolved
    return [s for s in resolved if not knowledge_ledger.has_features(s.signal_id)]


def backfill_signal_knowledge(limit: int = 0, force: bool = False) -> Dict[str, Any]:
    """Adjunta conocimiento técnico retrospectivo a la experiencia ya vivida.

    Args:
        limit: máximo de señales a procesar en esta corrida (0 = todas las
               candidatas). Pensado para corridas por lotes controladas.
        force: recalcula incluso señales que ya tienen `features`.

    Returns: resumen {"candidates", "enriched", "insufficient_depth",
              "errors", "processed"} — nunca lanza.
    """
    from connectors.etoro import knowledge_ledger
    from connectors.etoro.knowledge_snapshot import snapshot_at

    candidates = _lived_experience_signals(force)
    if limit and limit > 0:
        candidates = candidates[:limit]

    summary = {
        "candidates": len(candidates),
        "processed": 0,
        "enriched": 0,
        "insufficient_depth": 0,
        "errors": 0,
    }

    for sig in candidates:
        try:
            features = snapshot_at(sig.symbol, sig.timestamp)
            any_available = any(
                tf_data.get("available") for tf_data in features.values()
            )
            all_shallow = all(
                tf_data.get("insufficient_depth", True) for tf_data in features.values()
            )

            ok = knowledge_ledger.record_features(sig.signal_id, features)
            summary["processed"] += 1
            if ok and any_available:
                summary["enriched"] += 1
            if all_shallow:
                summary["insufficient_depth"] += 1
            if not ok:
                summary["errors"] += 1
                logger.warning(
                    "[KNOWLEDGE_BACKFILL] no se pudo persistir features de %s",
                    sig.signal_id,
                )
        except Exception as exc:
            summary["errors"] += 1
            logger.warning(
                "[KNOWLEDGE_BACKFILL] fallo en %s (%s): %s",
                getattr(sig, "signal_id", "?"), getattr(sig, "symbol", "?"), exc,
            )

    logger.info(
        "[KNOWLEDGE_BACKFILL] candidatas=%d procesadas=%d enriquecidas=%d "
        "depth_insuficiente=%d errores=%d",
        summary["candidates"], summary["processed"], summary["enriched"],
        summary["insufficient_depth"], summary["errors"],
    )
    return summary
