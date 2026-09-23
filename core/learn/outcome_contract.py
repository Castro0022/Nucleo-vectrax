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
from dataclasses import dataclass, field, replace
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
    #: Quién APLICA la evidencia: el ciclo. Distinto de su procedencia.
    source: str
    #: QUÉ MIDE este dominio: la unidad que cuenta una evidencia suya.
    #: freight cuenta entregas, market señales, cybersecurity CVE. No tienen
    #: por qué parecerse; lo que el núcleo exige es que esté DICHO, porque de
    #: eso depende poder leer la evidencia sin suponer qué representa.
    unit: str
    #: Identidad del resultado a partir del ítem de origen (evento o señal).
    identify: Callable[[Any], str]
    #: ¿Puede la fuente volver a presentar el ítem en un ciclo posterior?
    replayable: bool
    #: Estrella a la que pertenece un resultado. `None` = no alimenta gravedad.
    star_for: Optional[Callable[[Outcome], Optional[str]]] = None
    #: ¿Una escritura posterior supersede a la anterior? (cybersecurity)
    supersedes: bool = False
    #: ¿La fuente tiene un MARCADOR que impide volver a presentar el ítem?
    #:
    #: market lo tiene (`market_verified.json`) y cybersecurity también
    #: (`seen_ledger`, que al marcar una CVE hace que deje de figurar como
    #: nueva o cambiada). Son la misma cosa con dos nombres, y la regla es
    #: la misma: el marcador se confirma DESPUÉS de la escritura durable,
    #: nunca antes. Confirmarlo antes convierte `replayable=True` en mentira
    #: —la fuente puede releerse, pero el marcador ya la excluyó— y es
    #: exactamente cómo se perdía un resultado en los dos dominios.
    #:
    #: Declararlo aquí permite exigir esa garantía en la conformidad, en vez
    #: de confiar en que cada ciclo la escriba bien por su cuenta.
    confirms: bool = False

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
    #: De dónde venía el dato de este lote.
    origin: str = ""
    gravity: Mapping[str, int] = field(default_factory=dict)
    #: `None` si el dominio no tiene marcador; si lo tiene, si quedó escrito.
    confirmed: Optional[bool] = None

    @property
    def gravity_applied(self) -> int:
        return int(self.gravity.get(outcome_gravity.APPLIED, 0))


# ── La secuencia durable, UNA sola vez para todos los dominios ────────

def commit(
    contract: DomainContract,
    outcomes: Sequence[Outcome],
    *,
    record: bool = True,
    origin: Optional[str] = None,
    confirm: Optional[Callable[[Sequence[str]], bool]] = None,
) -> CommitReport:
    """Persiste un lote de resultados verificados cumpliendo el contrato.

    Orden y razones:

    1. Se apartan los resultados SIN identidad. No se escriben en ninguna
       parte y quedan en el log como WARNING: una fila sin identidad es
       indistinguible de otra y rompe toda deduplicación posterior.
    1.b Se sella la PROCEDENCIA de cada evidencia (`origin`): de dónde venía
       el dato. `origin` es del LOTE porque un ciclo lee de un proveedor por
       vez. Sin esto, la evidencia de un simulador y la de un feed real son
       indistinguibles en el ledger, y el núcleo no puede decidir qué
       significa lo observado — aprendería de datos simulados como si fueran
       reales. Es lo mismo que la identidad: el dominio lo declara, el núcleo
       lo exige y lo registra.
    2. Se lleva el lote a la gravedad (si el dominio la alimenta) y se
       comprueba con `accounted()` que el recuento cubre el lote entero.
    3. El ledger se escribe según `replayable` (ver el encabezado del módulo).
       La deduplicación por identidad se aplica salvo en dominios con
       `supersedes=True`.
    4. Y SOLO ENTONCES se confirma el marcador de la fuente, con los ítems
       que quedaron escritos y con ninguno más. Esta es la razón de que
       `confirm` se pase aquí en vez de llamarlo el ciclo: el orden es la
       garantía, y un orden que cada dominio escribe a mano es un orden que
       algún dominio escribirá al revés. Ya pasó dos veces —market marcaba el
       lote entero, cybersecurity marcaba la CVE antes de escribir su
       resultado— y en ambos casos el resultado se perdía para siempre pese a
       que la fuente podía releerse.

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

    where_from = str(origin or contract.source).strip() or contract.source
    with_identity = [
        replace(o, evidence={
            **dict(o.evidence), "origin": where_from, "unit": contract.unit,
        })
        for o in with_identity
    ]

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
            no_identity=no_identity, origin=where_from, gravity=gravity_counts,
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

    confirmed = _confirm(contract, confirm, handled)
    return CommitReport(
        domain=contract.domain, total=total, handled_ids=tuple(handled),
        accounted=gravity_ok and len(handled) == len(with_identity),
        ledger_written=written, ledger_skipped=skipped,
        no_identity=no_identity, origin=where_from,
        gravity=gravity_counts, confirmed=confirmed,
    )


def _confirm(
    contract: DomainContract,
    confirm: Optional[Callable[[Sequence[str]], bool]],
    handled: Sequence[str],
) -> Optional[bool]:
    """Marca en la fuente los ítems que YA quedaron escritos, y solo esos.

    Se llama en un único punto, al final de `commit()`, para que ningún
    dominio pueda adelantarlo. Si el marcador falla no se pierde nada: los
    ítems se volverán a presentar y la deduplicación por identidad evita que
    se escriban dos veces.
    """
    if confirm is None:
        if contract.confirms:
            logger.warning(
                "%s | declara marcador de fuente pero no lo pasó a commit(): "
                "sus ítems podrían volver a presentarse indefinidamente",
                contract.domain,
            )
        return None
    if not handled:
        return True
    try:
        ok = bool(confirm(tuple(handled)))
    except Exception as exc:
        logger.warning("%s | el marcador de fuente falló: %s", contract.domain, exc)
        ok = False
    if not ok:
        logger.warning(
            "%s | %d ítems quedaron sin marcar: se volverán a presentar y la "
            "deduplicación por identidad evitará que se escriban dos veces",
            contract.domain, len(handled),
        )
    return ok


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
