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


def _verify(module_path: str, function: str):
    """El ciclo real del dominio, importado tarde para no encadenar imports."""
    def _run(items):
        import importlib

        return getattr(importlib.import_module(module_path), function)(items)

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
        verify=_verify("connectors.etoro.verification_cycle", "verify_signals"),
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
    ),
}


def _outcome(contract, pid: str, status=OutcomeStatus.WIN, ts: float = 5000.0):
    return Outcome(
        prediction_id=pid, domain=contract.domain,
        subject=SAMPLES[contract.domain].subject,
        status=status, score=1.0 if status is OutcomeStatus.WIN else -1.0,
        resolved_ts=ts,
    )


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
    real = og._get_conn

    def _broken(*a, **k):
        raise sqlite3.OperationalError("database or disk is full")

    og._get_conn = _broken
    return lambda: setattr(og, "_get_conn", real)


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

        report = oc.commit(contract, [nameless])

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

        report = oc.commit(contract, [_outcome(contract, "a"), _outcome(contract, "b")])

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
            report = oc.commit(contract, [_outcome(contract, "a")])
        finally:
            restore()

        assert not report.accounted

    def test_a_rejected_ledger_write_is_not_reported_as_handled(
        self, contract, index, monkeypatch,
    ):
        if contract.feeds_gravity:
            _make_star(index, contract)
        monkeypatch.setattr(oc.vledger, "record_outcome", lambda o: False)

        report = oc.commit(contract, [_outcome(contract, "a")])

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
        outcome = _outcome(contract, "a")

        oc.commit(contract, [outcome])
        second = oc.commit(contract, [outcome])

        assert _ids(contract) == ["a"]
        assert second.ledger_written == 0
        assert second.ledger_skipped == 1
        assert second.accounted

    def test_the_accumulated_score_is_not_inflated(self, contract, index):
        if contract.feeds_gravity:
            _make_star(index, contract)
        batch = [_outcome(contract, "a"), _outcome(contract, "b"),
                 _outcome(contract, "c"),
                 _outcome(contract, "d", OutcomeStatus.LOSS)]

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

        oc.commit(contract, [_outcome(contract, "CVE-1|family", OutcomeStatus.LOSS)])
        oc.commit(contract, [_outcome(contract, "CVE-1|family", OutcomeStatus.WIN)])

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
            report = oc.commit(contract, [_outcome(contract, "a")])
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
            report = oc.commit(contract, [_outcome(contract, "a")])
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

        oc.commit(contract, [_outcome(contract, "a"),
                             _outcome(contract, "b", OutcomeStatus.LOSS)])

        assert _verdicts(index, fingerprint) == ["win", "loss"]

    def test_a_missing_star_parks_the_result_instead_of_losing_it(
        self, contract, index,
    ):
        report = oc.commit(contract, [_outcome(contract, "a")])

        assert og.pending_count(domain=contract.domain) == 1
        assert report.ledger_written == 1 or contract.replayable

        fingerprint = _make_star(index, contract)
        oc.recover(contract)

        assert _verdicts(index, fingerprint) == ["win"]
        assert og.pending_count(domain=contract.domain) == 0

    def test_recovery_is_idempotent(self, contract, index):
        fingerprint = _make_star(index, contract)
        oc.commit(contract, [_outcome(contract, "a"), _outcome(contract, "b")])

        for _ in range(4):
            oc.recover(contract)

        assert _verdicts(index, fingerprint) == ["win", "win"]
        assert og.applied_count(domain=contract.domain) == 2
        assert len(vledger.load_outcomes(contract.domain)) == 2

    def test_provenance_names_the_domain_cycle(self, contract, index):
        _make_star(index, contract)
        oc.commit(contract, [_outcome(contract, "a")])

        rows = og.provenance(domain=contract.domain)
        assert len(rows) == 1
        assert rows[0]["source"] == contract.source
        assert rows[0]["prediction_id"] == "a"
