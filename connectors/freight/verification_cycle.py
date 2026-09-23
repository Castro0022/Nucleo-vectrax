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

import hashlib
import json
import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional

from core.learn.outcome_adapter import (
    DomainScore,
    Outcome,
    OutcomeStatus,
    Prediction,
    score_outcomes,
)
from core.learn import outcome_gravity
from core.learn import verification_ledger as vledger
from connectors.freight.freight_outcome_adapter import FreightOutcomeAdapter

logger = logging.getLogger("vectrax.freight.verification_cycle")

_DOMAIN = "freight_logistics"
_ADAPTER = FreightOutcomeAdapter()

#: Quién aplicó el resultado, guardado en cada fila de procedencia.
_GRAVITY_SOURCE = "freight.verification_cycle"

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
    """Identidad estable de ESTE evento de resultado.

    `FreightEvent` no tiene id (ver `connectors/freight/base.py`: sus slots son
    event_type/data/source/ts), y `Prediction.prediction_id` quedaba en "". Sin
    identidad no hay deduplicación posible: re-verificar el mismo lote —que es
    lo que ocurre si un ciclo se reintenta— volvería a contar cada entrega en
    la historia acotada de la estrella, y además expulsaría resultados
    distintos.

    Se deriva de TODO lo que define el evento (tipo, origen, instante y datos,
    con las claves ordenadas para que el JSON sea canónico), así que el mismo
    evento da siempre el mismo id y dos eventos distintos no colisionan. No se
    inventa nada: es un resumen del evento que ya existe, no un dato nuevo.
    """
    try:
        payload = json.dumps(_event_data(ev), sort_keys=True, default=str,
                             ensure_ascii=False)
    except Exception:
        payload = str(_event_data(ev))
    raw = f"{_event_type(ev)}|{_event_source(ev)}|{_event_ts(ev):.6f}|{payload}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


#: El evento cuya estrella describe la SITUACIÓN comprometida (la reserva de
#: carga), firmada por ``region|carrier`` en el template del dominio — que es
#: EXACTAMENTE el `subject` que verifica este ciclo.
_SITUATION_EVENT = "load_booking"

#: Campos que son el RESULTADO, no la situación. Nunca pueden formar parte de
#: la identidad de la estrella a la que se acredita un veredicto (ver
#: `star_fingerprint_for`).
_OUTCOME_FIELDS = ("on_time", "delay_hours", "transit_days", "cause")


def star_fingerprint_for(data: Mapping[str, Any]) -> Optional[str]:
    """La estrella a la que pertenece este resultado — y por qué NO es la suya.

    EL DESAJUSTE
    ------------
    El `subject` que verifica este ciclo es ``region|carrier``, mientras que la
    estrella de freight se identifica como
    ``{dominio}:{tipo}:{firma_de_condiciones}``. No son el mismo espacio de
    identidad, así que anotar el resultado "en el subject" habría ido a una
    estrella inexistente y se habría descartado en silencio.

    POR QUÉ NO SE ACREDITA LA ESTRELLA DEL PROPIO EVENTO DE RESULTADO
    ------------------------------------------------------------------
    La respuesta aparentemente obvia —acreditar la estrella que la ingesta creó
    para ESTE evento— es incorrecta, y de la peor manera: silenciosamente
    productiva. El template del dominio firma ``delivery_complete`` por
    ``region|on_time`` y ``delay_reported`` por ``region|cause``. Es decir, el
    VEREDICTO ya forma parte de la identidad de esa estrella:

        freight_logistics:delivery_complete:region=Southeast|on_time=True
        freight_logistics:delivery_complete:region=Southeast|on_time=False

    La primera solo puede recibir "win" y la segunda solo "loss". Acreditarlas
    daría un win_rate del 100 % y del 0 % por construcción, y la del 100 %
    cruzaría MIN_WIN_RATE (55 %) siempre, sin haber aprendido nada: sería una
    tautología con aspecto de aprendizaje. Eso es fabricar resultados, que es
    justo lo que no se puede hacer para conseguir que aparezcan aprendizajes.

    LA ESTRELLA CORRECTA
    --------------------
    El resultado pertenece a la situación que lo PRECEDIÓ: la reserva de carga
    de ese ``region|carrier``, cuya estrella el template firma exactamente por
    ``region|carrier`` — la misma granularidad que ya usan
    `verified_subjects()` y el ranking del criterio para decir qué lane/carrier
    tiene criterio validado. Afirmar "las reservas de este tipo se entregaron a
    tiempo el X % de las veces" sí es una afirmación falsable.

    El fingerprint se calcula con LA MISMA función que creó la estrella
    (`core.domain_ingester.star_fingerprint`), sobre los campos ``region`` y
    ``carrier`` que el propio evento de resultado ya trae. Reimplementar la
    fórmula aquí reintroduciría la divergencia silenciosa que rompía el ciclo.

    Dos salvaguardas, ambas de fallo cerrado:

    * Los campos de resultado se retiran de los datos ANTES de calcular la
      identidad, de modo que un veredicto no pueda entrar en ella aunque el
      template cambie; si aun así apareciera en la firma, se devuelve None.
    * No se crea ninguna estrella. Si nunca hubo una reserva para ese
      lane/carrier, `record_verified_outcomes` devuelve False y el resultado
      queda sin aplicar (contado como ``no_star``), nunca inventado.
    """
    try:
        from core.domain_ingester import star_fingerprint
    except Exception as exc:  # pragma: no cover - import defensivo
        logger.warning("freight: no se pudo calcular el fingerprint: %s", exc)
        return None
    situation = {k: v for k, v in dict(data).items() if k not in _OUTCOME_FIELDS}
    if not situation.get("region") and not situation.get("carrier"):
        return None
    fp = star_fingerprint(_DOMAIN, _SITUATION_EVENT, situation)
    signature = fp.split(":", 2)[-1]
    if any(f"{field}=" in signature or f"{field}~" in signature
           for field in _OUTCOME_FIELDS):
        logger.warning(
            "freight: la identidad de la estrella contiene el resultado (%s); "
            "no se acredita", fp,
        )
        return None
    return fp


def verify_events(events: Iterable[Any], record: bool = True) -> DomainScore:
    """Resuelve los eventos de resultado de freight en Outcomes verificados.

    - Filtra a ``delivery_complete`` / ``delay_reported`` (los que tienen verdad).
    - subject = region|carrier; predicción favorable = "on_time".
    - Resuelve vía el mismo ``FreightOutcomeAdapter`` (núcleo invariante detrás).
    - Persiste los decisivos en el ledger (si ``record``).
    - Devuelve el DomainScore de ESTE lote (el acumulado está en el ledger).
    """
    outcomes: List[Outcome] = []
    to_gravity: List[tuple] = []
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
            prediction_id=_prediction_id(ev),
        )
        outcome = _ADAPTER.resolve(pred, observation)
        outcomes.append(outcome)
        if record and outcome.status is not OutcomeStatus.PENDING:
            vledger.record_outcome(outcome)
            fp = star_fingerprint_for(data)
            if fp:
                to_gravity.append((fp, outcome))
    fed = outcome_gravity.apply_verified_outcomes(
        to_gravity, source=_GRAVITY_SOURCE,
    ) if to_gravity else {}
    score = score_outcomes(_DOMAIN, outcomes)
    logger.info(
        "freight.verification | batch=%d | decisive=%d | WR=%.0f%% | acc=%.2f "
        "| gravity_applied=%d",
        score.n_total, score.n_decisive, score.win_rate, score.accuracy,
        fed.get(outcome_gravity.APPLIED, 0),
    )
    return score


def verified_score() -> DomainScore:
    """DomainScore ACUMULADO (todas las verificaciones persistidas)."""
    return vledger.domain_score(_DOMAIN)


def verified_subjects(min_decisive: int = 3) -> Dict[str, DomainScore]:
    """Lanes/carriers con criterio VALIDADO (≥min_decisive resultados)."""
    return vledger.subject_scores(_DOMAIN, min_decisive=min_decisive)
