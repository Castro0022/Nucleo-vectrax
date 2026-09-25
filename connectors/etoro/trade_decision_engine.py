"""
connectors/etoro/trade_decision_engine.py — TradeDecisionEngine real.

Sustituye a `legacy_criterion_engine.py` (retirado en este mismo cambio).
Diferencia central: ya no recibe una `PaperTrade` cruda con un
`take_profit` fijo — lee una `EntryThesis` y evalúa sus
`invalidation_conditions` contra evidencia real. El take-profit de
precio fijo SALE por completo del criterio: decidir "ya ganó
suficiente" es exactamente el tipo de decisión que esta arquitectura
existe para devolverle a la evidencia, no a un número fijado al entrar.
Mientras no haya una señal de evidencia para eso, `HOLD` simplemente no
sale por ganancia — solo por invalidación de tesis o por el seguro de
tiempo máximo.

Contrato: `propose_decision(thesis, current_price, now) -> TradeDecision`.
Nunca produce `ENTER` — decidir si abrir una posición nueva es un
problema distinto (hoy resuelto por el pipeline de propuestas de
`learning_engine.py` / `execute_proposal()`), fuera del alcance de esta
función, que solo vigila una posición YA abierta.

Cadencia acordada: este motor corre cada ~10 min, DESPUÉS de que
`RiskGate` (cada ~1–5 min) ya dio `PASS` — nunca antes. Lee evidencia ya
calculada (gravity engine, señales) sin disparar aprendizaje pesado él
mismo; el recálculo de esas fuentes es responsabilidad de los ciclos
`learning_cycle`/`gravity_rebalance` que ya corren aparte
(`core/operator/decision_authority.py`).

Creado: 2026-09-25
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional, Tuple

from core.trading.contracts import EntryThesis, TradeAction, TradeDecision

logger = logging.getLogger("vectrax.etoro.trade_decision_engine")


def propose_decision(
    thesis: EntryThesis,
    current_price: Optional[float],
    now: Optional[float] = None,
    max_hold_hours: float = 24.0,
) -> TradeDecision:
    """Evalúa si la tesis con la que se entró sigue vigente.

    `max_hold_hours` es el seguro extremo, no "la salida": solo actúa si
    ninguna condición de invalidación se disparó antes — ver el diseño
    acordado (el límite de tiempo deja de ser el mecanismo principal de
    salida y pasa a ser un límite duro de última instancia).
    """
    now = time.time() if now is None else now
    elapsed_h = (now - thesis.created_at.timestamp()) / 3600

    if elapsed_h >= max_hold_hours:
        return TradeDecision(
            action=TradeAction.EXIT,
            position_id=thesis.position_id,
            reason=f"seguro_tiempo_maximo ({elapsed_h:.1f}h >= {max_hold_hours}h) — ninguna invalidación se disparó antes",
            confidence=0.4,
            evidence_refs=("time_safety_net",),
        )

    if current_price is None:
        return TradeDecision(
            action=TradeAction.HOLD,
            position_id=thesis.position_id,
            reason="sin precio actual — no se puede evaluar la tesis, se abstiene",
            confidence=0.0,
        )

    broken_condition, evidence_refs = _check_invalidation(thesis)
    if broken_condition is not None:
        return TradeDecision(
            action=TradeAction.EXIT,
            position_id=thesis.position_id,
            reason=f"tesis invalidada: {broken_condition}",
            confidence=0.6,
            evidence_refs=tuple(evidence_refs),
        )

    return TradeDecision(
        action=TradeAction.HOLD,
        position_id=thesis.position_id,
        reason=f"tesis original sigue vigente: {thesis.thesis}",
        confidence=thesis.confidence_at_entry,
        evidence_refs=thesis.evidence_refs,
    )


# ---------------------------------------------------------------------------
# Evaluación de invalidation_conditions contra evidencia real.
#
# Las condiciones son texto libre (fijado por quien creó la EntryThesis
# al ENTER); se reconocen por contenido, no por un enum cerrado, porque
# la evidencia que las respalda puede crecer sin tocar este archivo cada
# vez. Hoy solo hay dos evaluadores reales — los mismos que ya existían
# en legacy_criterion_engine.py, movidos aquí tal cual, ahora
# conectados a texto de invalidación real en vez de a un booleano
# hardcodeado.
# ---------------------------------------------------------------------------

def _check_invalidation(thesis: EntryThesis) -> Tuple[Optional[str], List[str]]:
    for condition in thesis.invalidation_conditions:
        lowered = condition.lower()
        if "cc_score" in lowered and _coherence_lost(thesis.symbol):
            return condition, [f"gravity:market:{thesis.symbol.upper()}"]
        if "contraria" in lowered and _contrary_signal_detected(thesis.symbol, thesis.side):
            return condition, [f"signal:{thesis.symbol.upper()}:contrary"]
    return None, []


def _coherence_lost(symbol: str) -> bool:
    """cc_score del gravity engine cayó por debajo del umbral."""
    try:
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
        rec = gi.get(f"market:{symbol.upper()}")
        if rec and rec.cc_score < 0.1:
            return True
    except Exception:
        pass
    return False


def _contrary_signal_detected(symbol: str, side: str) -> bool:
    """Señal OPERABLE en dirección contraria en las últimas 2h."""
    try:
        from connectors.etoro.signal_recorder import load_signals
        opposite = "sell" if side == "buy" else "buy"
        cutoff = time.time() - 7200  # 2h
        signals = load_signals()
        return any(
            s.symbol == symbol.upper()
            and s.direction == opposite
            and s.timestamp > cutoff
            and s.scenario == "OPERABLE"
            for s in signals
        )
    except Exception:
        return False
