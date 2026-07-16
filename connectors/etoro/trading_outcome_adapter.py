"""
connectors/etoro/trading_outcome_adapter.py — OutcomeAdapter del dominio market.

Envuelve el clasificador CANÓNICO ``outcome_tracker.classify_outcome`` (única
fuente de verdad win/loss/neutral/expired) tras el contrato genérico
``OutcomeAdapter``. NO duplica la lógica de clasificación ni modifica el
outcome_tracker existente — solo lo adapta al núcleo invariante.

Verdad objetiva del dominio: el precio realizado (Binance/eToro).

Prediction:
    subject   = símbolo (p. ej. "BTC")
    predicted = dirección recomendada ("buy" | "sell")
    context   = {"direction", "entry_price", "invalidation_price"}
observation:
    {"current_price": float, "window_expired": bool}

Creador: Mario Bravo Castro
"""
from __future__ import annotations

from typing import Any, Mapping

from core.learn.outcome_adapter import (
    Outcome,
    OutcomeAdapter,
    OutcomeStatus,
    Prediction,
)

_DOMAIN = "market"

# Mapea el status canónico del outcome_tracker al estado universal.
# 'expired' = ventana vencida sin movimiento decisivo → NEUTRAL.
_STATUS_MAP = {
    "win": OutcomeStatus.WIN,
    "loss": OutcomeStatus.LOSS,
    "neutral": OutcomeStatus.NEUTRAL,
    "expired": OutcomeStatus.NEUTRAL,
}


class TradingOutcomeAdapter(OutcomeAdapter):
    """Verificación de dominio market vía el clasificador real de eToro."""

    @property
    def domain(self) -> str:
        return _DOMAIN

    def resolve(self, prediction: Prediction, observation: Mapping[str, Any]) -> Outcome:
        ctx = prediction.context or {}
        direction = str(ctx.get("direction") or prediction.predicted or "buy").lower()
        try:
            entry = float(ctx.get("entry_price", 0.0) or 0.0)
        except (TypeError, ValueError):
            entry = 0.0
        current = observation.get("current_price")

        # Aún no verificable: falta precio realizado o entrada válida.
        if current is None or entry <= 0:
            return self._outcome(
                prediction, OutcomeStatus.PENDING,
                reason="sin precio realizado o entrada inválida",
            )

        invalidation = ctx.get("invalidation_price")
        try:
            invalidation_px = float(invalidation) if invalidation not in (None, "") else None
        except (TypeError, ValueError):
            invalidation_px = None
        window_expired = bool(observation.get("window_expired", False))

        # Única fuente de verdad — sin re-implementar win/loss.
        from connectors.etoro.outcome_tracker import classify_outcome
        status, ret_pct, sl_touched = classify_outcome(
            direction=direction,
            entry_price=entry,
            current_price=float(current),
            invalidation_price=invalidation_px,
            window_expired=window_expired,
        )
        return self._outcome(
            prediction,
            _STATUS_MAP.get(status, OutcomeStatus.NEUTRAL),
            score=round(ret_pct, 4),
            raw_status=status,
            sl_touched=sl_touched,
            direction=direction,
            entry_price=entry,
            current_price=float(current),
        )
