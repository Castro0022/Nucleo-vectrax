"""
connectors/freight/freight_outcome_adapter.py — OutcomeAdapter del dominio freight.

Cierra la mitad que a freight le faltaba: la VERIFICACIÓN contra la verdad
objetiva real del dominio (¿la carga se entregó a tiempo?), en vez del proxy
de coherencia (WR = riqueza de campos × recurrencia) que usa hoy la elevación.

Verdad objetiva del dominio (del propio evento realizado):
    delivery_complete.on_time == True   → entrega a tiempo   → WIN (+1)
    delivery_complete.on_time == False  → entrega tardía     → LOSS (-1)
    delay_reported (o delayed=True)     → retraso confirmado → LOSS (-delay_h)
    sin evento de resultado todavía     → PENDING

Prediction:
    subject   = patrón/lane/carrier (p. ej. "Southeast|FastHaul")
    predicted = etiqueta favorable, por defecto "on_time"
observation:
    el evento realizado, p. ej.
      {"event_type": "delivery_complete", "on_time": true, "transit_days": 3}
      {"event_type": "delay_reported", "delay_hours": 12, "cause": "weather"}

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

_DOMAIN = "freight_logistics"


class FreightOutcomeAdapter(OutcomeAdapter):
    """Verificación de dominio freight vía el resultado de entrega realizado."""

    @property
    def domain(self) -> str:
        return _DOMAIN

    def resolve(self, prediction: Prediction, observation: Mapping[str, Any]) -> Outcome:
        event_type = str(observation.get("event_type", "")).lower()

        # 1. Retraso reportado = resultado adverso verificado.
        if event_type == "delay_reported" or observation.get("delayed") is True:
            try:
                delay_h = float(observation.get("delay_hours", 0.0) or 0.0)
            except (TypeError, ValueError):
                delay_h = 0.0
            return self._outcome(
                prediction, OutcomeStatus.LOSS,
                score=-abs(delay_h) if delay_h else -1.0,
                event_type=event_type or "delay_reported",
                delay_hours=delay_h,
                cause=observation.get("cause"),
            )

        # 2. Entrega completada = verdad objetiva por `on_time`.
        if event_type == "delivery_complete" or "on_time" in observation:
            on_time = observation.get("on_time")
            if on_time is None:
                return self._outcome(
                    prediction, OutcomeStatus.PENDING,
                    reason="delivery sin campo on_time",
                )
            if bool(on_time):
                return self._outcome(
                    prediction, OutcomeStatus.WIN, score=1.0,
                    event_type="delivery_complete", on_time=True,
                    transit_days=observation.get("transit_days"),
                )
            return self._outcome(
                prediction, OutcomeStatus.LOSS, score=-1.0,
                event_type="delivery_complete", on_time=False,
                transit_days=observation.get("transit_days"),
            )

        # 3. Sin evento de resultado todavía → pendiente de verificar.
        return self._outcome(
            prediction, OutcomeStatus.PENDING,
            reason="sin evento de resultado (delivery/delay) aún",
            event_type=event_type or "none",
        )
