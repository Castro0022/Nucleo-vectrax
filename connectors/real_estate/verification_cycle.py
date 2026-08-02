"""
connectors/real_estate/verification_cycle.py — Cierre VERIFICADO del dominio.

Convierte los eventos inmobiliarios con verdad objetiva (sale_closed / expired /
withdrawn / cancelled) en Outcomes verificados vía RealEstateOutcomeAdapter, los
persiste en el verification_ledger genérico y devuelve el DomainScore REAL
(win_rate/accuracy de absorción por segmento zona|tipo|tier).

Aditivo: NO toca ingest / elevación / criterio. Solo lee eventos y escribe en su
propio ledger. El núcleo de scoring es el invariante común (score_outcomes).

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
from connectors.real_estate.real_estate_outcome_adapter import RealEstateOutcomeAdapter

logger = logging.getLogger("vectrax.real_estate.verification_cycle")

_DOMAIN = "florida_real_estate"
_ADAPTER = RealEstateOutcomeAdapter()

# Solo estos eventos portan verdad objetiva de desenlace.
_OUTCOME_EVENTS = ("sale_closed", "expired", "withdrawn", "cancelled")


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
    """Segmento verificable: zona|tipo|tier (lo que el criterio evalúa)."""
    zone = str(data.get("zone") or data.get("zip") or "").strip()
    ptype = str(data.get("type") or "").strip()
    tier = str(data.get("tier") or "").strip()
    parts = [p for p in (zone, ptype, tier) if p]
    return "|".join(parts) if parts else "unknown"


def verify_events(events: Iterable[Any], record: bool = True) -> DomainScore:
    """Resuelve los eventos de desenlace en Outcomes verificados.

    - Filtra a los eventos terminales (los que portan verdad).
    - subject = zona|tipo|tier; predicción favorable = "sells".
    - Resuelve vía RealEstateOutcomeAdapter (núcleo invariante detrás).
    - Persiste los decisivos en el ledger (si record).
    - Devuelve el DomainScore de ESTE lote (el acumulado está en el ledger).
    """
    outcomes: List[Outcome] = []
    for ev in events:
        et = _event_type(ev)
        if et not in _OUTCOME_EVENTS:
            continue
        data = _event_data(ev)
        observation = {"event_type": et, **data}
        pred = Prediction(domain=_DOMAIN, subject=_subject(data), predicted="sells")
        outcome = _ADAPTER.resolve(pred, observation)
        outcomes.append(outcome)
        if record and outcome.status is not OutcomeStatus.PENDING:
            vledger.record_outcome(outcome)
    score = score_outcomes(_DOMAIN, outcomes)
    logger.info(
        "real_estate.verification | batch=%d | decisive=%d | WR=%.0f%% | acc=%.2f",
        score.n_total, score.n_decisive, score.win_rate, score.accuracy,
    )
    return score


def verified_score() -> DomainScore:
    """DomainScore ACUMULADO (todas las verificaciones persistidas)."""
    return vledger.domain_score(_DOMAIN)


def verified_subjects(min_decisive: int = 3) -> Dict[str, DomainScore]:
    """Segmentos con criterio VALIDADO (≥min_decisive desenlaces)."""
    return vledger.subject_scores(_DOMAIN, min_decisive=min_decisive)
