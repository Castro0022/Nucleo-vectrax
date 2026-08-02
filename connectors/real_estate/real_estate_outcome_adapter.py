"""
connectors/real_estate/real_estate_outcome_adapter.py — OutcomeAdapter del dominio.

Cierra la mitad de VERIFICACIÓN: convierte una predicción/criterio ("este
segmento se vende / mercado fuerte") en un Outcome verificado contra la verdad
objetiva del evento realizado. Mismo contrato que FreightOutcomeAdapter; el
núcleo de scoring (score_outcomes/DomainScore) es el invariante común.

Verdad objetiva del dominio (del evento realizado):
    sale_closed  → WIN   (score = sold/list - 1, el premium/descuento realizado)
    expired / withdrawn / cancelled → LOSS (-1)  (no se absorbió)
    cualquier otro evento (no terminal) → PENDING (aún sin verdad)

Prediction:
    subject   = segmento (zona|tipo|tier)
    predicted = etiqueta favorable, por defecto "sells"
observation:
    el evento realizado, p. ej.
      {"event_type": "sale_closed", "list_price": 500000, "sold_price": 520000, "dom": 40}
      {"event_type": "expired", "dom": 210}

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

_DOMAIN = "florida_real_estate"

_NEGATIVE = ("expired", "withdrawn", "cancelled")


class RealEstateOutcomeAdapter(OutcomeAdapter):
    """Verificación del dominio inmobiliario vía el desenlace realizado del listing."""

    @property
    def domain(self) -> str:
        return _DOMAIN

    def resolve(self, prediction: Prediction, observation: Mapping[str, Any]) -> Outcome:
        event_type = str(observation.get("event_type", "")).lower()
        status = str(observation.get("status", "")).lower()

        # 1. Desenlace negativo verificado: no se absorbió.
        if event_type in _NEGATIVE or status in _NEGATIVE:
            return self._outcome(
                prediction, OutcomeStatus.LOSS, score=-1.0,
                event_type=event_type or status or "expired",
                dom=observation.get("dom"),
            )

        # 2. Venta cerrada = verdad objetiva positiva; score = premium/descuento.
        if event_type == "sale_closed" or status in ("closed", "sold"):
            list_price = _num(observation.get("list_price"))
            sold_price = _num(observation.get("sold_price"))
            ratio = _num(observation.get("sale_to_list"))
            if ratio is None and list_price and sold_price and list_price > 0:
                ratio = sold_price / list_price
            score = round((ratio - 1.0), 4) if ratio is not None else 0.0
            return self._outcome(
                prediction, OutcomeStatus.WIN, score=score,
                event_type="sale_closed",
                sale_to_list=round(ratio, 4) if ratio is not None else None,
                dom=observation.get("dom"),
            )

        # 3. Sin desenlace todavía → pendiente de verificar.
        return self._outcome(
            prediction, OutcomeStatus.PENDING,
            reason="sin desenlace terminal (venta/expiración) aún",
            event_type=event_type or "none",
        )


def _num(v: Any):
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
