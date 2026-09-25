"""
connectors/etoro/legacy_criterion_engine.py — sustituto TEMPORAL de
TradeDecisionEngine.

`position_manager.py` ya no evalúa criterio directamente (ver commit de
migración a la máquina de estados pura en `core/trading/position_state.py`
+ `core/trading/risk_gate.py`). `_check_coherence_loss` y
`_check_contrary_signal` —junto con take-profit y expiración por
tiempo— salieron de ahí tal cual, sin reinventarlas, y aterrizaron aquí:
un proveedor de `TradeDecision` que reproduce EXACTAMENTE el
comportamiento anterior, para no perder cobertura de salida mientras
`TradeDecisionEngine` (Núcleo real, con evidencia/memoria) no existe
todavía.

Este módulo es un STAND-IN, no la arquitectura final:
  - No usa `EntryThesis`/`invalidation_conditions` reales — no existen
    para trades legacy (creados antes de que `TradeDecisionEngine`
    generara una tesis en el ENTER).
  - El día que `TradeDecisionEngine` exista, `position_manager.py` deja
    de llamar a `propose_decision()` de aquí y este archivo se retira
    completo — no se migra ni se extiende.

`RiskGate` es evaluado ANTES que este módulo (ver `position_manager.py`):
el stop duro ya no vive aquí, vive en `core/trading/risk_rules.py`.

Creado: 2026-09-25
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from connectors.etoro.auto_executor import PaperTrade
from core.trading.contracts import TradeAction, TradeDecision

logger = logging.getLogger("vectrax.etoro.legacy_criterion_engine")


def propose_decision(
    trade: PaperTrade,
    current_price: Optional[float],
    max_hold_hours: float,
    now: Optional[float] = None,
) -> TradeDecision:
    """Reproduce la lógica de `_evaluate_exit` del `position_manager.py`
    anterior, MENOS el stop_loss (ahora es responsabilidad exclusiva de
    RiskGate — `check_open_position` ya se evaluó antes de llegar aquí)."""
    now = time.time() if now is None else now
    elapsed_h = (now - trade.timestamp) / 3600

    if elapsed_h >= max_hold_hours:
        return TradeDecision(
            action=TradeAction.EXIT,
            position_id=trade.trade_id,
            reason=f"tiempo_expirado ({elapsed_h:.1f}h >= {max_hold_hours}h)",
            confidence=0.5,
        )

    if current_price is None:
        return TradeDecision(
            action=TradeAction.HOLD,
            position_id=trade.trade_id,
            reason="sin precio actual — no se puede evaluar criterio",
            confidence=0.0,
        )

    if trade.direction == "buy":
        take_profit_hit = current_price >= trade.take_profit
    else:
        take_profit_hit = current_price <= trade.take_profit
    if take_profit_hit:
        return TradeDecision(
            action=TradeAction.EXIT,
            position_id=trade.trade_id,
            reason=f"take_profit ({current_price:.5g} vs TP {trade.take_profit:.5g})",
            confidence=0.5,
        )

    if _check_coherence_loss(trade.symbol):
        return TradeDecision(
            action=TradeAction.EXIT,
            position_id=trade.trade_id,
            reason="coherencia_perdida (cc_score cayó en gravity engine)",
            confidence=0.5,
        )

    if _check_contrary_signal(trade.symbol, trade.direction):
        opposite = "sell" if trade.direction == "buy" else "buy"
        return TradeDecision(
            action=TradeAction.EXIT,
            position_id=trade.trade_id,
            reason=f"señal_contraria (nueva señal {opposite} detectada)",
            confidence=0.5,
        )

    return TradeDecision(
        action=TradeAction.HOLD,
        position_id=trade.trade_id,
        reason="tesis original (legacy) sigue vigente",
        confidence=0.5,
    )


def _check_coherence_loss(symbol: str) -> bool:
    """Check if gravity engine coherence score dropped significantly."""
    try:
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
        rec = gi.get(f"market:{symbol.upper()}")
        if rec and rec.cc_score < 0.1:
            return True
    except Exception:
        pass
    return False


def _check_contrary_signal(symbol: str, direction: str) -> bool:
    """Check if a contrary signal was recorded in last 2 hours."""
    try:
        from connectors.etoro.signal_recorder import load_signals
        opposite = "sell" if direction == "buy" else "buy"
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
