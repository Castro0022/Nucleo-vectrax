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


# ── La unidad que entra al núcleo ─────────────────────────────────────

@dataclass(frozen=True)
class Evidence:
    """TODO lo que un ítem de origen produjo, junto y con su procedencia.

    POR QUÉ LA UNIDAD ES EL ÍTEM Y NO EL RESULTADO
    -----------------------------------------------
    Un ítem puede producir VARIOS resultados: una CVE deja uno por cada peldaño
    de su escalera de subjects. Cuando el núcleo recibía resultados sueltos no
    podía saber cuándo un ítem estaba COMPLETO, así que el marcador de la
    fuente se confirmaba en cuanto aterrizaba cualquiera de ellos. Si un nivel
    se escribía y otro fallaba, la CVE quedaba marcada con evidencia
    incompleta y no volvía a presentarse jamás: la parte que faltó se perdía
    para siempre.

    Con la evidencia como unidad, la regla se puede enunciar y comprobar:
    **un ítem se confirma cuando TODOS sus resultados quedaron escritos, y no
    antes**. Vale igual para un dominio 1:1 (market, freight, real estate),
    donde la evidencia tiene un solo resultado.

    POR QUÉ LA PROCEDENCIA VIAJA AQUÍ
    ----------------------------------
    Antes era del LOTE. Dos problemas: un lote que mezclara simulador y feed
    real le ponía a todo la misma procedencia combinada —haciendo
    indistinguible lo simulado de lo real, que es justo lo que la procedencia
    existe para evitar—; y obtenerla exigía recorrer los eventos una SEGUNDA
    vez, cosa que con un iterable consumible no devuelve nada. Viajando con
    cada evidencia se lee una sola vez, del evento que la produjo.
    """

    #: Identidad del ÍTEM de origen (la señal, el evento, la CVE).
    item_id: str
    #: De dónde vino ESE dato: el proveedor o feed concreto.
    origin: str
    #: Todos los resultados que ese ítem produjo.
    outcomes: Tuple[Outcome, ...]

    @property
    def complete(self) -> bool:
        return bool(self.item_id) and all(
            getattr(o, "prediction_id", "") for o in self.outcomes
        )


def evidence(item_id: str, origin: str, *outcomes: Outcome) -> Evidence:
    """Atajo legible para el caso 1:1, que es el de la mayoría de dominios."""
    return Evidence(item_id=str(item_id), origin=str(origin),
                    outcomes=tuple(outcomes))


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
    #: Ítems cuya evidencia quedó COMPLETA: los únicos confirmables.
    complete_items: Tuple[str, ...] = ()
    #: Ítems con parte de su evidencia sin escribir: NO se confirman.
    partial_items: Tuple[str, ...] = ()
    #: Procedencias vistas en este lote (puede haber varias).
    origins: Tuple[str, ...] = ()
    gravity: Mapping[str, int] = field(default_factory=dict)
    #: `None` si el dominio no tiene marcador; si lo tiene, si quedó escrito.
    confirmed: Optional[bool] = None

    @property
    def gravity_applied(self) -> int:
        return int(self.gravity.get(outcome_gravity.APPLIED, 0))


# ── La secuencia durable, UNA sola vez para todos los dominios ────────

def commit(
    contract: DomainContract,
    evidences: Sequence[Evidence],
    *,
    record: bool = True,
    confirm: Optional[Callable[[Sequence[str]], bool]] = None,
) -> CommitReport:
    """Persiste las EVIDENCIAS de un lote cumpliendo el contrato.

    La unidad es la evidencia de un ítem, no el resultado suelto: ver
    `Evidence`. De ahí salen los dos invariantes que el núcleo puede exigir.

    Orden y razones:

    1. Se aparta la evidencia INCOMPLETA —sin identidad de ítem, o con algún
       resultado sin `prediction_id`—. No se escribe nada de ella: una fila
       sin identidad es indistinguible de otra y rompe toda deduplicación.
    2. Se sella en cada resultado QUÉ MIDE (`unit`) y DE DÓNDE VINO
       (`origin`, el de SU evidencia). Sin eso el núcleo no puede decidir qué
       significa lo observado: aprendería de datos simulados como de reales.
    3. Se lleva el lote a la gravedad (si el dominio la alimenta) y
       `accounted()` comprueba que el recuento cubre el lote entero.
    4. El ledger se escribe según `replayable` (ver el encabezado del módulo).
       La deduplicación por identidad se aplica salvo con `supersedes=True`.
    5. Y SOLO ENTONCES se confirma el marcador de la fuente, con los ítems
       cuya evidencia quedó COMPLETA y con ninguno más. Un ítem al que le
       falte cualquiera de sus resultados no se confirma: volverá a
       presentarse, y la deduplicación por identidad impedirá que lo ya
       escrito se escriba dos veces.

    Que el orden y la completitud vivan aquí es la garantía. Un orden que cada
    dominio escribe a mano es un orden que algún dominio escribirá al revés:
    ya pasó dos veces (market marcaba el lote entero; cybersecurity marcaba la
    CVE antes de escribir su resultado) y las dos se perdía evidencia pese a
    que la fuente podía releerse.

    Nunca lanza.
    """
    total = sum(len(e.outcomes) for e in evidences)
    if total == 0:
        return CommitReport(domain=contract.domain, total=0)

    usable: List[Evidence] = []
    no_identity = 0
    for ev in evidences:
        if ev.complete:
            usable.append(ev)
        else:
            no_identity += len(ev.outcomes)
    if no_identity:
        logger.warning(
            "%s | %d resultados en evidencias INCOMPLETAS: no se persisten. "
            "Sin identidad de ítem o de resultado no hay deduplicación ni "
            "recuperación posibles.",
            contract.domain, no_identity,
        )
    if not usable:
        return CommitReport(domain=contract.domain, total=total,
                            accounted=False, no_identity=no_identity)

    # 2. Sellar unidad y procedencia. La procedencia es la de SU evidencia.
    stamped: List[Tuple[Evidence, List[Outcome]]] = []
    for ev in usable:
        where_from = str(ev.origin).strip() or contract.source
        stamped.append((ev, [
            replace(o, evidence={
                **dict(o.evidence), "origin": where_from,
                "unit": contract.unit,
            })
            for o in ev.outcomes
        ]))
    origins = tuple(sorted({
        str(ev.origin).strip() or contract.source for ev, _ in stamped
    }))
    flat = [o for _ev, outs in stamped for o in outs]

    if not record:
        return CommitReport(
            domain=contract.domain, total=total, no_identity=no_identity,
            handled_ids=tuple(o.prediction_id for o in flat),
            complete_items=tuple(ev.item_id for ev, _ in stamped),
            origins=origins,
        )

    # 3. Gravedad.
    gravity_counts: Dict[str, int] = {}
    gravity_ok = True
    if contract.feeds_gravity:
        pairs: List[Tuple[str, Outcome]] = []
        for outcome in flat:
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

    # 4. Ledger.
    if not gravity_ok and contract.replayable:
        # La fuente puede volver a presentar el lote entero: no se escribe
        # nada, así ledger, gravedad y marcador avanzan juntos o no avanzan.
        logger.warning(
            "%s | lote NO contabilizado (%d): no se escribe el ledger ni se "
            "marca nada; la fuente lo volverá a presentar",
            contract.domain,
            gravity_counts.get(outcome_gravity.FAILED, len(flat)),
        )
        return CommitReport(
            domain=contract.domain, total=total, accounted=False,
            no_identity=no_identity, origins=origins, gravity=gravity_counts,
        )

    already = (
        set() if contract.supersedes
        else outcome_gravity.ledger_prediction_ids(contract.domain)
    )
    handled: List[str] = []
    complete: List[str] = []
    partial: List[str] = []
    written = skipped = 0
    for ev, outs in stamped:
        landed = 0
        for outcome in outs:
            pid = outcome.prediction_id
            if pid in already:
                # Ya está en el ledger de una pasada anterior que no llegó a
                # cerrarse. Reescribirlo duplicaría la fila y el DomainScore
                # acumulado contaría las dos.
                skipped += 1
                landed += 1
                handled.append(pid)
                continue
            if vledger.record_outcome(outcome):
                written += 1
                landed += 1
                already.add(pid)
                handled.append(pid)
            else:
                # `record_outcome` no lanza: devuelve False.
                logger.warning(
                    "%s | el ledger rechazó %s", contract.domain, pid,
                )
        (complete if landed == len(outs) else partial).append(ev.item_id)

    if partial:
        logger.warning(
            "%s | %d ítems con evidencia INCOMPLETA no se confirman; volverán "
            "a presentarse y lo ya escrito no se duplicará: %s",
            contract.domain, len(partial), sorted(partial)[:5],
        )
    if not gravity_ok:
        logger.warning(
            "%s | %d resultados no llegaron a la gravedad; están en el ledger "
            "y se recuperarán en el próximo ciclo",
            contract.domain, gravity_counts.get(outcome_gravity.FAILED, 0),
        )

    confirmed = _confirm(contract, confirm, complete)
    return CommitReport(
        domain=contract.domain, total=total, handled_ids=tuple(handled),
        accounted=gravity_ok and not partial,
        ledger_written=written, ledger_skipped=skipped,
        no_identity=no_identity, complete_items=tuple(complete),
        partial_items=tuple(partial), origins=origins,
        gravity=gravity_counts, confirmed=confirmed,
    )


def _confirm(
    contract: DomainContract,
    confirm: Optional[Callable[[Sequence[str]], bool]],
    complete_items: Sequence[str],
) -> Optional[bool]:
    """Marca en la fuente los ÍTEMS cuya evidencia quedó COMPLETA, y solo esos.

    Recibe identidades de ÍTEM, no de resultado. La diferencia importa donde
    un ítem produce varios resultados: confirmar por resultado marcaba la CVE
    en cuanto aterrizaba cualquiera de sus niveles, aunque a otro le faltara.

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
    if not complete_items:
        return True
    try:
        ok = bool(confirm(tuple(complete_items)))
    except Exception as exc:
        logger.warning("%s | el marcador de fuente falló: %s", contract.domain, exc)
        ok = False
    if not ok:
        logger.warning(
            "%s | %d ítems quedaron sin marcar: se volverán a presentar y la "
            "deduplicación por identidad evitará que se escriban dos veces",
            contract.domain, len(complete_items),
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
