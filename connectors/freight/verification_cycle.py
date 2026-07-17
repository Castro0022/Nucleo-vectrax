"""
connectors/freight/verification_cycle.py — Cierre del ciclo VERIFICADO de freight.

Convierte los eventos freight REALIZADOS con verdad objetiva
(``delivery_complete`` con ``on_time``, ``delay_reported``) en Outcomes
verificados vía ``FreightOutcomeAdapter``, los persiste en el
``verification_ledger`` genérico y devuelve el ``DomainScore`` REAL (WR/accuracy
sobre entregas a tiempo por lane/carrier).

Esto cierra la mitad que a freight le faltaba: la verificación contra la verdad
del dominio, reemplazando el proxy de coherencia como fuente de desempeño.

Aditivo: NO toca ingest / elevación / criterio. Solo lee eventos y escribe en
su propio ledger de verificación. El núcleo de scoring es el invariante común.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Mapping

from core.learn.outcome_adapter import (
    DomainScore,
    Outcome,
    OutcomeStatus,
    Prediction,
    score_outcomes,
)
from core.learn import verification_ledger as vledger
from connectors.freight.freight_outcome_adapter import FreightOutcomeAdapter

logger = logging.getLogger("vectrax.freight.verification_cycle")

_DOMAIN = "freight_logistics"
_ADAPTER = FreightOutcomeAdapter()

# Solo estos eventos portan verdad objetiva de resultado.
_OUTCOME_EVENTS = ("delivery_complete", "delay_reported")


def _event_type(ev: Any) -> str:
    et = getattr(ev, "event_type", None)
    if et is None and isinstance(ev, Mapping):
        et = ev.get("event_type")
    return str(et or "").lower()


def _event_data(ev: Any) -> Dict[str, Any]:
    data = getattr(ev, "data", None)
    if data is None and isinstance(ev, Mapping):
        data = ev.get("data")
    return dict(data) if isinstance(data, Mapping) else {}


def _subject(data: Mapping[str, Any]) -> str:
    """Entidad verificable: lane|carrier (lo que el criterio evalúa)."""
    region = str(data.get("region") or "").strip()
    carrier = str(data.get("carrier") or "").strip()
    if region and carrier:
        return f"{region}|{carrier}"
    return region or carrier or "unknown"


def verify_events(events: Iterable[Any], record: bool = True) -> DomainScore:
    """Resuelve los eventos de resultado de freight en Outcomes verificados.

    - Filtra a ``delivery_complete`` / ``delay_reported`` (los que tienen verdad).
    - subject = region|carrier; predicción favorable = "on_time".
    - Resuelve vía el mismo ``FreightOutcomeAdapter`` (núcleo invariante detrás).
    - Persiste los decisivos en el ledger (si ``record``).
    - Devuelve el DomainScore de ESTE lote (el acumulado está en el ledger).
    """
    outcomes: List[Outcome] = []
    for ev in events:
        et = _event_type(ev)
        if et not in _OUTCOME_EVENTS:
            continue
        data = _event_data(ev)
        observation = {"event_type": et, **data}
        pred = Prediction(
            domain=_DOMAIN,
            subject=_subject(data),
            predicted="on_time",
        )
        outcome = _ADAPTER.resolve(pred, observation)
        outcomes.append(outcome)
        if record and outcome.status is not OutcomeStatus.PENDING:
            vledger.record_outcome(outcome)
    score = score_outcomes(_DOMAIN, outcomes)
    logger.info(
        "freight.verification | batch=%d | decisive=%d | WR=%.0f%% | acc=%.2f",
        score.n_total, score.n_decisive, score.win_rate, score.accuracy,
    )
    return score


def verified_score() -> DomainScore:
    """DomainScore ACUMULADO (todas las verificaciones persistidas)."""
    return vledger.domain_score(_DOMAIN)


def verified_subjects(min_decisive: int = 3) -> Dict[str, DomainScore]:
    """Lanes/carriers con criterio VALIDADO (≥min_decisive resultados)."""
    return vledger.subject_scores(_DOMAIN, min_decisive=min_decisive)
