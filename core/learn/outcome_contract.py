"""
core/learn/outcome_contract.py — El contrato COMÚN de resultados verificados.

Por qué existe este módulo
--------------------------
El mismo defecto apareció cuatro veces, dominio a dominio, y cada vez se
corrigió donde apareció:

    market              un resultado sin estrella se perdía
    market              un fallo del almacén se daba por hecho
    market              un marcador fallido duplicaba el ledger
    freight_logistics   los tres otra vez, y la duplicación era peor

Corregirlos uno a uno era el error. Un dominio no puede heredar por accidente
una garantía que otro consiguió: o el contrato es único y se comprueba contra
TODOS los dominios a la vez, o el siguiente dominio que se añada repetirá los
mismos cuatro fallos. Este módulo es ese contrato, y
`tests/test_outcome_contract_conformance.py` lo exige a cada dominio
registrado — incluidos los que aún no existen.

El contrato, en tres requisitos que dependen unos de otros
----------------------------------------------------------
1. **Identidad estable del resultado.** Todo `Outcome` verificado lleva un
   `prediction_id` determinista: el mismo resultado da siempre el mismo id, y
   dos resultados distintos nunca colisionan. Es la PRECONDICIÓN de lo demás:
   sin identidad no hay deduplicación posible, ni en el ledger, ni en la
   gravedad, ni en la procedencia. `florida_real_estate` no la tenía y por eso
   duplicaba su ledger sin que ninguna protección compartida pudiera ayudarle.

2. **Confirmación de las escrituras necesarias.** Ninguna escritura se da por
   hecha sin comprobarla. `outcome_gravity.accounted(counts, expected)` exige
   que el recuento cubra el lote entero, no solo que nadie declarara un fallo.

3. **Reintento sin pérdida ni duplicación.** Lo que no se pudo escribir se
   recupera, y recuperarlo dos veces no cuenta dos veces.

La asimetría NO es por dominio: es una propiedad de la FUENTE
--------------------------------------------------------------
Durante varias revisiones traté como excepción de freight algo que es una
propiedad general: si la fuente puede volver a presentar el ítem, el dominio
puede retener las escrituras y repetir el lote entero; si no puede, retener
las escrituras no protege el resultado, lo pierde.

    replayable=True   market (la señal vive en `signal_recorder`),
                      cybersecurity (el backfill relee las CVE)
        -> ante un fallo de la gravedad NO se escribe el ledger y NO se marca
           nada: el ciclo siguiente repite el lote completo.

    replayable=False  freight_logistics, florida_real_estate (streaming: el
                      proveedor emite el evento una vez y no vuelve)
        -> se escribe SIEMPRE el ledger, que es durable e independiente, y la
           gravedad se pone al día después con
           `outcome_gravity.reconcile_from_ledger()`.

Una sola regla, decidida por un campo del contrato, en lugar de cuatro
comportamientos escritos a mano en cuatro conectores.

Lo que el contrato NO impone
----------------------------
`cybersecurity` reescribe a propósito un decisivo ya escrito cuando una CVE
vieja entra en KEV (LOSS -> WIN) y deduplica en la LECTURA quedándose con el
`resolved_ts` más reciente. Eso NO es un defecto y forzarle la deduplicación
del ledger lo rompería. El contrato lo admite con `supersedes=True`: ese
dominio queda exento de la deduplicación de escritura y conserva el resto de
requisitos, empezando por la identidad. Un contrato que solo sirve si todos
los dominios se parecen no es un contrato, es un molde.

`ai_provider` (`core/learn/provider_stars.py`) no está aquí a propósito: no
produce `Outcome` verificados contra la verdad de un dominio, sino que anota
el desenlace de sus propias llamadas en `outcome_history`. Es otra forma y
tiene otras garantías.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from core.learn import outcome_gravity
from core.learn import verification_ledger as vledger
from core.learn.outcome_adapter import Outcome, OutcomeStatus

logger = logging.getLogger("vectrax.outcome_contract")


# ── Identidad (requisito 1) ───────────────────────────────────────────

def derive_identity(
    event_type: str,
    data: Mapping[str, Any],
    source: str = "",
    ts: float = 0.0,
) -> str:
    """Identidad determinista de un evento que no trae identificador propio.

    Los dominios en streaming (freight, real estate) reciben eventos sin id.
    La identidad se deriva de TODO lo que define el evento —tipo, origen,
    instante y datos, con las claves ordenadas para que el JSON sea canónico—,
    así que el mismo evento da siempre el mismo id y dos eventos distintos no
    colisionan. No se inventa nada: es un resumen de lo que ya existe.

    Vive aquí, y no en cada conector, porque freight y real estate necesitan
    EXACTAMENTE la misma derivación. Dos copias divergen, y la que se quede
    atrás vuelve a duplicar sin que nadie lo note.

    Un dominio cuya fuente SÍ trae un identificador natural (el `signal_id` de
    market, el `cve_id` de cybersecurity) debe usar ese, no este resumen: es
    más estable frente a cambios de formato del payload.
    """
    try:
        payload = json.dumps(dict(data), sort_keys=True, default=str,
                             ensure_ascii=False)
    except Exception:
        payload = str(dict(data))
    raw = f"{event_type}|{source}|{float(ts or 0.0):.6f}|{payload}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


# ── El contrato ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class DomainContract:
    """Lo que un dominio declara para participar del ciclo de aprendizaje.

    `star_for` es `None` en los dominios que todavía no alimentan la gravedad
    con resultados verificados. No es un incumplimiento: el contrato cubre
    entonces identidad y ledger, y la conformidad se comprueba sobre lo que el
    dominio declara, no sobre lo que se supone que hace.
    """

    domain: str
    source: str
    #: Identidad del resultado a partir del ítem de origen (evento o señal).
    identify: Callable[[Any], str]
    #: ¿Puede la fuente volver a presentar el ítem en un ciclo posterior?
    replayable: bool
    #: Estrella a la que pertenece un resultado. `None` = no alimenta gravedad.
    star_for: Optional[Callable[[Outcome], Optional[str]]] = None
    #: ¿Una escritura posterior supersede a la anterior? (cybersecurity)
    supersedes: bool = False

    @property
    def feeds_gravity(self) -> bool:
        return self.star_for is not None


_REGISTRY: Dict[str, DomainContract] = {}


def register(contract: DomainContract) -> DomainContract:
    """Registra el contrato de un dominio. Idempotente por `domain`."""
    _REGISTRY[contract.domain] = contract
    return contract


def contract_for(domain: str) -> Optional[DomainContract]:
    return _REGISTRY.get(domain)


def registered_domains() -> Tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def all_contracts() -> Tuple[DomainContract, ...]:
    return tuple(_REGISTRY[d] for d in registered_domains())


def load_all() -> Tuple[DomainContract, ...]:
    """Importa cada ciclo de verificación para que registre su contrato.

    La conformidad se comprueba sobre el REGISTRO, así que el registro tiene
    que estar completo sin depender de qué haya importado quien pregunta.
    """
    for module in (
        "connectors.etoro.verification_cycle",
        "connectors.freight.verification_cycle",
        "connectors.real_estate.verification_cycle",
        "connectors.cybersecurity.verification_cycle",
    ):
        try:
            __import__(module)
        except Exception as exc:  # pragma: no cover - import defensivo
            logger.warning("outcome_contract: %s no registró contrato: %s",
                           module, exc)
    return all_contracts()


# ── El resultado de confirmar un lote ─────────────────────────────────

@dataclass(frozen=True)
class CommitReport:
    """Qué pasó EXACTAMENTE con un lote. Nada se da por hecho sin constancia."""

    domain: str
    total: int
    handled_ids: Tuple[str, ...] = ()
    accounted: bool = True
    ledger_written: int = 0
    ledger_skipped: int = 0
    no_identity: int = 0
    gravity: Mapping[str, int] = field(default_factory=dict)

    @property
    def gravity_applied(self) -> int:
        return int(self.gravity.get(outcome_gravity.APPLIED, 0))


# ── La secuencia durable, UNA sola vez para todos los dominios ────────

def commit(
    contract: DomainContract,
    outcomes: Sequence[Outcome],
    *,
    record: bool = True,
) -> CommitReport:
    """Persiste un lote de resultados verificados cumpliendo el contrato.

    Orden y razones:

    1. Se apartan los resultados SIN identidad. No se escriben en ninguna
       parte y quedan en el log como WARNING: una fila sin identidad es
       indistinguible de otra y rompe toda deduplicación posterior.
    2. Se lleva el lote a la gravedad (si el dominio la alimenta) y se
       comprueba con `accounted()` que el recuento cubre el lote entero.
    3. El ledger se escribe según `replayable` (ver el encabezado del módulo).
       La deduplicación por identidad se aplica salvo en dominios con
       `supersedes=True`.

    Devuelve un `CommitReport`. `handled_ids` son los que el dominio puede dar
    por cerrados —marcarlos, no volver a presentarlos—; en un dominio no
    reemitible es informativo, porque no hay nada que marcar.

    Nunca lanza.
    """
    total = len(outcomes)
    if total == 0:
        return CommitReport(domain=contract.domain, total=0)

    with_identity: List[Outcome] = []
    no_identity = 0
    for outcome in outcomes:
        if getattr(outcome, "prediction_id", ""):
            with_identity.append(outcome)
        else:
            no_identity += 1
    if no_identity:
        logger.warning(
            "%s | %d resultados SIN identidad: no se persisten. Un resultado "
            "sin `prediction_id` no puede deduplicarse ni recuperarse.",
            contract.domain, no_identity,
        )
    if not with_identity:
        return CommitReport(domain=contract.domain, total=total,
                            accounted=False, no_identity=no_identity)

    if not record:
        return CommitReport(
            domain=contract.domain, total=total, no_identity=no_identity,
            handled_ids=tuple(o.prediction_id for o in with_identity),
        )

    # 2. Gravedad.
    gravity_counts: Dict[str, int] = {}
    gravity_ok = True
    if contract.feeds_gravity:
        pairs: List[Tuple[str, Outcome]] = []
        for outcome in with_identity:
            try:
                fingerprint = contract.star_for(outcome)
            except Exception:
                fingerprint = None
            if fingerprint:
                pairs.append((fingerprint, outcome))
        if pairs:
            gravity_counts = outcome_gravity.apply_verified_outcomes(
                pairs, source=contract.source,
            )
            gravity_ok = outcome_gravity.accounted(gravity_counts, len(pairs))

    # 3. Ledger.
    if not gravity_ok and contract.replayable:
        # La fuente puede volver a presentar el lote entero: no se escribe
        # nada, así ledger, gravedad y marcador avanzan juntos o no avanzan.
        logger.warning(
            "%s | lote NO contabilizado (%d): no se escribe el ledger ni se "
            "marca nada; la fuente lo volverá a presentar",
            contract.domain,
            gravity_counts.get(outcome_gravity.FAILED, len(with_identity)),
        )
        return CommitReport(
            domain=contract.domain, total=total, accounted=False,
            no_identity=no_identity, gravity=gravity_counts,
        )

    already = (
        set() if contract.supersedes
        else outcome_gravity.ledger_prediction_ids(contract.domain)
    )
    handled: List[str] = []
    written = skipped = 0
    for outcome in with_identity:
        pid = outcome.prediction_id
        if pid in already:
            # Ya está en el ledger de una pasada anterior cuyo cierre no llegó
            # a completarse. Reescribirlo duplicaría la fila y el DomainScore
            # acumulado contaría las dos.
            skipped += 1
            handled.append(pid)
            continue
        if vledger.record_outcome(outcome):
            written += 1
            already.add(pid)
            handled.append(pid)
        else:
            # `record_outcome` no lanza: devuelve False. Ignorarlo dejaba el
            # ledger sin el resultado y el dominio dándolo por cerrado.
            logger.warning(
                "%s | el ledger rechazó %s: no se da por cerrado",
                contract.domain, pid,
            )

    if not gravity_ok:
        logger.warning(
            "%s | %d resultados no llegaron a la gravedad; están en el ledger "
            "y se recuperarán en el próximo ciclo",
            contract.domain,
            gravity_counts.get(outcome_gravity.FAILED, 0),
        )

    return CommitReport(
        domain=contract.domain, total=total, handled_ids=tuple(handled),
        accounted=gravity_ok and len(handled) == len(with_identity),
        ledger_written=written, ledger_skipped=skipped,
        no_identity=no_identity, gravity=gravity_counts,
    )


def recover(contract: DomainContract) -> Dict[str, int]:
    """Recupera lo que quedó atrás. Se llama al PRINCIPIO de cada ciclo.

    Dos fuentes, por este orden:

    * los resultados APARCADOS, cuya estrella no existía cuando llegaron;
    * los que están en el ledger sin haber llegado a la gravedad, porque el
      almacén no estaba disponible.

    Tiene que ir por delante de cualquier salida temprana del ciclo: un ciclo
    sin ítems nuevos sigue teniendo que recuperar lo pendiente. En market, el
    marcador de señales verificadas hace que una señal ya cerrada no se vuelva
    a presentar jamás, así que si la recuperación dependiera de que el ítem
    reapareciera, no ocurriría nunca.

    Nunca lanza.
    """
    totals: Dict[str, int] = {}
    if not contract.feeds_gravity:
        return totals
    try:
        retried = outcome_gravity.retry_pending(contract.domain)
        reconciled = outcome_gravity.reconcile_from_ledger(
            contract.domain, contract.star_for, source=contract.source,
        )
    except Exception as exc:  # pragma: no cover - defensivo
        logger.warning("%s | recuperación fallida: %s", contract.domain, exc)
        return totals
    for counts in (retried, reconciled):
        for key, value in counts.items():
            totals[key] = totals.get(key, 0) + int(value)
    return totals
