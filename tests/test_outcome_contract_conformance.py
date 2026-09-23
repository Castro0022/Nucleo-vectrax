"""
tests/test_outcome_contract_conformance.py — El contrato, exigido a TODOS.

QUÉ DEMUESTRA
-------------
Que `core/learn/outcome_contract.py` no es una biblioteca que cada dominio usa
como quiere, sino un contrato que TODOS cumplen, comprobado dominio por
dominio sobre el registro real.

POR QUÉ ESTE ARCHIVO EXISTE
---------------------------
El mismo defecto apareció cuatro veces y cada vez se corrigió donde apareció:
un resultado sin estrella que se perdía, un fallo del almacén que se daba por
hecho, un marcador fallido que duplicaba el ledger. Market los fue cerrando;
freight los tenía todos, y peor; `florida_real_estate` ni siquiera podía
recibir la protección compartida porque sus resultados no tenían identidad.

Corregirlos uno a uno era el error de método. Una prueba que pasa en market no
demuestra nada sobre freight. Estas pruebas se ejecutan una vez POR DOMINIO
REGISTRADO, así que un defecto encontrado en uno queda cerrado en todos, y un
dominio nuevo no puede entrar sin cumplir lo mismo.

CÓMO SE AÑADE UN DOMINIO NUEVO
------------------------------
1. Su ciclo de verificación declara y registra un `DomainContract`.
2. Se añade una fila a `SAMPLES` con un ítem de ejemplo y su subject.

`test_every_registered_domain_has_a_sample` falla si falta el paso 2, y
`test_every_verification_cycle_registers_a_contract` falla si falta el 1. No
hay forma de añadir un dominio y saltarse el contrato en silencio.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.learn import causal_learning as cl  # noqa: E402
from core.learn import outcome_contract as oc  # noqa: E402
from core.learn import outcome_gravity as og  # noqa: E402
from core.learn import verification_ledger as vledger  # noqa: E402
from core.learn.gravity_engine import GravityIndex  # noqa: E402
from core.learn.outcome_adapter import Outcome, OutcomeStatus  # noqa: E402

CONTRACTS = oc.load_all()


# ===========================================================================
# Material de ejemplo por dominio — el ÚNICO sitio con conocimiento específico
# ===========================================================================

class _Signal:
    def __init__(self, sid):
        self.signal_id = sid
        self.symbol = "AAPL"
        self.direction = "buy"
        self.entry_price = self.price = 100.0
        self.invalidation_price = None
        self.outcome_price = 101.0
        self.status = "resolved"


class _Event:
    __slots__ = ("event_type", "data", "source", "ts")

    def __init__(self, event_type, data, ts=1000.0):
        self.event_type = event_type
        self.data = dict(data)
        self.source = "sim"
        self.ts = ts


@dataclass(frozen=True)
class Sample:
    """Un ítem de ejemplo del dominio, su subject, y su ciclo REAL.

    `verify` es la entrada de verdad del dominio. Sin ella, la conformidad
    comprobaría el contrato DECLARADO y no el APLICADO: un dominio podría
    declarar una identidad impecable y no usarla al construir sus
    `Prediction`. Eso pasó de verdad —quitarle `prediction_id=` a real estate
    no rompía ninguna prueba— y por eso el ciclo real se ejercita aquí.
    """
    item: Any
    other: Any          # un ítem DISTINTO, para comprobar que no colisionan
    subject: str
    verify: Any         # Callable[[list], DomainScore]: el ciclo real
    #: ¿Está el ítem marcado en la fuente como "no volver a presentar"?
    #: Solo en dominios con `confirms=True`.
    is_confirmed: Any = None


def _market_marked(item) -> bool:
    from connectors.etoro import verification_cycle as mvc

    return item.signal_id in mvc._load_verified_ids()


def _cve_marked(item) -> bool:
    from connectors.cybersecurity import seen_ledger

    return seen_ledger.get(item.data["cve_id"]) is not None


def _verify(module_path: str, function: str):
    """El ciclo real del dominio, importado tarde para no encadenar imports."""
    def _run(items):
        import importlib

        module = importlib.import_module(module_path)
        if function == "run_market_verification":
            # El ciclo de market carga las señales del recorder, no las recibe.
            import connectors.etoro.signal_recorder as rec

            class _Status:
                class PENDING:
                    value = "pending"

            rec.load_signals = lambda *a, **k: list(items)
            rec.SignalStatus = _Status
            return module.run_market_verification()
        return getattr(module, function)(items)

    return _run


_CVE = {
    "cve_id": "CVE-2024-0001", "published": "2024-01-10", "in_kev": True,
    "kev_date": "2024-01-20", "vendor": "acme", "product": "widget",
    "cvss_v3_severity": "CRITICAL", "attack_vector": "NETWORK",
    "cwe": "CWE-79", "description": "xss",
}


SAMPLES = {
    "market": Sample(
        item=_Signal("sig-1"), other=_Signal("sig-2"), subject="AAPL",
        verify=_verify("connectors.etoro.verification_cycle", "run_market_verification"),
        is_confirmed=lambda item: _market_marked(item),
    ),
    "freight_logistics": Sample(
        item=_Event("delivery_complete", {
            "origin": "Atlanta", "destination": "Miami", "carrier": "FastHaul",
            "transit_days": 3, "on_time": True, "region": "Southeast"}),
        other=_Event("delivery_complete", {
            "origin": "Atlanta", "destination": "Miami", "carrier": "FastHaul",
            "transit_days": 6, "on_time": False, "region": "Southeast"}, ts=2000.0),
        subject="Southeast|FastHaul",
        verify=_verify("connectors.freight.verification_cycle", "verify_events"),
    ),
    "florida_real_estate": Sample(
        item=_Event("sale_closed", {
            "zone": "33139", "type": "condo", "tier": "mid",
            "list_price": 500000, "close_price": 495000, "days_on_market": 40}),
        other=_Event("sale_closed", {
            "zone": "33139", "type": "condo", "tier": "mid",
            "list_price": 600000, "close_price": 560000, "days_on_market": 90},
            ts=2000.0),
        subject="33139|condo|mid",
        verify=_verify("connectors.real_estate.verification_cycle", "verify_events"),
    ),
    "cybersecurity": Sample(
        item=_Event("cve", _CVE),
        other=_Event("cve", {**_CVE, "cve_id": "CVE-2024-0002"}),
        subject="product_family=acme",
        verify=_verify("connectors.cybersecurity.verification_cycle",
                       "verify_events"),
        is_confirmed=lambda item: _cve_marked(item),
    ),
}


def _outcome(contract, pid: str, status=OutcomeStatus.WIN, ts: float = 5000.0):
    return Outcome(
        prediction_id=pid, domain=contract.domain,
        subject=SAMPLES[contract.domain].subject,
        status=status, score=1.0 if status is OutcomeStatus.WIN else -1.0,
        resolved_ts=ts,
    )


def _ev(contract, pid: str, status=OutcomeStatus.WIN, ts: float = 5000.0,
        origin: str = "prueba"):
    """La evidencia 1:1 que usan las comprobaciones genéricas."""
    return oc.evidence(pid, origin, _outcome(contract, pid, status, ts))


def _ids(contract):
    return [o.prediction_id for o in vledger.load_outcomes(contract.domain)]


@pytest.fixture
def index(tmp_path, monkeypatch) -> GravityIndex:
    """Índice de gravedad REAL sobre disco temporal, instalado como el vivo."""
    import core.learn.gravity_engine as ge

    gi = GravityIndex(path=str(tmp_path / "gravity" / "gravity_index.json"))
    monkeypatch.setattr(ge, "get_gravity_index", lambda: gi, raising=False)
    monkeypatch.setattr(ge, "_index", gi, raising=False)
    return gi


def _make_star(index, contract) -> str:
    """Crea la estrella del subject de ejemplo, por la vía normal."""
    fingerprint = contract.star_for(_outcome(contract, "probe"))
    assert fingerprint, f"{contract.domain}: star_for no resolvió el subject"
    index.record_event(
        fingerprint=fingerprint, cc_score=0.6, impact="medium",
        domain=contract.domain, intent=fingerprint, outcome="observed",
        summary=fingerprint,
    )
    return fingerprint


def _verdicts(index, fingerprint) -> list:
    rec = index.get(fingerprint)
    return [e["status"] for e in rec.verified_outcomes] if rec else []


def _break_the_store():
    """Rompe el almacén de gravedad. Solo afecta a dominios que lo alimentan."""
    real = og._get_conn

    def _broken(*a, **k):
        raise sqlite3.OperationalError("database or disk is full")

    og._get_conn = _broken
    return lambda: setattr(og, "_get_conn", real)


def _break_the_ledger():
    """Rompe la escritura del ledger: el fallo que afecta a TODO dominio.

    `record_outcome` no lanza, devuelve False. Es la inyección que sirve para
    comprobar el marcador de la fuente en cualquier dominio, alimente o no la
    gravedad.
    """
    real = oc.vledger.record_outcome
    oc.vledger.record_outcome = lambda o: False
    return lambda: setattr(oc.vledger, "record_outcome", real)


ALL = pytest.mark.parametrize(
    "contract", CONTRACTS, ids=[c.domain for c in CONTRACTS],
)
GRAVITY = pytest.mark.parametrize(
    "contract", [c for c in CONTRACTS if c.feeds_gravity],
    ids=[c.domain for c in CONTRACTS if c.feeds_gravity],
)
DEDUPING = pytest.mark.parametrize(
    "contract", [c for c in CONTRACTS if not c.supersedes],
    ids=[c.domain for c in CONTRACTS if not c.supersedes],
)


# ===========================================================================
# 0. El registro está completo y no se puede eludir
# ===========================================================================

class TestTheRegistryIsComplete:

    def test_the_five_domains_are_accounted_for(self):
        """Vectrax tiene cinco dominios. Cuatro producen resultados
        verificados y están bajo contrato; el quinto tiene otra forma.

        `ai_provider` (`core/learn/provider_stars.py`) no produce `Outcome`
        verificados contra la verdad de un dominio: anota el desenlace de sus
        propias llamadas en `outcome_history`. No se le impone este contrato, y
        eso queda dicho aquí en vez de quedar implícito en una ausencia.
        """
        assert set(oc.registered_domains()) == {
            "market", "freight_logistics", "florida_real_estate", "cybersecurity",
        }

        from core.learn.provider_stars import PROVIDER_DOMAIN

        assert PROVIDER_DOMAIN == "ai_provider"
        assert oc.contract_for(PROVIDER_DOMAIN) is None

    def test_every_verification_cycle_registers_a_contract(self):
        """Un dominio nuevo no puede entrar sin contrato.

        Se descubren los ciclos en disco, no una lista escrita a mano: si
        alguien añade `connectors/<nuevo>/verification_cycle.py` y no registra
        su contrato, esto falla.
        """
        import importlib
        import re

        cycles = sorted(_ROOT.glob("connectors/*/verification_cycle.py"))
        assert cycles, "no se encontró ningún ciclo de verificación"

        for path in cycles:
            module_name = f"connectors.{path.parent.name}.verification_cycle"
            module = importlib.import_module(module_name)
            contract = getattr(module, "CONTRACT", None)
            assert isinstance(contract, oc.DomainContract), (
                f"{module_name} no declara CONTRACT: ningún dominio puede "
                f"persistir resultados fuera del contrato común"
            )
            assert oc.contract_for(contract.domain) is contract

            src = path.read_text(encoding="utf-8")
            direct = re.findall(r"^\s*vledger\.record_outcome\(", src, re.M)
            assert not direct, (
                f"{module_name} escribe en el ledger por su cuenta "
                f"({len(direct)} veces): la persistencia va por "
                f"`outcome_contract.commit()`, que es lo que garantiza "
                f"identidad, deduplicación y recuperación"
            )

    def test_every_registered_domain_has_a_sample(self):
        """Un dominio registrado sin ejemplo quedaría sin comprobar."""
        assert set(SAMPLES) == set(oc.registered_domains())


# ===========================================================================
# 1. Identidad estable — la PRECONDICIÓN del contrato
# ===========================================================================

@ALL
class TestRequirementOneStableIdentity:

    def test_the_identity_is_never_empty(self, contract):
        assert contract.identify(SAMPLES[contract.domain].item)

    def test_the_identity_is_deterministic(self, contract):
        sample = SAMPLES[contract.domain]
        assert contract.identify(sample.item) == contract.identify(sample.item)

    def test_distinct_items_do_not_collide(self, contract):
        sample = SAMPLES[contract.domain]
        assert contract.identify(sample.item) != contract.identify(sample.other)

    def test_the_real_cycle_attaches_the_declared_identity(self, contract, index):
        """La identidad DECLARADA es la que el ciclo real persiste.

        Sin esto la conformidad comprobaría el contrato sobre el papel: un
        dominio podría declarar `identify` y luego construir sus `Prediction`
        sin `prediction_id`. Comprobado por mutación — quitárselo a real estate
        no rompía nada hasta que existió esta prueba.
        """
        sample = SAMPLES[contract.domain]
        if contract.feeds_gravity:
            _make_star(index, contract)

        sample.verify([sample.item])

        ids = _ids(contract)
        assert ids, f"{contract.domain}: el ciclo real no persistió nada"
        assert all(ids), f"{contract.domain}: resultado sin identidad {ids}"

        declared = contract.identify(sample.item)
        # cybersecurity compone la identidad por peldaño (`{cve_id}|{nivel}`),
        # así que la declarada es el prefijo de las persistidas.
        assert any(pid == declared or pid.startswith(f"{declared}|")
                   for pid in ids), (
            f"{contract.domain}: el ciclo persistió {ids}, que no se "
            f"corresponde con la identidad declarada {declared!r}"
        )

    def test_the_real_cycle_does_not_duplicate_on_a_repeat(self, contract, index):
        """El mismo ítem, dos veces, por la entrada REAL del dominio."""
        sample = SAMPLES[contract.domain]
        if contract.feeds_gravity:
            _make_star(index, contract)

        sample.verify([sample.item])
        first = sorted(_ids(contract))
        sample.verify([sample.item])

        assert sorted(_ids(contract)) == first, (
            f"{contract.domain}: el ciclo real duplicó el ledger"
        )

    def test_an_outcome_without_identity_is_never_persisted(self, contract):
        """Una fila sin identidad es indistinguible de otra y rompe toda
        deduplicación posterior: no se escribe, y se dice."""
        nameless = Outcome(
            prediction_id="", domain=contract.domain,
            subject=SAMPLES[contract.domain].subject,
            status=OutcomeStatus.WIN, score=1.0,
        )

        report = oc.commit(contract, [oc.evidence("", "prueba", nameless)])

        assert report.no_identity == 1
        assert report.ledger_written == 0
        assert not report.accounted
        assert vledger.load_outcomes(contract.domain) == []


# ===========================================================================
# 2. Confirmación de las escrituras
# ===========================================================================

@ALL
class TestRequirementTwoWritesAreConfirmed:

    def test_a_successful_commit_reports_what_it_wrote(self, contract, index):
        if contract.feeds_gravity:
            _make_star(index, contract)

        report = oc.commit(contract, [_ev(contract, "a"), _ev(contract, "b")])

        assert report.ledger_written == 2
        assert report.accounted
        assert set(report.handled_ids) == {"a", "b"}
        assert sorted(_ids(contract)) == ["a", "b"]

    def test_a_store_failure_is_never_reported_as_success(self, contract, index):
        if not contract.feeds_gravity:
            pytest.skip("no alimenta la gravedad: no hay almacén que falle")
        _make_star(index, contract)

        restore = _break_the_store()
        try:
            report = oc.commit(contract, [_ev(contract, "a")])
        finally:
            restore()

        assert not report.accounted

    def test_a_rejected_ledger_write_is_not_reported_as_handled(
        self, contract, index, monkeypatch,
    ):
        if contract.feeds_gravity:
            _make_star(index, contract)
        monkeypatch.setattr(oc.vledger, "record_outcome", lambda o: False)

        report = oc.commit(contract, [_ev(contract, "a")])

        assert report.handled_ids == ()
        assert not report.accounted


# ===========================================================================
# 3. Reintento sin pérdida ni duplicación
# ===========================================================================

@DEDUPING
class TestRequirementThreeNoDuplication:

    def test_committing_the_same_outcome_twice_writes_one_row(
        self, contract, index,
    ):
        if contract.feeds_gravity:
            _make_star(index, contract)
        ev = _ev(contract, "a")

        oc.commit(contract, [ev])
        second = oc.commit(contract, [ev])

        assert _ids(contract) == ["a"]
        assert second.ledger_written == 0
        assert second.ledger_skipped == 1
        assert second.accounted

    def test_the_accumulated_score_is_not_inflated(self, contract, index):
        if contract.feeds_gravity:
            _make_star(index, contract)
        batch = [_ev(contract, "a"), _ev(contract, "b"), _ev(contract, "c"),
                 _ev(contract, "d", OutcomeStatus.LOSS)]

        for _ in range(3):
            oc.commit(contract, batch)

        score = vledger.domain_score(contract.domain)
        assert score.n_decisive == 4, score
        assert score.win_rate == pytest.approx(75.0)


class TestASupersedingDomainKeepsItsRule:
    """El contrato admite a cybersecurity, no lo uniformiza.

    Una CVE vieja que entra en KEV pasa de LOSS a WIN, y ese dominio escribe
    una fila que supersede a la anterior. Forzarle la deduplicación de
    escritura rompería esa corrección.
    """

    @staticmethod
    def _superseding():
        found = [c for c in CONTRACTS if c.supersedes]
        if not found:
            pytest.skip("ningún dominio declara supersesión")
        return found[0]

    def test_a_second_write_supersedes_instead_of_being_skipped(self, index):
        contract = self._superseding()

        oc.commit(contract, [_ev(contract, "CVE-1|family", OutcomeStatus.LOSS)])
        oc.commit(contract, [_ev(contract, "CVE-1|family", OutcomeStatus.WIN)])

        rows = vledger.load_outcomes(contract.domain)
        assert len(rows) == 2, "la supersesión quedó suprimida"
        assert rows[-1].status is OutcomeStatus.WIN

    def test_it_still_owes_identity(self, index):
        contract = self._superseding()
        assert contract.identify(SAMPLES[contract.domain].item)


# ===========================================================================
# 4. La asimetría es una propiedad de la FUENTE, no del dominio
# ===========================================================================

class TestTheSourceDecidesTheFailureRule:

    @pytest.mark.parametrize(
        "contract", [c for c in CONTRACTS if c.feeds_gravity and c.replayable],
        ids=[c.domain for c in CONTRACTS if c.feeds_gravity and c.replayable],
    )
    def test_a_replayable_source_withholds_the_ledger(self, contract, index):
        """Puede volver a presentar el ítem: no se escribe nada, y el lote
        entero se repite. Las tres escrituras avanzan juntas o no avanzan."""
        _make_star(index, contract)

        restore = _break_the_store()
        try:
            report = oc.commit(contract, [_ev(contract, "a")])
        finally:
            restore()

        assert not report.accounted
        assert report.ledger_written == 0
        assert vledger.load_outcomes(contract.domain) == []
        assert report.handled_ids == ()

    @pytest.mark.parametrize(
        "contract", [c for c in CONTRACTS if c.feeds_gravity and not c.replayable],
        ids=[c.domain for c in CONTRACTS if c.feeds_gravity and not c.replayable],
    )
    def test_a_streaming_source_keeps_the_ledger_and_recovers(
        self, contract, index,
    ):
        """No puede volver a presentar el ítem: retener el ledger lo perdería
        del todo. Se escribe, y la gravedad se recupera desde ahí."""
        fingerprint = _make_star(index, contract)

        restore = _break_the_store()
        try:
            report = oc.commit(contract, [_ev(contract, "a")])
        finally:
            restore()

        assert not report.accounted
        assert report.ledger_written == 1
        assert _verdicts(index, fingerprint) == []

        oc.recover(contract)

        assert _verdicts(index, fingerprint) == ["win"]
        assert len(vledger.load_outcomes(contract.domain)) == 1


# ===========================================================================
# 5. Los dominios que alimentan la gravedad: el resultado llega y se recupera
# ===========================================================================

@GRAVITY
class TestGravityFedDomains:

    def test_the_verdict_reaches_the_star(self, contract, index):
        fingerprint = _make_star(index, contract)

        oc.commit(contract, [_ev(contract, "a"),
                             _ev(contract, "b", OutcomeStatus.LOSS)])

        assert _verdicts(index, fingerprint) == ["win", "loss"]

    def test_a_missing_star_parks_the_result_instead_of_losing_it(
        self, contract, index,
    ):
        report = oc.commit(contract, [_ev(contract, "a")])

        assert og.pending_count(domain=contract.domain) == 1
        assert report.ledger_written == 1 or contract.replayable

        fingerprint = _make_star(index, contract)
        oc.recover(contract)

        assert _verdicts(index, fingerprint) == ["win"]
        assert og.pending_count(domain=contract.domain) == 0

    def test_recovery_is_idempotent(self, contract, index):
        fingerprint = _make_star(index, contract)
        oc.commit(contract, [_ev(contract, "a"), _ev(contract, "b")])

        for _ in range(4):
            oc.recover(contract)

        assert _verdicts(index, fingerprint) == ["win", "win"]
        assert og.applied_count(domain=contract.domain) == 2
        assert len(vledger.load_outcomes(contract.domain)) == 2

    def test_provenance_names_the_domain_cycle(self, contract, index):
        _make_star(index, contract)
        oc.commit(contract, [_ev(contract, "a")])

        rows = og.provenance(domain=contract.domain)
        assert len(rows) == 1
        assert rows[0]["source"] == contract.source
        assert rows[0]["prediction_id"] == "a"


# ===========================================================================
# 6. El marcador de la fuente se confirma DESPUÉS de escribir, nunca antes
# ===========================================================================

CONFIRMING = pytest.mark.parametrize(
    "contract", [c for c in CONTRACTS if c.confirms],
    ids=[c.domain for c in CONTRACTS if c.confirms],
)


@CONFIRMING
class TestTheSourceMarkerFollowsTheWrite:
    """`market_verified.json` y el `seen_ledger` de cybersecurity son LA MISMA
    COSA: un marcador que le dice a la fuente "no vuelvas a presentar esto".

    Los dos tenían el mismo fallo y se encontró por separado: market marcaba el
    lote entero, cybersecurity marcaba la CVE ANTES de escribir su resultado.
    En ambos casos `replayable=True` se volvía mentira —la fuente podía
    releerse, pero el marcador ya la había excluido— y el resultado se perdía
    para siempre.

    La regla la aplica ahora `outcome_contract.commit()` en un único punto, así
    que estas pruebas valen para cualquier dominio con marcador, incluido uno
    que todavía no exista.
    """

    def test_a_failed_write_confirms_nothing(self, contract, index):
        sample = SAMPLES[contract.domain]
        if contract.feeds_gravity:
            _make_star(index, contract)

        restore = _break_the_ledger()
        try:
            sample.verify([sample.item])
        finally:
            restore()

        assert not sample.is_confirmed(sample.item), (
            f"{contract.domain}: la fuente quedó marcada sin haber escrito el "
            f"resultado; ese ítem no se volvería a presentar nunca"
        )

    def test_the_item_comes_back_and_lands_after_the_failure(
        self, contract, index,
    ):
        """La prueba de la promesa de "sin pérdida": tras el fallo, el ítem
        vuelve a presentarse y esta vez sí queda escrito."""
        sample = SAMPLES[contract.domain]
        if contract.feeds_gravity:
            _make_star(index, contract)

        restore = _break_the_ledger()
        try:
            sample.verify([sample.item])
        finally:
            restore()
        assert vledger.load_outcomes(contract.domain) == []
        assert not sample.is_confirmed(sample.item)

        sample.verify([sample.item])

        assert _ids(contract), f"{contract.domain}: el resultado se perdió"
        assert sample.is_confirmed(sample.item)

    def test_a_successful_write_confirms(self, contract, index):
        sample = SAMPLES[contract.domain]
        if contract.feeds_gravity:
            _make_star(index, contract)

        sample.verify([sample.item])

        assert sample.is_confirmed(sample.item)

    def test_a_failed_marker_does_not_duplicate_on_the_retry(
        self, contract, index, monkeypatch,
    ):
        """Si el marcador falla DESPUÉS de escribir, el ítem vuelve — y la
        deduplicación por identidad impide que se escriba dos veces."""
        sample = SAMPLES[contract.domain]
        if contract.feeds_gravity:
            _make_star(index, contract)

        real = oc._confirm
        monkeypatch.setattr(oc, "_confirm", lambda c, f, h: False)
        sample.verify([sample.item])
        first = sorted(_ids(contract))
        monkeypatch.setattr(oc, "_confirm", real)

        sample.verify([sample.item])

        if contract.supersedes:
            pytest.skip("supersede a propósito: la reescritura es su regla")
        assert sorted(_ids(contract)) == first, (
            f"{contract.domain}: el reintento duplicó el ledger"
        )


# ===========================================================================
# 7. La reconciliación barre el ledger ENTERO, no una ventana
# ===========================================================================

@GRAVITY
class TestReconciliationSweepsTheWholeLedger:
    """`reconcile_from_ledger` acotaba el trabajo por ciclo mirando solo la
    COLA reciente. Si durante una caída prolongada entraban más resultados que
    el límite, los anteriores quedaban detrás y no se examinaban nunca: ni
    aplicados, ni aparcados, ni recuperables.

    Ahora avanza desde una marca de agua persistida, así que el límite acota el
    trabajo por ciclo pero no lo que se llega a mirar.
    """

    def test_results_older_than_the_window_are_still_recovered(
        self, contract, index,
    ):
        fingerprint = _make_star(index, contract)

        restore = _break_the_store()
        try:
            for i in range(12):
                oc.commit(contract, [_ev(contract, f"old-{i}", ts=1000.0 + i)])
        finally:
            restore()

        if contract.replayable:
            pytest.skip("una fuente reemitible repite el lote, no reconcilia")

        assert len(vledger.load_outcomes(contract.domain)) == 12
        assert _verdicts(index, fingerprint) == []

        # Ventana de 3: hacen falta varios ciclos, y NINGUNO se queda fuera.
        for _ in range(6):
            og.reconcile_from_ledger(
                contract.domain, contract.star_for, limit=3,
                source=contract.source,
            )

        assert og.applied_count(domain=contract.domain) == 12, (
            f"{contract.domain}: la reconciliación dejó resultados sin examinar"
        )

    def test_the_watermark_advances_and_persists(self, contract, index):
        _make_star(index, contract)
        for i in range(5):
            oc.commit(contract, [_ev(contract, f"a-{i}", ts=2000.0 + i)])

        before = og.reconcile_position(contract.domain)
        og.reconcile_from_ledger(
            contract.domain, contract.star_for, limit=2, source=contract.source,
        )
        after = og.reconcile_position(contract.domain)

        assert after > before
        assert og.reconcile_position(contract.domain) == after  # persistida

    def test_the_watermark_does_not_advance_over_an_unaccounted_batch(
        self, contract, index,
    ):
        """Si el almacén no está, el tramo NO se da por examinado."""
        _make_star(index, contract)
        restore = _break_the_store()
        try:
            for i in range(3):
                oc.commit(contract, [_ev(contract, f"b-{i}", ts=3000.0 + i)])
        finally:
            restore()
        if contract.replayable:
            pytest.skip("una fuente reemitible repite el lote, no reconcilia")

        before = og.reconcile_position(contract.domain)
        restore = _break_the_store()
        try:
            og.reconcile_from_ledger(
                contract.domain, contract.star_for, source=contract.source,
            )
        finally:
            restore()

        assert og.reconcile_position(contract.domain) == before


# ===========================================================================
# 8. Cada dominio declara QUÉ MIDE y DE DÓNDE VIENE; el núcleo lo registra
# ===========================================================================

@ALL
class TestTheEvidenceSaysWhatItIsAndWhereItCameFrom:
    """Un dominio entrega al núcleo de dónde viene el dato, qué mide, cómo se
    cuenta y cuál fue el resultado. Freight cuenta entregas y market señales;
    no tienen por qué parecerse. Lo común es que esté DICHO, porque decidir qué
    significa lo observado es del núcleo, y no puede decidirlo sobre evidencia
    que no dice qué es ni de dónde salió.
    """

    def test_the_contract_declares_its_unit(self, contract):
        assert contract.unit.strip(), f"{contract.domain} no dice qué mide"

    def test_the_persisted_evidence_carries_unit_and_origin(
        self, contract, index,
    ):
        sample = SAMPLES[contract.domain]
        if contract.feeds_gravity:
            _make_star(index, contract)

        sample.verify([sample.item])

        rows = vledger.load_outcomes(contract.domain)
        assert rows, f"{contract.domain}: no persistió evidencia"
        for row in rows:
            assert row.evidence.get("unit") == contract.unit, row.evidence
            assert str(row.evidence.get("origin", "")).strip(), (
                f"{contract.domain}: evidencia sin procedencia: {row.evidence}"
            )

    def test_the_core_stamps_the_origin_the_domain_supplies(self, contract):
        """La procedencia es del LOTE, no una constante del módulo."""
        report = oc.commit(contract, [_ev(contract, "a", origin="feed-X")])

        assert report.origins == ("feed-X",)
        rows = vledger.load_outcomes(contract.domain)
        assert rows[-1].evidence["origin"] == "feed-X"

    def test_an_absent_origin_is_unknown_never_the_cycle_name(self, contract):
        """No saber de dónde vino se DICE, no se rellena.

        Antes una procedencia ausente se sustituía por el nombre del ciclo.
        Eso la hacía PARECER conocida: una etiqueta verosímil ocupando el
        sitio de un dato que no se tenía, indistinguible de una procedencia
        real. Fabricar procedencia es el mismo error que fabricar resultados.
        """
        report = oc.commit(contract, [_ev(contract, "a", origin="  ")])

        assert report.origins == (oc.UNKNOWN,)
        row = vledger.load_outcomes(contract.domain)[-1]
        assert row.evidence["origin"] == oc.UNKNOWN
        assert row.evidence["origin_kind"] == oc.UNKNOWN
        assert contract.source not in str(row.evidence["origin"])


def test_every_domain_measures_something_distinct():
    """Dos dominios que digan medir lo mismo harían la unidad inútil."""
    units = [c.unit for c in CONTRACTS]
    assert len(set(units)) == len(units), f"unidades repetidas: {units}"


class TestSimulatedEvidenceIsDistinguishableFromReal:
    """El caso concreto que hacía falta cerrar.

    Freight y real estate admiten varios proveedores. Si la procedencia fuera
    una constante del ciclo, la evidencia de un simulador y la de un feed real
    quedarían idénticas en el ledger y el núcleo aprendería de lo simulado como
    si fuera real. La procedencia se lee de los propios eventos.
    """

    @pytest.mark.parametrize(
        "domain,module",
        [("freight_logistics", "connectors.freight.verification_cycle"),
         ("florida_real_estate", "connectors.real_estate.verification_cycle")],
    )
    def test_two_providers_leave_different_provenance(
        self, domain, module, index,
    ):
        import importlib

        vc = importlib.import_module(module)
        contract = oc.contract_for(domain)
        if contract.feeds_gravity:
            _make_star(index, contract)

        simulated = SAMPLES[domain].item
        real = SAMPLES[domain].other
        real.source = "dat_feed"

        vc.verify_events([simulated])
        vc.verify_events([real])

        origins = {r.evidence.get("origin") for r in vledger.load_outcomes(domain)}
        assert origins == {"sim", "dat_feed"}, origins


# ===========================================================================
# 9. Un ítem se confirma SOLO con su evidencia completa
# ===========================================================================

@CONFIRMING
class TestPartialEvidenceIsNeverConfirmed:
    """Un ítem puede producir VARIOS resultados: una CVE deja uno por cada
    peldaño de su escalera.

    Confirmando por RESULTADO, la CVE quedaba marcada en cuanto aterrizaba
    cualquiera de ellos. Si un nivel se escribía y otro fallaba, la CVE no se
    volvía a presentar nunca y la parte que faltó se perdía para siempre — con
    `replayable=True`, que decía justo lo contrario.

    La unidad que entra al núcleo es la EVIDENCIA de un ítem, y la regla es del
    contrato: se confirma cuando TODOS sus resultados quedaron escritos.
    """

    @staticmethod
    def _multi(contract, item="ITEM-1", n=3):
        return oc.Evidence(
            item_id=item, origin="prueba",
            outcomes=tuple(_outcome(contract, f"{item}|n{i}") for i in range(n)),
        )

    def test_a_partial_write_confirms_nothing(self, contract, index):
        if contract.feeds_gravity:
            _make_star(index, contract)
        ev = self._multi(contract)
        rejected = ev.outcomes[1].prediction_id
        real = oc.vledger.record_outcome
        oc.vledger.record_outcome = (
            lambda o: False if o.prediction_id == rejected else real(o)
        )
        try:
            report = oc.commit(contract, [ev])
        finally:
            oc.vledger.record_outcome = real

        assert report.partial_items == ("ITEM-1",), report
        assert report.complete_items == ()
        assert not report.accounted

    def test_the_marker_is_not_set_for_a_partial_item(self, contract, index):
        if contract.feeds_gravity:
            _make_star(index, contract)
        marked: list = []
        ev = self._multi(contract)
        rejected = ev.outcomes[-1].prediction_id
        real = oc.vledger.record_outcome
        oc.vledger.record_outcome = (
            lambda o: False if o.prediction_id == rejected else real(o)
        )
        try:
            oc.commit(contract, [ev], confirm=lambda ids: marked.extend(ids) or True)
        finally:
            oc.vledger.record_outcome = real

        assert marked == [], (
            f"{contract.domain}: se marcó un ítem con evidencia incompleta"
        )

    def test_a_complete_item_is_confirmed(self, contract, index):
        if contract.feeds_gravity:
            _make_star(index, contract)
        marked: list = []

        oc.commit(contract, [self._multi(contract)],
                  confirm=lambda ids: marked.extend(ids) or True)

        assert marked == ["ITEM-1"]

    def test_one_partial_item_does_not_block_the_complete_ones(
        self, contract, index,
    ):
        if contract.feeds_gravity:
            _make_star(index, contract)
        good = self._multi(contract, "OK-1")
        bad = self._multi(contract, "BAD-1")
        rejected = bad.outcomes[0].prediction_id
        marked: list = []
        real = oc.vledger.record_outcome
        oc.vledger.record_outcome = (
            lambda o: False if o.prediction_id == rejected else real(o)
        )
        try:
            report = oc.commit(contract, [good, bad],
                               confirm=lambda ids: marked.extend(ids) or True)
        finally:
            oc.vledger.record_outcome = real

        assert marked == ["OK-1"]
        assert report.partial_items == ("BAD-1",)


class TestCybersecurityKeepsAllItsLevels:
    """El caso concreto, por el ciclo REAL: una CVE con varios peldaños."""

    def test_a_cve_with_a_rejected_level_is_not_marked_as_seen(self, index):
        import importlib

        from connectors.cybersecurity import seen_ledger

        cvc = importlib.import_module("connectors.cybersecurity.verification_cycle")
        sample = SAMPLES["cybersecurity"]

        real = oc.vledger.record_outcome
        calls = {"n": 0}

        def _one_fails(o):
            calls["n"] += 1
            return False if calls["n"] == 1 else real(o)

        oc.vledger.record_outcome = _one_fails
        try:
            cvc.verify_events([sample.item])
        finally:
            oc.vledger.record_outcome = real

        assert seen_ledger.get(_CVE["cve_id"]) is None, (
            "la CVE quedó marcada con un nivel sin escribir: se perdería"
        )

        cvc.verify_events([sample.item])
        assert seen_ledger.get(_CVE["cve_id"]) is not None


# ===========================================================================
# 10. La procedencia viaja con cada evidencia, no con el lote
# ===========================================================================

class TestProvenanceTravelsWithEachEvidence:

    @ALL
    def test_a_mixed_batch_keeps_each_provenance(self, contract, index):
        """Un lote que mezcla simulador y feed real no puede estamparlos
        igual: es justo lo que la procedencia existe para distinguir."""
        if contract.feeds_gravity:
            _make_star(index, contract)

        oc.commit(contract, [
            _ev(contract, "a", origin="simulador"),
            _ev(contract, "b", origin="feed_real"),
        ])

        by_id = {o.prediction_id: o.evidence.get("origin")
                 for o in vledger.load_outcomes(contract.domain)}
        assert by_id == {"a": "simulador", "b": "feed_real"}, by_id

    @pytest.mark.parametrize(
        "domain,module",
        [("freight_logistics", "connectors.freight.verification_cycle"),
         ("florida_real_estate", "connectors.real_estate.verification_cycle")],
    )
    def test_the_real_cycle_reads_the_events_only_once(
        self, domain, module, index,
    ):
        """Con un iterable CONSUMIBLE, recorrer los eventos por segunda vez
        para obtener el origen no devuelve nada. La procedencia se lee en la
        misma pasada que los resuelve."""
        import importlib

        vc = importlib.import_module(module)
        contract = oc.contract_for(domain)
        if contract.feeds_gravity:
            _make_star(index, contract)

        simulated = SAMPLES[domain].item
        real = SAMPLES[domain].other
        real.source = "dat_feed"

        vc.verify_events(iter([simulated, real]))   # <- generador, un solo paso

        origins = {r.evidence.get("origin") for r in vledger.load_outcomes(domain)}
        assert origins == {"sim", "dat_feed"}, origins


# ===========================================================================
# 11. QUÉ USO hace el núcleo de cada evidencia: el recorrido hasta el criterio
# ===========================================================================

@GRAVITY
class TestTheCoreDecidesWhatItCanLearnFrom:
    """Guardar la procedencia no es usarla.

    Hasta aquí el contrato registraba de dónde venía cada evidencia y después
    aprendía de todas por igual. Estas pruebas siguen una evidencia SIMULADA y
    una REAL hasta `qualify_pattern()` —el punto donde el núcleo decide si un
    patrón tiene criterio— y comprueban que hace un uso distinto de cada una.

    La regla: lo simulado se registra y se puede auditar, pero NO cualifica un
    patrón. Aprender de un simulador y aplicar ese criterio a decisiones
    reales es el error que la procedencia existe para impedir.
    """

    @staticmethod
    def _feed(contract, index, n, origin, status=OutcomeStatus.WIN, tag="e"):
        return oc.commit(contract, [
            _ev(contract, f"{tag}-{origin}-{i}", status, ts=6000.0 + i,
                origin=origin)
            for i in range(n)
        ])

    def test_simulated_evidence_is_recorded(self, contract, index):
        fingerprint = _make_star(index, contract)

        self._feed(contract, index, 3, "simulator")

        # Está: se puede auditar qué se excluyó y por qué.
        assert len(index.get(fingerprint).verified_outcomes) == 3
        assert len(vledger.load_outcomes(contract.domain)) == 3
        rows = og.provenance(domain=contract.domain)
        assert len(rows) == 3

    def test_simulated_evidence_does_not_grade_the_pattern(self, contract, index):
        from core.gravity_kernel.signals import fetch_pattern_stats

        fingerprint = _make_star(index, contract)
        self._feed(contract, index, 20, "simulator")

        assert fetch_pattern_stats(fingerprint) is None, (
            f"{contract.domain}: un patrón se graduó con evidencia simulada"
        )

    def test_real_evidence_does_grade_the_pattern(self, contract, index):
        from core.gravity_kernel.signals import fetch_pattern_stats

        fingerprint = _make_star(index, contract)
        self._feed(contract, index, 16, "dat_feed")
        self._feed(contract, index, 4, "dat_feed", OutcomeStatus.LOSS, tag="l")

        stats = fetch_pattern_stats(fingerprint)
        assert stats is not None
        assert stats["sample_size"] == 20
        assert stats["win_rate"] == pytest.approx(0.8)
        assert stats["real"] == 20
        assert stats["simulated"] == 0

    def test_simulated_evidence_does_not_dilute_the_real_one(
        self, contract, index,
    ):
        """Lo importante: mezcladas, el criterio sale del subconjunto real."""
        from core.gravity_kernel.signals import fetch_pattern_stats

        fingerprint = _make_star(index, contract)
        # 5 reales, todas acierto. 10 simuladas, todas fallo.
        self._feed(contract, index, 5, "dat_feed", tag="r")
        self._feed(contract, index, 10, "simulator", OutcomeStatus.LOSS, tag="s")

        stats = fetch_pattern_stats(fingerprint)
        assert stats["sample_size"] == 5, stats
        assert stats["win_rate"] == pytest.approx(1.0), stats
        assert stats["simulated"] == 10   # visible, no silenciada
        assert stats["real"] == 5

    def test_a_simulated_pattern_never_qualifies(self, contract, index):
        """El recorrido entero: evidencia simulada -> NO hay criterio."""
        fingerprint = _make_star(index, contract)
        self._feed(contract, index, 20, "simulator")

        stats = cl.qualify_pattern(
            fingerprint, cl.build_production_policy(contract.domain),
        )

        assert not stats.qualified, stats.reason
        assert "no tiene outcomes graduados" in stats.reason

    def test_the_same_evidence_from_a_real_feed_does_qualify(
        self, contract, index,
    ):
        """La misma evidencia, cambiando SOLO la procedencia, sí cualifica.

        Es la comparación que demuestra que la decisión la toma la procedencia
        y no otra cosa del dato.
        """
        fingerprint = _make_star(index, contract)
        self._feed(contract, index, 16, "dat_feed")
        self._feed(contract, index, 4, "dat_feed", OutcomeStatus.LOSS, tag="l")

        stats = cl.qualify_pattern(
            fingerprint, cl.build_production_policy(contract.domain),
        )

        assert stats.qualified, stats.reason
        assert stats.sample_size == 20
        assert stats.win_rate == pytest.approx(80.0)

    def test_the_breakdown_makes_the_exclusion_auditable(self, contract, index):
        """Excluir en silencio sería otro agujero: se puede preguntar."""
        from core.gravity_kernel.signals import provenance_breakdown

        fingerprint = _make_star(index, contract)
        self._feed(contract, index, 3, "dat_feed", tag="r")
        self._feed(contract, index, 2, "simulator", tag="s")
        self._feed(contract, index, 1, "", tag="u")

        breakdown = provenance_breakdown(index.get(fingerprint))
        assert breakdown == {"real": 3, "simulated": 2, "unknown": 1}, breakdown


class TestFreightLearnsFromSimulatedDataToday:
    """Consecuencia concreta, dicha en vez de escondida.

    `FREIGHT_FEED_PROVIDER` y `REAL_ESTATE_FEED_PROVIDER` valen "simulator"
    por defecto, así que HOY casi toda la evidencia de esos dos dominios es
    simulada — y desde este cambio ya no gradúa. Un patrón de freight no puede
    cualificar con entregas que nunca ocurrieron, y eso significa que la
    demostración de aprendizaje de freight en rondas anteriores de este PR
    estaba hecha sobre datos simulados.

    No se cambia el proveedor por defecto aquí: es una decisión de operación.
    Lo que sí se fija es que el núcleo ya no confunde una cosa con la otra.
    """

    def test_the_simulator_is_classified_as_simulated(self):
        for domain in ("freight_logistics", "florida_real_estate"):
            contract = oc.contract_for(domain)
            assert contract.origin_kind("simulator") == oc.SIMULATED, domain
            assert contract.origin_kind("sim") == oc.SIMULATED, domain

    def test_a_real_feed_is_classified_as_real(self):
        assert oc.contract_for("freight_logistics").origin_kind("dat") == oc.REAL
        assert oc.contract_for("florida_real_estate").origin_kind("attom") == oc.REAL

    def test_market_prices_and_cve_feeds_are_real(self):
        assert oc.contract_for("market").origin_kind("signal_recorder") == oc.REAL
        assert oc.contract_for("cybersecurity").origin_kind("nvd+kev") == oc.REAL

    def test_market_paper_shadow_is_not_real(self):
        """El PAPER-shadow no observa el mundo: registra hipótesis."""
        contract = oc.contract_for("market")
        assert contract.origin_kind("paper_shadow") == oc.SIMULATED


# ===========================================================================
# 12. La ventana de aprendizaje se calcula SOLO con evidencia apta
# ===========================================================================

@GRAVITY
class TestInadmissibleEvidenceNeverDisplacesAdmissible:
    """La estrella conserva una ventana ACOTADA, y lo no admisible ocupaba
    plaza en ella ANTES de que el graduador lo excluyera.

    Consecuencia reproducida: 20 simulados llegados después de 20 reales
    expulsaban a los reales, y el patrón dejaba de cualificar sin que su
    evidencia real hubiera cambiado. Un simulador podía apagar un criterio
    aprendido de observaciones reales solo por llegar más tarde.

    La regla: conservar toda procedencia para auditoría, pero calcular la
    ventana de aprendizaje únicamente con evidencia apta.
    """

    @staticmethod
    def _feed(contract, n, origin, status=OutcomeStatus.WIN, t0=1000.0, tag="e"):
        return oc.commit(contract, [
            _ev(contract, f"{tag}-{i}", status, ts=t0 + i, origin=origin)
            for i in range(n)
        ])

    def test_a_flood_of_simulated_does_not_evict_the_real(self, contract, index):
        from core.gravity_kernel.signals import fetch_pattern_stats
        from core.learn.gravity_engine import MAX_OUTCOME_HISTORY

        fingerprint = _make_star(index, contract)
        self._feed(contract, 16, "dat_feed", t0=1000.0, tag="r")
        self._feed(contract, 4, "dat_feed", OutcomeStatus.LOSS, t0=2000.0, tag="rl")
        before = cl.qualify_pattern(
            fingerprint, cl.build_production_policy(contract.domain))
        assert before.qualified, before.reason

        # Llegan MÁS simulados que el tamaño de la ventana, y DESPUÉS.
        self._feed(contract, MAX_OUTCOME_HISTORY + 10, "simulator",
                   OutcomeStatus.LOSS, t0=9000.0, tag="s")

        stats = fetch_pattern_stats(fingerprint)
        assert stats["sample_size"] == 20, stats
        assert stats["win_rate"] == pytest.approx(0.8), stats
        after = cl.qualify_pattern(
            fingerprint, cl.build_production_policy(contract.domain))
        assert after.qualified, (
            f"{contract.domain}: lo simulado apagó un criterio real: {after.reason}"
        )

    def test_the_simulated_evidence_is_still_retained_for_audit(
        self, contract, index,
    ):
        from core.gravity_kernel.signals import provenance_breakdown
        from core.learn.gravity_engine import MAX_OUTCOME_HISTORY

        fingerprint = _make_star(index, contract)
        self._feed(contract, 5, "dat_feed", t0=1000.0, tag="r")
        self._feed(contract, 8, "simulator", t0=9000.0, tag="s")

        breakdown = provenance_breakdown(index.get(fingerprint))
        assert breakdown == {"real": 5, "simulated": 8}, breakdown
        # Y acotada también: la auditoría no crece sin límite.
        self._feed(contract, MAX_OUTCOME_HISTORY + 15, "simulator",
                   t0=20000.0, tag="s2")
        breakdown = provenance_breakdown(index.get(fingerprint))
        assert breakdown["simulated"] == MAX_OUTCOME_HISTORY, breakdown
        assert breakdown["real"] == 5, breakdown

    def test_each_class_keeps_its_own_window(self, contract, index):
        from core.gravity_kernel.signals import provenance_breakdown
        from core.learn.gravity_engine import MAX_OUTCOME_HISTORY

        fingerprint = _make_star(index, contract)
        for origin, tag in (("dat_feed", "r"), ("simulator", "s"), ("", "u")):
            self._feed(contract, MAX_OUTCOME_HISTORY + 5, origin,
                       t0=1000.0, tag=tag)

        breakdown = provenance_breakdown(index.get(fingerprint))
        assert breakdown == {
            "real": MAX_OUTCOME_HISTORY,
            "simulated": MAX_OUTCOME_HISTORY,
            "unknown": MAX_OUTCOME_HISTORY,
        }, breakdown

def test_the_grader_and_the_window_share_one_rule():
    """Si discreparan, la ventana conservaría lo que el graduador descarta
    —o al revés— y nadie lo vería. Es propiedad del núcleo, no de un dominio."""
    import inspect

    from core.learn import gravity_engine as ge
    from core.gravity_kernel import signals
    from core.learn.schemas import ADMISSIBLE_ORIGIN_KINDS

    assert ADMISSIBLE_ORIGIN_KINDS == ("real",)
    assert "entry_origin_kind" in inspect.getsource(ge._trim_by_origin_kind)
    assert "is_admissible" in inspect.getsource(signals._gradable_history)


@GRAVITY
class TestUnknownProvenanceDoesNotTeach:
    """Decisión del núcleo: un dominio nuevo no enseña por defecto.

    `unknown` se registra y se audita, pero no gradúa hasta que se conozca su
    procedencia. Protege el crecimiento —añadir un dominio no mueve el
    criterio hasta que declare de dónde vienen sus datos— sin quitar
    aprendizaje a los que ya la declaran.
    """

    def test_unknown_evidence_does_not_grade(self, contract, index):
        from core.gravity_kernel.signals import fetch_pattern_stats

        fingerprint = _make_star(index, contract)
        oc.commit(contract, [
            _ev(contract, f"u-{i}", ts=1000.0 + i, origin="")
            for i in range(20)
        ])

        assert fetch_pattern_stats(fingerprint) is None

    def test_unknown_evidence_never_qualifies_a_pattern(self, contract, index):
        fingerprint = _make_star(index, contract)
        oc.commit(contract, [
            _ev(contract, f"u-{i}", ts=1000.0 + i, origin="")
            for i in range(20)
        ])

        stats = cl.qualify_pattern(
            fingerprint, cl.build_production_policy(contract.domain))

        assert not stats.qualified, stats.reason

    def test_unknown_evidence_is_recorded_and_visible(self, contract, index):
        from core.gravity_kernel.signals import provenance_breakdown

        fingerprint = _make_star(index, contract)
        oc.commit(contract, [_ev(contract, "u-1", origin="")])

        assert provenance_breakdown(index.get(fingerprint)) == {"unknown": 1}
        assert len(vledger.load_outcomes(contract.domain)) == 1

    def test_unknown_does_not_dilute_a_real_pattern(self, contract, index):
        from core.gravity_kernel.signals import fetch_pattern_stats

        fingerprint = _make_star(index, contract)
        oc.commit(contract, [
            _ev(contract, f"r-{i}", ts=1000.0 + i, origin="dat_feed")
            for i in range(10)
        ])
        oc.commit(contract, [
            _ev(contract, f"u-{i}", OutcomeStatus.LOSS, ts=9000.0 + i, origin="")
            for i in range(10)
        ])

        stats = fetch_pattern_stats(fingerprint)
        assert stats["sample_size"] == 10, stats
        assert stats["win_rate"] == pytest.approx(1.0), stats
        assert stats["unknown"] == 10
