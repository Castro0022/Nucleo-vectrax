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
from core.learn import outcome_contract
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


def _event_source(ev: Any) -> str:
    src = getattr(ev, "source", None)
    if src is None and isinstance(ev, Mapping):
        src = ev.get("source")
    return str(src or "")


def _event_ts(ev: Any) -> float:
    ts = getattr(ev, "ts", None)
    if ts is None and isinstance(ev, Mapping):
        ts = ev.get("ts")
    try:
        return float(ts or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _prediction_id(ev: Any) -> str:
    """Identidad estable de ESTE evento de desenlace.

    Antes NO existía: `Prediction(...)` se construía sin `prediction_id`, así
    que quedaba en "" y todas las filas del ledger eran indistinguibles entre
    sí. Reproducido: el mismo evento procesado dos veces dejaba 2 filas y
    n_decisive=2, inflando el desempeño que alimenta el criterio. Y como la
    deduplicación compartida descarta los ids vacíos,
    `ledger_prediction_ids('florida_real_estate')` devolvía un conjunto vacío:
    ninguna protección podía ayudarle.

    `RealEstateEvent` no trae identificador propio (sus campos son
    event_type/data/ts), así que se deriva con la función COMPARTIDA
    `outcome_contract.derive_identity`, la misma que usa freight.
    """
    return outcome_contract.derive_identity(
        _event_type(ev), _event_data(ev), _event_source(ev), _event_ts(ev),
    )


#: EL CONTRATO DE ESTE DOMINIO.
#:
#: `replayable=False`: el proveedor (simulador, ATTOM, RESO/MLS) emite el
#: evento una vez. `star_for=None`: este dominio todavía NO alimenta la
#: gravedad con resultados verificados — el contrato cubre entonces identidad y
#: ledger, y la conformidad se comprueba sobre lo que declara, no sobre lo que
#: se supone. Conectarlo a la gravedad es un cambio aparte.
def _origin_kind(origin: str) -> str:
    """De qué tipo es esta procedencia de real estate.

    Igual que freight: el proveedor por defecto es el simulador
    (`REAL_ESTATE_FEED_PROVIDER`), y ATTOM o RentCast son observaciones
    reales del mercado.
    """
    name = str(origin or "").strip().lower()
    if not name:
        return outcome_contract.UNKNOWN
    if "sim" in name or name in ("test", "fixture"):
        return outcome_contract.SIMULATED
    return outcome_contract.REAL


CONTRACT = outcome_contract.register(outcome_contract.DomainContract(
    domain=_DOMAIN,
    source="real_estate.verification_cycle",
    unit="desenlace de listado verificado contra su cierre",
    identify=_prediction_id,
    origin_kind=_origin_kind,
    replayable=False,
))


def verify_events(events: Iterable[Any], record: bool = True) -> DomainScore:
    """Resuelve los eventos de desenlace en Outcomes verificados.

    - Filtra a los eventos terminales (los que portan verdad).
    - subject = zona|tipo|tier; predicción favorable = "sells".
    - Resuelve vía RealEstateOutcomeAdapter (núcleo invariante detrás).
    - Persiste los decisivos en el ledger (si record).
    - Devuelve el DomainScore de ESTE lote (el acumulado está en el ledger).
    """
    outcome_contract.recover(CONTRACT)

    outcomes: List[Outcome] = []
    evidences: List[outcome_contract.Evidence] = []
    # UNA sola pasada sobre `events`: puede ser un iterable consumible, y la
    # procedencia se lee del evento que la produjo, no del lote.
    for ev in events:
        et = _event_type(ev)
        if et not in _OUTCOME_EVENTS:
            continue
        data = _event_data(ev)
        observation = {"event_type": et, **data}
        item_id = _prediction_id(ev)
        pred = Prediction(
            domain=_DOMAIN, subject=_subject(data), predicted="sells",
            prediction_id=item_id,
        )
        outcome = _ADAPTER.resolve(pred, observation)
        outcomes.append(outcome)
        if outcome.status is not OutcomeStatus.PENDING:
            evidences.append(outcome_contract.evidence(
                item_id, _event_source(ev), outcome,
            ))

    report = outcome_contract.commit(CONTRACT, evidences, record=record)
    score = score_outcomes(_DOMAIN, outcomes)
    logger.info(
        "real_estate.verification | batch=%d | decisive=%d | WR=%.0f%% | "
        "acc=%.2f | ledger=%d (dup evitados=%d)",
        score.n_total, score.n_decisive, score.win_rate, score.accuracy,
        report.ledger_written, report.ledger_skipped,
    )
    return score


def verified_score() -> DomainScore:
    """DomainScore ACUMULADO (todas las verificaciones persistidas)."""
    return vledger.domain_score(_DOMAIN)


def verified_subjects(min_decisive: int = 3) -> Dict[str, DomainScore]:
    """Segmentos con criterio VALIDADO (≥min_decisive desenlaces)."""
    return vledger.subject_scores(_DOMAIN, min_decisive=min_decisive)
