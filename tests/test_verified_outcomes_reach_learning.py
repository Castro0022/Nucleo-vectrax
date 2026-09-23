"""
tests/test_verified_outcomes_reach_learning.py — El ciclo real de aprendizaje.

QUÉ DEMUESTRA
-------------
El recorrido COMPLETO, extremo a extremo, con código real en cada tramo:

    resultado verificado → patrón cualificado → convergencia LEARNED → criterio

y, sobre todo, el tramo que estaba roto y hacía que ninguna convergencia
llegara nunca a LEARNED: los `Outcome` verificados de market y de freight no
llegaban a la historia graduable del `GravityRecord` que interroga
`qualify_pattern()`.

Nada se simula donde importa: los resultados los produce el `OutcomeAdapter`
real de cada dominio contra su verdad objetiva (el precio realizado, la
entrega a tiempo), la estrella es un `GravityIndex` de verdad sobre un fichero
temporal, y la cualificación recorre `cycle_stats_fetcher` ->
`derive_pattern_stats` sin dobles.

QUÉ NO SE TOCA
--------------
Ningún umbral. `domain_knowledge.MIN_SAMPLE` (15), `MIN_WIN_RATE` (55 %) y
`MIN_EXPECTANCY` (0.0) se leen de la política de producción tal cual están; las
pruebas construyen evidencia suficiente para superarlos, no umbrales que se
dejen superar.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.learn import causal_learning as cl  # noqa: E402
from core.learn import convergence_registry as reg  # noqa: E402
from core.learn import outcome_gravity as og  # noqa: E402
from core.learn import verification_ledger as vledger  # noqa: E402
from core.learn.gravity_engine import GravityIndex  # noqa: E402
from connectors.etoro import verification_cycle as market_vc  # noqa: E402
from connectors.freight import verification_cycle as freight_vc  # noqa: E402


# ===========================================================================
# Material real de cada dominio
# ===========================================================================

class _Signal:
    """Una MarketSignal resuelta: los campos que lee `_signal_to_pair`.

    Se construye a mano en vez de cargarla del recorder para que la prueba
    controle el precio realizado, que es la verdad objetiva del dominio.
    """

    def __init__(self, signal_id, symbol, direction, entry, outcome_price):
        self.signal_id = signal_id
        self.symbol = symbol
        self.direction = direction
        self.entry_price = entry
        self.price = entry
        self.invalidation_price = None
        self.outcome_price = outcome_price
        self.status = "resolved"


def _win_signal(i: int, symbol: str = "AAPL") -> _Signal:
    """Movimiento a favor del +1 %: muy por encima de MIN_WIN_PCT (0.3 %)."""
    return _Signal(f"sig-win-{symbol}-{i}", symbol, "buy", 100.0, 101.0)


def _loss_signal(i: int, symbol: str = "AAPL") -> _Signal:
    """Movimiento en contra del -1 %: por debajo de -MIN_LOSS_PCT (0.2 %)."""
    return _Signal(f"sig-loss-{symbol}-{i}", symbol, "buy", 100.0, 99.0)


def _neutral_signal(i: int, symbol: str = "AAPL") -> _Signal:
    """Movimiento del +0.05 %: ni acierto ni fallo -> NEUTRAL."""
    return _Signal(f"sig-flat-{symbol}-{i}", symbol, "buy", 100.0, 100.05)


class _FreightEvent:
    """Una entrega REAL verificada.

    `source` es un feed real a propósito. Desde que la procedencia es
    consecuente (ver `core/learn/outcome_contract.py`), una entrega de
    "simulator" se registra pero NO gradúa el patrón, y estas pruebas van
    sobre el recorrido del aprendizaje, no sobre la exclusión de lo simulado
    —que tiene las suyas en `test_outcome_contract_conformance.py`.
    """

    __slots__ = ("event_type", "data", "source", "ts")

    def __init__(self, event_type, data, source="dat_feed", ts=0.0):
        self.event_type = event_type
        self.data = dict(data)
        self.source = source
        self.ts = ts


def _delivery(i: int, on_time: bool, carrier: str = "FastHaul") -> _FreightEvent:
    return _FreightEvent("delivery_complete", {
        "origin": "Atlanta", "destination": "Miami", "carrier": carrier,
        "transit_days": 3 if on_time else 6, "on_time": on_time,
        "region": "Southeast",
    }, ts=1000.0 + i)


BOOKING_DATA = {
    "origin": "Atlanta", "destination": "Miami", "carrier": "FastHaul",
    "rate_usd": 900.0, "trucks_available": 12, "region": "Southeast",
}


# ===========================================================================
# Aislamiento: índice de gravedad real, en un fichero temporal
# ===========================================================================

@pytest.fixture
def index(tmp_path, monkeypatch) -> GravityIndex:
    """Un `GravityIndex` REAL sobre disco temporal, instalado como el vivo.

    Es un índice de verdad —con su lock, su lectura y su escritura— y no un
    doble, porque parte de lo que hay que demostrar es que el resultado
    sobrevive a la persistencia y que la ingesta no lo expulsa.
    """
    import core.learn.gravity_engine as ge

    gi = GravityIndex(path=str(tmp_path / "gravity" / "gravity_index.json"))
    monkeypatch.setattr(ge, "get_gravity_index", lambda: gi, raising=False)
    monkeypatch.setattr(ge, "_index", gi, raising=False)
    return gi


def _verdicts(index: GravityIndex, fingerprint: str) -> list:
    """Solo los veredictos de la estrella, sin los `prediction_id`.

    Cada entrada de `verified_outcomes` es {"status": ..., "id": ...}; el id
    hace idempotente la escritura y aquí estorba.
    """
    rec = index.get(fingerprint)
    return [e["status"] for e in rec.verified_outcomes] if rec else []


def _make_star(index: GravityIndex, fingerprint: str, domain: str, intent: str = "") -> None:
    """Crea la estrella por la vía normal: una ingesta observada."""
    index.record_event(
        fingerprint=fingerprint, cc_score=0.6, impact="medium",
        domain=domain, intent=intent or fingerprint, outcome="observed",
        summary=fingerprint,
    )


# ===========================================================================
# 1. El veredicto verificado llega a la estrella correcta
# ===========================================================================

class TestTheVerdictReachesTheStar:

    def test_market_result_lands_on_the_symbol_star(self, index):
        _make_star(index, "market:AAPL", "market", intent="AAPL")

        score = market_vc.verify_signals([_win_signal(1), _loss_signal(2)])

        assert score.n_decisive == 2
        assert _verdicts(index, "market:AAPL") == ["win", "loss"]

    def test_the_fingerprint_is_the_one_the_real_feeder_creates(self, index):
        """Paridad COMPROBADA con `learning_engine._feed_gravity`.

        La estrella no la crea la prueba: la crea el productor real. Si las dos
        expresiones divergieran, el resultado aterrizaría en una estrella
        inexistente y se descartaría en silencio — que es exactamente el modo
        en que el ciclo estaba roto. Se comprueba el comportamiento, no el
        texto del módulo: un texto puede coincidir y el efecto no.
        """
        from connectors.etoro import learning_engine

        assert learning_engine._feed_gravity(["aapl"]) == 1
        created = list(index.load_raw())

        assert created == [market_vc.star_fingerprint_for("aapl")]

        market_vc.verify_signals([_win_signal(1)])
        assert _verdicts(index, created[0]) == ["win"]

    def test_freight_result_lands_on_the_booking_star(self, index):
        """La entrega acredita la estrella de la RESERVA de ese lane/carrier."""
        from core.domain_ingester import star_fingerprint

        booking_fp = star_fingerprint("freight_logistics", "load_booking", BOOKING_DATA)
        _make_star(index, booking_fp, "freight_logistics", intent="load_booking")

        freight_vc.verify_events([_delivery(1, True), _delivery(2, False)])

        assert index.get(booking_fp) is not None, "no encontró la estrella de la reserva"
        assert _verdicts(index, booking_fp) == ["win", "loss"]

    def test_the_freight_fingerprint_is_computed_with_the_ingester_function(self):
        """La identidad la calcula la MISMA función que creó la estrella."""
        from core.domain_ingester import star_fingerprint

        expected = star_fingerprint("freight_logistics", "load_booking", BOOKING_DATA)
        assert freight_vc.star_fingerprint_for(_delivery(1, True).data) == expected
        assert freight_vc.star_fingerprint_for(_delivery(2, False).data) == expected


# ===========================================================================
# 2. La identidad acreditada NO contiene el resultado
# ===========================================================================

class TestTheCreditedIdentityIsNotCircular:

    def test_on_time_and_late_deliveries_share_one_star(self, index):
        """Si no lo compartieran, el win_rate sería una tautología.

        El template firma `delivery_complete` por ``region|on_time``: la
        estrella del propio evento lleva el veredicto dentro. Acreditarla daría
        una estrella con 100 % de aciertos y otra con 0 %, ambas por
        construcción. Una sola estrella para ambos desenlaces es lo que hace
        que el win_rate mida algo.
        """
        assert (freight_vc.star_fingerprint_for(_delivery(1, True).data)
                == freight_vc.star_fingerprint_for(_delivery(2, False).data))

    def test_the_credited_identity_carries_no_verdict_field(self):
        fp = freight_vc.star_fingerprint_for(_delivery(1, True).data)
        for field in freight_vc._OUTCOME_FIELDS:
            assert f"{field}=" not in fp, fp
            assert f"{field}~" not in fp, fp

    def test_a_star_whose_identity_embeds_the_verdict_is_never_credited(self, index):
        """La estrella del propio evento de resultado queda intacta."""
        from core.domain_ingester import star_fingerprint

        own_fp = star_fingerprint(
            "freight_logistics", "delivery_complete", _delivery(1, True).data,
        )
        assert "on_time=True" in own_fp  # el veredicto está en la identidad
        _make_star(index, own_fp, "freight_logistics")
        _make_star(
            index,
            star_fingerprint("freight_logistics", "load_booking", BOOKING_DATA),
            "freight_logistics",
        )

        freight_vc.verify_events([_delivery(1, True)])

        assert _verdicts(index, own_fp) == []


# ===========================================================================
# 3. Sin duplicados
# ===========================================================================

class TestNoDuplicates:

    def test_reverifying_the_same_batch_adds_nothing(self, index):
        _make_star(index, "market:AAPL", "market")
        batch = [_win_signal(1), _win_signal(2), _loss_signal(3)]

        market_vc.verify_signals(batch)
        first = _verdicts(index, "market:AAPL")
        market_vc.verify_signals(batch)

        assert _verdicts(index, "market:AAPL") == first

    def test_the_second_pass_is_counted_as_duplicate(self, index):
        _make_star(index, "market:AAPL", "market")
        outcome = _resolved_market_outcome(_win_signal(1))

        first = og.apply_verified_outcome("market:AAPL", outcome, source="test")
        second = og.apply_verified_outcome("market:AAPL", outcome, source="test")

        assert first == og.APPLIED
        assert second == og.DUPLICATE
        assert og.applied_count(fingerprint="market:AAPL") == 1

    def test_freight_deduplicates_by_the_event_itself(self, index):
        """`FreightEvent` no tiene id; la identidad se deriva del evento."""
        from core.domain_ingester import star_fingerprint

        fp = star_fingerprint("freight_logistics", "load_booking", BOOKING_DATA)
        _make_star(index, fp, "freight_logistics")
        batch = [_delivery(1, True), _delivery(2, False)]

        freight_vc.verify_events(batch)
        freight_vc.verify_events(batch)

        assert _verdicts(index, fp) == ["win", "loss"]

    def test_two_distinct_events_are_not_confused(self, index):
        from core.domain_ingester import star_fingerprint

        fp = star_fingerprint("freight_logistics", "load_booking", BOOKING_DATA)
        _make_star(index, fp, "freight_logistics")

        # Mismo lane/carrier y mismo desenlace, pero eventos distintos (ts).
        freight_vc.verify_events([_delivery(1, True), _delivery(2, True)])

        assert _verdicts(index, fp) == ["win", "win"]


# ===========================================================================
# 4. No se inventan resultados ni patrones
# ===========================================================================

def _resolved_market_outcome(sig):
    pred, obs = market_vc._signal_to_pair(sig)
    return market_vc._ADAPTER.resolve(pred, obs)


class TestNothingIsInvented:

    def test_a_result_without_a_star_creates_no_star(self, index):
        outcome = _resolved_market_outcome(_win_signal(1))

        result = og.apply_verified_outcome("market:NOPE", outcome, source="test")

        assert result == og.DEFERRED
        assert index.get("market:NOPE") is None

    def test_an_unapplied_result_is_parked_not_discarded(self, index):
        """La reclamación se deshace y el resultado queda aparcado.

        Si la fila de deduplicación quedara escrita, el resultado se habría
        dado por aplicado sin estarlo. Si se descartara sin más, se habría
        perdido en cuanto la estrella existiera.
        """
        outcome = _resolved_market_outcome(_win_signal(1))
        assert og.apply_verified_outcome("market:LATE", outcome, source="test") == og.DEFERRED
        assert og.applied_count(fingerprint="market:LATE") == 0
        assert og.pending_count(domain="market") == 1

        _make_star(index, "market:LATE", "market")
        assert og.retry_pending("market")[og.RECOVERED] == 1
        assert _verdicts(index, "market:LATE") == ["win"]
        assert og.pending_count(domain="market") == 0

    def test_neutral_results_are_not_graded(self, index):
        """Un NEUTRAL no es acierto ni fallo: no ocupa plaza en la historia."""
        _make_star(index, "market:AAPL", "market")

        market_vc.verify_signals([_neutral_signal(1), _win_signal(2)])

        assert _verdicts(index, "market:AAPL") == ["win"]

    def test_the_verdict_does_not_inflate_the_convergence_mass(self, index):
        """`hits` alimenta `combined_hits`: un resultado no puede moverlo.

        Si los resultados entraran por `record_event()`, "se observó muchas
        veces" y "se acertó muchas veces" se confundirían.
        """
        _make_star(index, "market:AAPL", "market")
        before = index.get("market:AAPL")
        hits, cc, tier = before.hits, before.cc_score, before.tier

        market_vc.verify_signals([_win_signal(i) for i in range(5)])

        after = index.get("market:AAPL")
        assert (after.hits, after.cc_score, after.tier) == (hits, cc, tier)
        assert len(after.verified_outcomes) == 5


# ===========================================================================
# 5. La regresión de fondo: la observación ya no expulsa los resultados
# ===========================================================================

class TestObservationDoesNotEvictResults:

    def test_a_full_cycle_of_ingests_does_not_erase_the_verdicts(self, index):
        """Este era el defecto estructural.

        `outcome_history` está acotada (MAX_OUTCOME_HISTORY = 20) y la alimenta
        CADA ingesta. Un ciclo freight de 20 eventos desplazaba la ventana
        entera, así que un resultado verificado guardado ahí desaparecía antes
        de que `qualify_pattern()` pudiera contarlo. Ahora viven en listas
        separadas.
        """
        from core.learn.gravity_engine import MAX_OUTCOME_HISTORY

        _make_star(index, "market:AAPL", "market")
        market_vc.verify_signals([_win_signal(i) for i in range(16)]
                                 + [_loss_signal(i) for i in range(4)])
        assert len(index.get("market:AAPL").verified_outcomes) == 20

        for _ in range(MAX_OUTCOME_HISTORY + 5):
            _make_star(index, "market:AAPL", "market")

        verdicts = _verdicts(index, "market:AAPL")
        assert verdicts.count("win") == 16
        assert verdicts.count("loss") == 4
        assert "observed" not in verdicts

    def test_the_market_feeder_no_longer_writes_a_pattern_summary(self):
        """`_feed_gravity` escribía "WR=62% E=+0.450%" como si fuera un
        resultado. No es graduable y ocupaba plaza en una lista acotada.

        Se comprueba sobre el AST, no sobre el texto: `outcome` solo puede
        recibir la constante "observed" dentro de la función. Un `in src`
        pasaría igual si alguien reintrodujera la asignación después.
        """
        import ast
        import inspect
        import textwrap
        from connectors.etoro import learning_engine

        tree = ast.parse(textwrap.dedent(inspect.getsource(learning_engine._feed_gravity)))
        assigned = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "outcome" for t in node.targets)
        ]
        assert assigned, "`outcome` ya no se asigna: revisar esta prueba"
        for value in assigned:
            assert isinstance(value, ast.Constant) and value.value == "observed", (
                f"`outcome` recibe algo que no es la constante 'observed': "
                f"{ast.dump(value)}"
            )


# ===========================================================================
# 6. Procedencia
# ===========================================================================

class TestProvenance:

    def test_every_application_says_where_it_came_from(self, index):
        _make_star(index, "market:AAPL", "market")

        market_vc.verify_signals([_win_signal(7)])

        rows = og.provenance(fingerprint="market:AAPL")
        assert len(rows) == 1
        row = rows[0]
        assert row["prediction_id"] == "sig-win-AAPL-7"
        assert row["domain"] == "market"
        assert row["subject"] == "AAPL"
        assert row["status"] == "win"
        assert row["source"] == "etoro.verification_cycle"
        assert row["applied_at"] > 0

    def test_freight_provenance_names_its_cycle(self, index):
        from core.domain_ingester import star_fingerprint

        fp = star_fingerprint("freight_logistics", "load_booking", BOOKING_DATA)
        _make_star(index, fp, "freight_logistics")

        freight_vc.verify_events([_delivery(1, True)])

        rows = og.provenance(domain="freight_logistics")
        assert len(rows) == 1
        assert rows[0]["source"] == "freight.verification_cycle"
        assert rows[0]["subject"] == "Southeast|FastHaul"
        assert rows[0]["prediction_id"]  # ya no queda vacío

    def test_a_reader_never_creates_the_store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "empty"))
        assert og.provenance() == []
        assert og.applied_count() == 0
        assert og.pending_outcomes() == []
        assert og.pending_count() == 0
        assert og.retry_pending("market") == {k: 0 for k in og.RESULTS}
        assert not Path(og.store_path()).exists()


class TestTheStorePathIsNeverFrozen:
    """La ruta se resuelve EN CADA LLAMADA, no al importar.

    Es la misma clase de defecto que `tests/test_vault_write_isolation.py`
    documenta para `learned_rules`: un valor por defecto evaluado al importar
    congelaba la ruta y el aislamiento del conftest no tenía efecto, así que la
    suite escribía en el vault de producción. Este almacén es nuevo y no puede
    heredar ese fallo.
    """

    def test_the_env_var_is_honoured_at_call_time(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "uno"))
        assert og.store_path() == str(tmp_path / "uno" / og.STORE_FILENAME)

        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "dos"))
        assert og.store_path() == str(tmp_path / "dos" / og.STORE_FILENAME)

    def test_no_override_preserves_the_production_path(self, monkeypatch):
        monkeypatch.delenv("VECTRAX_VAULT_DIR", raising=False)
        assert og.store_path() == str(
            Path(og.PRODUCTION_VAULT_DIR) / og.STORE_FILENAME
        )

    def test_an_empty_override_falls_back_to_production(self, monkeypatch):
        monkeypatch.setenv("VECTRAX_VAULT_DIR", "")
        assert og.store_path() == str(
            Path(og.PRODUCTION_VAULT_DIR) / og.STORE_FILENAME
        )

    def test_an_explicit_db_path_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "ignorado"))
        assert og.store_path(str(tmp_path / "x.db")) == str(tmp_path / "x.db")


# ===========================================================================
# 7. EL RECORRIDO COMPLETO
# ===========================================================================

LIVE = {"market:AAPL": "market", "freight_logistics:LANE-7": "freight_logistics"}

CANDIDATE = {
    "type": "intent_overlap",
    "star_a": "market:AAPL",
    "star_b": "freight_logistics:LANE-7",
    "combined_cc": 0.77,
    "combined_hits": 9,
    "domains": ["market", "freight_logistics"],
}


class TestTheFullJourney:
    """resultado verificado → patrón cualificado → LEARNED → criterio."""

    @staticmethod
    def _build_real_evidence(index):
        """16 aciertos y 4 fallos REALES en cada patrón de la convergencia.

        Los produce el adaptador de cada dominio contra su verdad objetiva, y
        llegan a la estrella por el mismo camino que en producción. 20
        resultados graduados superan MIN_SAMPLE (15) y un 80 % supera
        MIN_WIN_RATE (55 %) — los umbrales se cumplen, no se cambian.
        """
        _make_star(index, "market:AAPL", "market", intent="AAPL")
        _make_star(index, "freight_logistics:LANE-7", "freight_logistics")

        market_vc.verify_signals([_win_signal(i) for i in range(16)]
                                 + [_loss_signal(i) for i in range(4)])

        # El otro patrón de la convergencia, por la misma puerta pública.
        from core.learn.outcome_adapter import Outcome, OutcomeStatus
        lane = [
            (
                "freight_logistics:LANE-7",
                Outcome(
                    prediction_id=f"lane7-{i}", domain="freight_logistics",
                    subject="LANE-7",
                    status=OutcomeStatus.WIN if i < 16 else OutcomeStatus.LOSS,
                    score=1.0 if i < 16 else -1.0,
                ),
            )
            for i in range(20)
        ]
        og.apply_verified_outcomes(lane, source="test.freight")

    def test_step_1_the_verified_result_reaches_the_star(self, index):
        self._build_real_evidence(index)

        for fp in ("market:AAPL", "freight_logistics:LANE-7"):
            assert _verdicts(index, fp).count("win") == 16, fp
            assert _verdicts(index, fp).count("loss") == 4, fp

    def test_step_2_the_pattern_qualifies(self, index):
        self._build_real_evidence(index)
        policy = cl.build_production_policy("market")

        stats = cl.qualify_pattern("market:AAPL", policy)

        assert stats.qualified, stats.reason
        assert stats.sample_size == 20
        assert stats.win_rate == pytest.approx(80.0)

    def test_step_2b_without_the_bridge_it_could_never_qualify(self, index):
        """El estado anterior a esta corrección, explícito.

        La estrella existe y se observó, pero nadie le llevó un resultado: no
        hay nada que graduar y `qualify_pattern` lo dice.
        """
        _make_star(index, "market:AAPL", "market")
        policy = cl.build_production_policy("market")

        stats = cl.qualify_pattern("market:AAPL", policy)

        assert not stats.qualified
        assert "no tiene outcomes graduados" in stats.reason

    def test_step_3_the_convergence_reaches_learned(self, index, tmp_path):
        self._build_real_evidence(index)

        result = reg.record_convergence_snapshot(
            [CANDIDATE], live_fingerprints=LIVE,
            db_path=str(tmp_path / "registry" / "convergence.db"),
        )

        assert result["created"] == 1
        assert result["causal"]["learned"] >= 1, result["causal"]
        learned = cl.list_learnings(state=cl.STATE_LEARNED)
        assert learned, "ninguna convergencia llegó a LEARNED"

    def test_step_4_the_criterion_applies_it(self, index, tmp_path):
        from core.learn import criterion

        self._build_real_evidence(index)
        reg.record_convergence_snapshot(
            [CANDIDATE], live_fingerprints=LIVE,
            db_path=str(tmp_path / "registry" / "convergence.db"),
        )

        causal = [
            e for e in criterion.rank_domain_evidence("market")
            if "causal_learning" in e.get("sources", [])
        ]

        assert causal, "el aprendizaje no llegó al criterio"
        assert causal[0]["sample_size"] == 20
        assert causal[0]["win_rate"] == pytest.approx(80.0)

    def test_the_whole_journey_in_one_pass(self, index, tmp_path):
        """El recorrido entero, de una sola vez, sin atajos."""
        from core.learn import criterion

        # 1. Resultados verificados contra la verdad del dominio.
        self._build_real_evidence(index)
        assert _verdicts(index, "market:AAPL").count("win") == 16

        # 2. El patrón cualifica con métricas REALES.
        stats = cl.qualify_pattern("market:AAPL", cl.build_production_policy("market"))
        assert stats.qualified, stats.reason

        # 3. La convergencia pasa a LEARNED.
        out = reg.record_convergence_snapshot(
            [CANDIDATE], live_fingerprints=LIVE,
            db_path=str(tmp_path / "registry" / "convergence.db"),
        )
        assert out["causal"]["learned"] >= 1

        # 4. El criterio lo aplica.
        assert any(
            "causal_learning" in e.get("sources", [])
            for e in criterion.rank_domain_evidence("market")
        )

        # 5. Y se puede decir de dónde salió cada pieza de evidencia.
        rows = og.provenance(fingerprint="market:AAPL")
        assert len(rows) == 20
        assert {r["source"] for r in rows} == {"etoro.verification_cycle"}


# ===========================================================================
# 8. El ledger de verificación sigue siendo la fuente; esto es aditivo
# ===========================================================================

class TestTheLedgerIsStillWritten:

    def test_the_verification_ledger_keeps_receiving_every_outcome(self, index):
        """Alimentar la gravedad no sustituye al ledger: lo complementa."""
        _make_star(index, "market:AAPL", "market")

        market_vc.verify_signals([_win_signal(1), _loss_signal(2), _neutral_signal(3)])

        outcomes = vledger.load_outcomes("market")
        assert len(outcomes) == 3  # el NEUTRAL sí se persiste en el ledger
        assert {o.prediction_id for o in outcomes} == {
            "sig-win-AAPL-1", "sig-loss-AAPL-2", "sig-flat-AAPL-3",
        }


# ===========================================================================
# 9. Los dos fallos bajo estrés encontrados en la revisión del PR #129
# ===========================================================================

class TestTheResultIsNeverLost:
    """Fallo 1: Market marcaba la señal como verificada aunque el resultado no
    hubiera llegado a ninguna estrella.

    El marcador de `signal_id` vive FUERA de `outcome_gravity`
    (`market_verified.json`): una vez marcada, `run_market_verification` no
    vuelve a presentar esa señal jamás. Así que "el próximo ciclo lo
    reintentará" era falso — el resultado quedaba perdido aunque la estrella
    apareciera después. Se reproduce el caso exacto: verificar sin estrella,
    crear la estrella, repetir el ciclo.
    """

    @staticmethod
    def _install_recorder(monkeypatch, signals):
        """Sustituye el cargador de señales para ejercitar el ciclo REAL
        (`run_market_verification`), incluido su marcador de verificadas."""
        import connectors.etoro.signal_recorder as rec

        class _Status:
            class PENDING:
                value = "pending"

        monkeypatch.setattr(rec, "load_signals", lambda *a, **k: list(signals),
                            raising=False)
        monkeypatch.setattr(rec, "SignalStatus", _Status, raising=False)

    def test_a_result_verified_before_its_star_exists_is_not_lost(
        self, index, monkeypatch,
    ):
        sig = _win_signal(1)
        self._install_recorder(monkeypatch, [sig])

        # Ciclo 1: la estrella NO existe todavía.
        market_vc.run_market_verification()
        assert index.get("market:AAPL") is None
        assert og.pending_count(domain="market") == 1

        # La señal ya quedó marcada: el ciclo no la volverá a presentar.
        assert sig.signal_id in market_vc._load_verified_ids()

        # La estrella aparece después.
        _make_star(index, "market:AAPL", "market")

        # Ciclo 2: sin señales nuevas que procesar, el resultado se recupera.
        market_vc.run_market_verification()

        assert _verdicts(index, "market:AAPL") == ["win"]
        assert og.pending_count(domain="market") == 0
        assert og.applied_count(fingerprint="market:AAPL") == 1

    def test_the_recovered_result_qualifies_the_pattern(self, index, monkeypatch):
        """Recuperar no es solo archivar: el patrón acaba cualificando."""
        sigs = [_win_signal(i) for i in range(16)] + [_loss_signal(i) for i in range(4)]
        self._install_recorder(monkeypatch, sigs)

        market_vc.run_market_verification()          # sin estrella: 20 aparcados
        assert og.pending_count(domain="market") == 20

        _make_star(index, "market:AAPL", "market")
        market_vc.run_market_verification()          # se recuperan

        stats = cl.qualify_pattern("market:AAPL", cl.build_production_policy("market"))
        assert stats.qualified, stats.reason
        assert stats.sample_size == 20
        assert stats.win_rate == pytest.approx(80.0)

    def test_the_ledger_is_not_written_twice_by_the_retry(self, index, monkeypatch):
        """El reintento toca la gravedad, no el verification_ledger.

        Si el reintento re-resolviera la señal, el DomainScore acumulado se
        inflaría. Por eso lo aparcado es el `Outcome` ya resuelto, no la señal.
        """
        self._install_recorder(monkeypatch, [_win_signal(1)])

        market_vc.run_market_verification()
        _make_star(index, "market:AAPL", "market")
        market_vc.run_market_verification()
        market_vc.run_market_verification()

        assert len(vledger.load_outcomes("market")) == 1

    def test_a_star_that_never_appears_keeps_the_result_waiting(self, index):
        """No se descarta ni se da por bueno: sigue esperando, y se puede ver."""
        outcome = _resolved_market_outcome(_win_signal(1))
        og.apply_verified_outcome("market:GHOST", outcome, source="test")

        for _ in range(5):
            assert og.retry_pending("market")[og.RECOVERED] == 0

        rows = og.pending_outcomes(domain="market")
        assert len(rows) == 1
        assert rows[0]["fingerprint"] == "market:GHOST"
        assert rows[0]["status"] == "win"
        assert rows[0]["attempts"] >= 5


class TestACrashCannotDoubleCount:
    """Fallo 2: una caída entre la escritura en gravedad y la confirmación en
    SQLite permitía que el reintento añadiera el mismo resultado otra vez,
    inflando el win_rate y desplazando otro resultado de la ventana de 20.

    La escritura en gravedad es ahora idempotente por `prediction_id`: el
    reintento reconoce lo ya anotado.
    """

    @staticmethod
    def _crash_after_gravity(index):
        """Escribe en gravedad y revienta antes de que el llamador confirme.

        Devuelve la función que deshace SOLO esta sustitución. No se usa
        `monkeypatch.undo()`: revertiría también el índice temporal que instala
        la fixture `index`, y el reintento acabaría mirando al índice vivo.
        """
        real = index.record_verified_outcomes

        def _boom(triples):
            real(triples)
            raise RuntimeError("caída simulada entre gravedad y commit")

        index.record_verified_outcomes = _boom

        def _restore():
            index.record_verified_outcomes = real

        return _restore

    def test_the_retry_after_a_crash_does_not_add_it_twice(self, index):
        _make_star(index, "market:AAPL", "market")
        outcome = _resolved_market_outcome(_win_signal(1))

        restore = self._crash_after_gravity(index)
        og.apply_verified_outcome("market:AAPL", outcome, source="test")

        # La gravedad SÍ quedó escrita; la procedencia no se confirmó.
        assert _verdicts(index, "market:AAPL") == ["win"]
        assert og.applied_count(fingerprint="market:AAPL") == 0

        # El reintento no duplica y deja la procedencia coherente.
        restore()
        assert og.apply_verified_outcome("market:AAPL", outcome, source="test") == og.APPLIED

        assert _verdicts(index, "market:AAPL") == ["win"]
        assert og.applied_count(fingerprint="market:AAPL") == 1

    def test_a_crash_does_not_evict_another_result_from_the_window(self, index):
        """Lo que hacía grave al doble conteo: expulsaba evidencia real."""
        from core.learn.gravity_engine import MAX_OUTCOME_HISTORY

        _make_star(index, "market:AAPL", "market")
        market_vc.verify_signals([_win_signal(i) for i in range(16)]
                                 + [_loss_signal(i) for i in range(3)])
        assert len(index.get("market:AAPL").verified_outcomes) == 19

        last = _resolved_market_outcome(_loss_signal(99))
        restore = self._crash_after_gravity(index)
        og.apply_verified_outcome("market:AAPL", last, source="test")
        restore()
        og.apply_verified_outcome("market:AAPL", last, source="test")

        verdicts = _verdicts(index, "market:AAPL")
        assert len(verdicts) == MAX_OUTCOME_HISTORY
        assert verdicts.count("win") == 16      # ninguno expulsado
        assert verdicts.count("loss") == 4

    def test_a_crashed_retry_is_idempotent_across_many_results(self, index):
        """El mismo caso sobre un lote, no sobre un único resultado."""
        _make_star(index, "market:AAPL", "market")
        sigs = [_win_signal(i) for i in range(8)]
        pairs = [("market:AAPL", _resolved_market_outcome(s)) for s in sigs]

        restore = self._crash_after_gravity(index)
        og.apply_verified_outcomes(pairs, source="test")
        assert len(index.get("market:AAPL").verified_outcomes) == 8
        assert og.applied_count(fingerprint="market:AAPL") == 0

        restore()
        counts = og.apply_verified_outcomes(pairs, source="test")

        assert counts[og.APPLIED] == 8
        assert len(index.get("market:AAPL").verified_outcomes) == 8
        assert og.applied_count(fingerprint="market:AAPL") == 8


# ===========================================================================
# 10. Los dos fallos bajo carga de la revisión de 5b5a11d
# ===========================================================================

def _park(index, fingerprint: str, n: int, tag: str) -> None:
    """Aparca `n` resultados para una estrella que todavía no existe."""
    from core.learn.outcome_adapter import Outcome, OutcomeStatus

    og.apply_verified_outcomes(
        [
            (fingerprint, Outcome(
                prediction_id=f"{tag}-{i}", domain="market",
                subject=fingerprint.split(":")[-1],
                status=OutcomeStatus.WIN, score=1.0,
            ))
            for i in range(n)
        ],
        source="test",
        index=index,
    )


class TestNoPendingResultIsStarved:
    """Fallo 3: el reintento tomaba siempre los aparcados MÁS ANTIGUOS.

    Con `ORDER BY deferred_at ASC LIMIT n`, si esos n seguían sin estrella se
    volvían a elegir en cada ciclo y ningún aparcado posterior se examinaba
    jamás — ni siquiera uno cuya estrella ya existía. Un atasco permanente
    detrás de un grupo bloqueado.
    """

    def test_a_blocked_head_does_not_hide_a_ready_result(self, index):
        _park(index, "market:BLOCKED", 3, "blocked")   # llegan ANTES
        _park(index, "market:READY", 2, "ready")       # llegan DESPUÉS
        _make_star(index, "market:READY", "market")    # y SÍ tienen estrella

        # El presupuesto solo alcanza para el grupo bloqueado.
        recovered = 0
        for _ in range(3):
            recovered += og.retry_pending("market", index=index, limit=3)[og.RECOVERED]

        assert recovered == 2, "los aparcados de detrás nunca se examinaron"
        assert _verdicts(index, "market:READY") == ["win", "win"]
        assert og.pending_count(domain="market") == 3   # los bloqueados siguen

    def test_every_parked_result_is_examined_within_a_full_sweep(self, index):
        """La tabla entera se recorre en ceil(total / limit) ciclos."""
        for k in range(4):
            _park(index, f"market:S{k}", 5, f"s{k}")
        assert og.pending_count(domain="market") == 20

        seen = set()
        for _ in range(4):                       # 20 aparcados / limit 5
            before = {(r["fingerprint"], r["prediction_id"], r["attempts"])
                      for r in og.pending_outcomes(domain="market", limit=100)}
            og.retry_pending("market", index=index, limit=5)
            after = {(r["fingerprint"], r["prediction_id"], r["attempts"])
                     for r in og.pending_outcomes(domain="market", limit=100)}
            seen |= {(f, p) for f, p, _a in after} - {(f, p) for f, p, _a in before}

        # Tras un barrido completo, todos han sido intentados al menos 2 veces
        # (la del aparcado + la del reintento): ninguno quedó sin examinar.
        assert all(r["attempts"] >= 2
                   for r in og.pending_outcomes(domain="market", limit=100))

    def test_the_rotation_state_lives_on_disk_not_in_memory(self, index):
        """El turno está persistido: cada llamada abre su propia conexión.

        Un reintento que guardara el cursor en memoria volvería a empezar por
        el principio en cada arranque del proceso, y el grupo bloqueado de
        cabecera atascaría la cola otra vez tras cada reinicio.
        """
        _park(index, "market:BLOCKED", 3, "blocked")
        first = {r["prediction_id"]: r["last_attempt_at"]
                 for r in og.pending_outcomes(domain="market", limit=10)}

        og.retry_pending("market", index=index, limit=3)

        second = {r["prediction_id"]: r["last_attempt_at"]
                  for r in og.pending_outcomes(domain="market", limit=10)}
        assert second.keys() == first.keys()
        assert all(second[k] >= first[k] for k in first)
        assert any(second[k] > first[k] for k in first), (
            "el intento no quedó sellado en disco: la rotación no avanza"
        )

    def test_a_newly_parked_result_waits_its_turn_but_does_land(self, index):
        """Un aparcado nuevo va al final de la cola (acaba de fallar), pero la
        cola avanza: aterriza dentro del barrido, no 'algún día'."""
        _park(index, "market:BLOCKED", 3, "blocked")
        og.retry_pending("market", index=index, limit=3)

        _park(index, "market:READY", 1, "ready")
        _make_star(index, "market:READY", "market")

        # 4 aparcados con presupuesto 3 -> el barrido completo son 2 ciclos.
        recovered = sum(
            og.retry_pending("market", index=index, limit=3)[og.RECOVERED]
            for _ in range(2)
        )

        assert recovered == 1
        assert _verdicts(index, "market:READY") == ["win"]

    def test_a_star_arriving_late_is_still_served(self, index):
        """Tres ciclos bloqueados y la estrella llega al cuarto."""
        _park(index, "market:BLOCKED", 5, "blocked")
        _park(index, "market:LATE", 2, "late")

        for _ in range(3):
            assert og.retry_pending("market", index=index, limit=4)[og.RECOVERED] == 0

        _make_star(index, "market:LATE", "market")
        recovered = sum(
            og.retry_pending("market", index=index, limit=4)[og.RECOVERED]
            for _ in range(3)
        )

        assert recovered == 2
        assert _verdicts(index, "market:LATE") == ["win", "win"]


class TestVerifiedEvidenceIsNeverDiscarded:
    """Fallo 4: al llegar al tope, el código borraba los aparcados más
    antiguos. Era un error mío, y del peor tipo: convertía un problema visible
    —una tabla que crece porque no se crean estrellas— en uno invisible,
    aprendizaje verificado que desaparece sin que nadie lo note. Y el criterio
    lo agravaba: los más antiguos son los que más tiempo llevan esperando.
    """

    def test_crossing_the_threshold_deletes_nothing(self, index, monkeypatch, caplog):
        monkeypatch.setattr(og, "PENDING_ALERT_THRESHOLD", 5)

        _park(index, "market:GHOST", 8, "ghost")

        assert og.pending_count(domain="market") == 8, "se descartó evidencia"
        ids = {r["prediction_id"] for r in og.pending_outcomes(domain="market", limit=50)}
        assert ids == {f"ghost-{i}" for i in range(8)}

    def test_crossing_the_threshold_warns(self, index, monkeypatch, caplog):
        monkeypatch.setattr(og, "PENDING_ALERT_THRESHOLD", 3)

        with caplog.at_level("WARNING", logger="vectrax.outcome_gravity"):
            _park(index, "market:GHOST", 5, "ghost")

        assert any("esperando una estrella" in r.message for r in caplog.records)

    def test_the_oldest_parked_result_still_lands_when_its_star_arrives(
        self, index, monkeypatch,
    ):
        """El que más tiempo llevaba esperando es el que antes se borraba."""
        monkeypatch.setattr(og, "PENDING_ALERT_THRESHOLD", 2)

        _park(index, "market:OLDEST", 1, "oldest")
        for k in range(6):
            _park(index, f"market:NOISE{k}", 3, f"noise{k}")

        _make_star(index, "market:OLDEST", "market")
        for _ in range(8):
            og.retry_pending("market", index=index, limit=4)

        assert _verdicts(index, "market:OLDEST") == ["win"]

    def test_no_bulk_delete_of_pending_evidence_exists(self):
        """Un aparcado solo desaparece al APLICARSE, nunca en bloque.

        El borrado anterior era un `DELETE ... WHERE rowid IN (SELECT ...
        LIMIT n)`: una sentencia que elimina un número arbitrario de filas de
        golpe. Se comprueba que no queda ninguna así, sin depender de cómo
        esté escrita: se cuentan las sentencias y se exige que todas estén
        acotadas a UNA fila por su clave primaria.
        """
        import inspect
        import re

        src = inspect.getsource(og)
        statements = re.findall(
            r"DELETE FROM pending_outcomes\s*(?:\"\s*\n\s*\")?([^\"]*)", src,
        )
        assert statements, "la sentencia cambió de forma: revisar esta prueba"
        for stmt in statements:
            flat = " ".join(stmt.split())
            assert flat.startswith(
                "WHERE fingerprint = ? AND prediction_id = ?"
            ), f"borrado no acotado a una sola fila: {flat!r}"
        assert "rowid IN" not in src
        assert "LIMIT" not in src.split("DELETE FROM pending_outcomes")[1][:200]


# ===========================================================================
# 11. Los dos fallos de la revisión de 0097272
# ===========================================================================

def _market_outcome(i: int, status: str = "win", ts: float = 0.0):
    """Un Outcome de market con instante de resolución explícito."""
    from core.learn.outcome_adapter import Outcome, OutcomeStatus

    return Outcome(
        prediction_id=f"p{i}", domain="market", subject="AAPL",
        status=OutcomeStatus.WIN if status == "win" else OutcomeStatus.LOSS,
        score=1.0 if status == "win" else -1.0,
        resolved_ts=ts or float(1000 + i),
    )


class TestAnOldResultCannotComeBackAsRecent:
    """Fallo 5: tras una interrupción, si entraban más de 20 resultados antes
    del reintento, el interrumpido ya había sido desplazado de la ventana; el
    reintento no lo reconocía por id y lo reinsertaba COMO RECIENTE —
    expulsando a uno genuinamente nuevo y alterando el criterio.

    La comprobación por id solo ve lo que sigue en la ventana. Creer que
    bastaba fue un error de razonamiento mío. La ventana se mantiene ahora
    ordenada por el instante en que cada resultado se resolvió, así que
    reinsertar algo ya superado es inofensivo: vuelve a caer fuera.
    """

    def test_the_interrupted_result_does_not_evict_a_newer_one(self, index):
        from core.learn.gravity_engine import MAX_OUTCOME_HISTORY

        _make_star(index, "market:AAPL", "market")
        old = _market_outcome(0, "loss", ts=1000.0)

        # Se interrumpe justo después de escribir en gravedad.
        real = index.record_verified_outcomes

        def _boom(entries):
            real(entries)
            raise RuntimeError("caída entre gravedad y commit")

        index.record_verified_outcomes = _boom
        og.apply_verified_outcome("market:AAPL", old, source="test")
        index.record_verified_outcomes = real
        assert _verdicts(index, "market:AAPL") == ["loss"]

        # Llegan MÁS de 20 resultados nuevos antes del reintento.
        og.apply_verified_outcomes(
            [("market:AAPL", _market_outcome(i, "win", ts=2000.0 + i))
             for i in range(1, 26)],
            source="test",
        )
        window = _verdicts(index, "market:AAPL")
        assert len(window) == MAX_OUTCOME_HISTORY
        assert window.count("loss") == 0, "el viejo debería haber salido ya"

        # El reintento del interrumpido: ya no lo reconoce por id.
        og.apply_verified_outcome("market:AAPL", old, source="test")

        window = _verdicts(index, "market:AAPL")
        assert len(window) == MAX_OUTCOME_HISTORY
        assert window.count("loss") == 0, (
            "un resultado viejo resucitó y desplazó a uno más reciente"
        )
        assert window.count("win") == MAX_OUTCOME_HISTORY

    def test_the_window_holds_the_most_recent_by_resolution_time(self, index):
        _make_star(index, "market:AAPL", "market")

        # Se aplican desordenados a propósito.
        og.apply_verified_outcomes(
            [("market:AAPL", _market_outcome(i, "win", ts=5000.0 + i))
             for i in range(20)],
            source="test",
        )
        og.apply_verified_outcome(
            "market:AAPL", _market_outcome(99, "loss", ts=1.0), source="test",
        )

        # El de ts=1.0 es el más antiguo de todos: no entra en la ventana.
        assert _verdicts(index, "market:AAPL").count("loss") == 0

    def test_a_recovered_result_is_placed_by_when_it_happened(self, index):
        """Un aparcado se anotaba como si fuera el más nuevo. Su verdad se
        resolvió antes que la de los que llegaron mientras esperaba."""
        from core.learn.outcome_adapter import Outcome, OutcomeStatus

        og.apply_verified_outcomes(
            [("market:LATE", Outcome(
                prediction_id="viejo", domain="market", subject="LATE",
                status=OutcomeStatus.LOSS, score=-1.0, resolved_ts=10.0))],
            source="test",
        )
        assert og.pending_count(domain="market") == 1

        _make_star(index, "market:LATE", "market")
        og.apply_verified_outcomes(
            [("market:LATE", Outcome(
                prediction_id=f"nuevo-{i}", domain="market", subject="LATE",
                status=OutcomeStatus.WIN, score=1.0, resolved_ts=9000.0 + i))
             for i in range(20)],
            source="test",
        )
        og.retry_pending("market", index=index)

        # Recuperado, pero por antigüedad queda fuera de los 20 más recientes.
        assert _verdicts(index, "market:LATE").count("loss") == 0
        assert og.provenance(fingerprint="market:LATE", prediction_id="viejo")

    def test_the_near_term_retry_still_dedupes_by_id(self, index):
        """La comprobación por id sigue haciendo su trabajo cuando toca."""
        _make_star(index, "market:AAPL", "market")
        outcome = _market_outcome(1, "win", ts=3000.0)

        for _ in range(4):
            index.record_verified_outcome(
                "market:AAPL", "win", outcome.prediction_id, 3000.0,
            )

        assert _verdicts(index, "market:AAPL") == ["win"]


class TestALockedStoreNeverLosesTheResult:
    """Fallo 6: con dos escrituras simultáneas, una recibía
    `database is locked`; esa aplicación quedaba en cero y, en Market, la
    señal quedaba marcada como verificada y no se volvía a intentar.
    """

    @staticmethod
    def _install_recorder(monkeypatch, signals):
        import connectors.etoro.signal_recorder as rec

        class _Status:
            class PENDING:
                value = "pending"

        monkeypatch.setattr(rec, "load_signals", lambda *a, **k: list(signals),
                            raising=False)
        monkeypatch.setattr(rec, "SignalStatus", _Status, raising=False)

    @staticmethod
    def _lock_the_store():
        """Simula que otro escritor tiene la base tomada.

        Devuelve la función que suelta el bloqueo. No se usa
        `monkeypatch.undo()`: revertiría también el índice temporal de la
        fixture `index`.
        """
        import sqlite3

        real = og._get_conn

        def _locked(*a, **k):
            raise sqlite3.OperationalError("database is locked")

        og._get_conn = _locked

        def _release():
            og._get_conn = real

        return _release

    def test_a_locked_store_reports_failure_instead_of_zero(self, index, monkeypatch):
        _make_star(index, "market:AAPL", "market")
        release = self._lock_the_store()
        try:
            counts = og.apply_verified_outcomes(
                [("market:AAPL", _market_outcome(1))], source="test", index=index,
            )
        finally:
            release()

        assert counts[og.FAILED] == 1, counts
        assert counts[og.APPLIED] == 0

    def test_the_signal_is_not_marked_when_the_store_is_locked(
        self, index, monkeypatch,
    ):
        sig = _win_signal(1)
        self._install_recorder(monkeypatch, [sig])
        _make_star(index, "market:AAPL", "market")
        release = self._lock_the_store()
        try:
            market_vc.run_market_verification()
        finally:
            release()

        assert sig.signal_id not in market_vc._load_verified_ids(), (
            "la señal quedó marcada pese a que su resultado se perdió"
        )
        assert vledger.load_outcomes("market") == []

    def test_the_next_cycle_recovers_it_once_the_lock_clears(
        self, index, monkeypatch,
    ):
        sig = _win_signal(1)
        self._install_recorder(monkeypatch, [sig])
        _make_star(index, "market:AAPL", "market")

        release = self._lock_the_store()
        try:
            market_vc.run_market_verification()
        finally:
            release()               # se suelta el bloqueo

        market_vc.run_market_verification()

        assert _verdicts(index, "market:AAPL") == ["win"]
        assert sig.signal_id in market_vc._load_verified_ids()
        assert len(vledger.load_outcomes("market")) == 1, "el ledger se duplicó"

    def test_a_locked_store_that_already_exists_does_not_raise(
        self, index, monkeypatch,
    ):
        """El caso que la prueba anterior no cubría.

        `retry_pending` se llama por delante del `try` de
        `run_market_verification`, así que una excepción ahí tumbaba la
        verificación entera del ciclo. No se veía cuando el almacén todavía no
        existía —`_open_for_read` devuelve None antes de abrirlo—, solo cuando
        ya había uno. Lo encontró la reproducción sobre el pipeline real, no
        esta suite.
        """
        sig = _win_signal(1)
        self._install_recorder(monkeypatch, [sig])
        _make_star(index, "market:AAPL", "market")

        # Se crea el almacén de verdad (con aparcados dentro).
        og.apply_verified_outcome("market:GHOST", _market_outcome(7), source="t")
        assert Path(og.store_path()).exists()

        release = self._lock_the_store()
        try:
            score = market_vc.run_market_verification()   # no debe lanzar
            assert og.retry_pending("market")[og.FAILED] == 1
        finally:
            release()

        # El score describe lo que se RESOLVIÓ en el lote, no lo que se
        # persistió: la señal sí se resolvió. Lo que no debe haber ocurrido es
        # darla por guardada.
        assert score.n_decisive == 1
        assert sig.signal_id not in market_vc._load_verified_ids()
        assert vledger.load_outcomes("market") == []
        assert _verdicts(index, "market:AAPL") == []

    def test_every_read_survives_a_locked_store(self, index, monkeypatch):
        og.apply_verified_outcome("market:GHOST", _market_outcome(7), source="t")

        release = self._lock_the_store()
        try:
            assert og.provenance() == []
            assert og.applied_count() == 0
            assert og.pending_outcomes() == []
            assert og.pending_count() == 0
        finally:
            release()

    def test_a_pending_signal_is_never_marked_verified(self, index, monkeypatch):
        """Un PENDING no está verificado; marcarlo lo excluía para siempre."""
        sig = _Signal("sig-pending", "AAPL", "buy", 0.0, 101.0)   # entry inválido
        self._install_recorder(monkeypatch, [sig])
        _make_star(index, "market:AAPL", "market")

        market_vc.run_market_verification()

        assert sig.signal_id not in market_vc._load_verified_ids()

    def test_two_concurrent_writers_both_land(self, tmp_path, index):
        """Dos escritores reales a la vez sobre la misma base."""
        import threading

        _make_star(index, "market:AAPL", "market")
        errors: list = []
        done: list = []

        def _worker(tag: str):
            try:
                c = og.apply_verified_outcomes(
                    [("market:AAPL", _market_outcome(int(tag) * 100 + i,
                                                     "win", ts=7000.0 + i))
                     for i in range(10)],
                    source=f"w{tag}", index=index,
                )
                done.append(c)
            except Exception as exc:          # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(str(t),)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors
        assert sum(c[og.FAILED] for c in done) == 0, done
        assert sum(c[og.APPLIED] for c in done) == 40
        assert og.applied_count(fingerprint="market:AAPL") == 40

    def test_the_store_uses_wal(self, tmp_path):
        """WAL es lo que permite que un lector no tumbe a un escritor."""
        import sqlite3

        db = str(tmp_path / "og.db")
        og._get_conn(db).close()
        conn = sqlite3.connect(db)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()

        assert mode.lower() == "wal"


# ===========================================================================
# 12. Los dos fallos de la revisión de b590511
# ===========================================================================

class TestAnyStoreFailureIsReportedAsFailed:
    """Fallo 7: un fallo del almacén distinto de «base bloqueada» —disco
    lleno, permisos, corrupción— devolvía `no_star`. Market lo leía como un
    estado terminal normal, escribía el ledger y marcaba la señal aunque la
    gravedad no hubiera guardado nada: esa señal no se reintentaba nunca.

    El estado `no_star` ya no existe. Lo que no queda atendido es `FAILED`,
    sin excepciones, y `accounted()` es la única pregunta que decide si el
    llamador puede dar el lote por hecho.
    """

    @staticmethod
    def _break_the_store(exc: Exception):
        real = og._get_conn

        def _broken(*a, **k):
            raise exc

        og._get_conn = _broken
        return lambda: setattr(og, "_get_conn", real)

    def test_a_full_disk_is_reported_as_failed(self, index):
        import sqlite3

        _make_star(index, "market:AAPL", "market")
        restore = self._break_the_store(
            sqlite3.OperationalError("database or disk is full")
        )
        try:
            counts = og.apply_verified_outcomes(
                [("market:AAPL", _market_outcome(1))], source="t", index=index,
            )
        finally:
            restore()

        assert counts[og.FAILED] == 1, counts
        assert not og.accounted(counts, 1)

    def test_the_signal_is_not_marked_when_the_disk_is_full(
        self, index, monkeypatch,
    ):
        import sqlite3

        sig = _win_signal(1)
        TestALockedStoreNeverLosesTheResult._install_recorder(monkeypatch, [sig])
        _make_star(index, "market:AAPL", "market")

        restore = self._break_the_store(
            sqlite3.OperationalError("database or disk is full")
        )
        try:
            market_vc.run_market_verification()
        finally:
            restore()

        assert sig.signal_id not in market_vc._load_verified_ids()
        assert vledger.load_outcomes("market") == []

        # Y el siguiente ciclo, con disco, lo recupera.
        market_vc.run_market_verification()
        assert _verdicts(index, "market:AAPL") == ["win"]
        assert sig.signal_id in market_vc._load_verified_ids()

    def test_an_unavailable_gravity_index_is_reported_as_failed(self, index):
        counts = og.apply_verified_outcomes(
            [("market:AAPL", _market_outcome(1))], source="t",
            index=_ExplodingIndex(),
        )

        assert counts[og.FAILED] == 1, counts
        assert not og.accounted(counts, 1)

    def test_no_status_can_be_mistaken_for_success(self):
        """`no_star` era el estado trampa: fallo disfrazado de terminal."""
        assert not hasattr(og, "NO_STAR")
        assert set(og.RESULTS) - set(og.ACCOUNTED) == {og.FAILED}

    def test_an_unbalanced_count_is_forced_to_failed(self, index, monkeypatch):
        """Conservación: si la suma no cuadra, la diferencia es FAILED.

        Es la red que hace fail-safe cualquier rama de error futura, que es
        justo de donde han salido estos dos fallos.
        """
        _make_star(index, "market:AAPL", "market")

        def _loses_one(*a, **k):
            return {k2: 0 for k2 in og.RESULTS}      # no contabiliza nada

        monkeypatch.setattr(og, "_apply_once", _loses_one)

        counts = og.apply_verified_outcomes(
            [("market:AAPL", _market_outcome(i)) for i in range(3)],
            source="t", index=index,
        )

        assert counts[og.FAILED] == 3, counts
        assert not og.accounted(counts, 3)


class _ExplodingIndex:
    def record_verified_outcomes(self, entries):
        raise RuntimeError("índice de gravedad caído")

    def load_raw(self):
        raise RuntimeError("índice de gravedad caído")


class TestALedgerFailureDoesNotMarkTheSignal:
    """Fallo 8: si la escritura del ledger fallaba, Market marcaba la señal
    igualmente. `vledger.record_outcome` no lanza —devuelve False—, y ese
    False se ignoraba: el ledger se quedaba sin el resultado y la señal no se
    volvía a presentar.
    """

    def test_a_rejected_ledger_write_leaves_the_signal_unmarked(
        self, index, monkeypatch,
    ):
        sig = _win_signal(1)
        TestALockedStoreNeverLosesTheResult._install_recorder(monkeypatch, [sig])
        _make_star(index, "market:AAPL", "market")
        monkeypatch.setattr(market_vc.vledger, "record_outcome", lambda o: False)

        market_vc.run_market_verification()

        assert sig.signal_id not in market_vc._load_verified_ids()

    def test_the_next_cycle_writes_the_ledger_without_duplicating_gravity(
        self, index, monkeypatch,
    ):
        sig = _win_signal(1)
        TestALockedStoreNeverLosesTheResult._install_recorder(monkeypatch, [sig])
        _make_star(index, "market:AAPL", "market")

        # Se sustituye SOLO la escritura del ledger, y se restaura a mano.
        # `monkeypatch.undo()` no vale aquí: revierte también el `setenv` de
        # VECTRAX_VAULT_DIR que instala la fixture autouse del conftest, de
        # modo que la siguiente escritura iría al VAULT DE PRODUCCIÓN. Ese era
        # exactamente el origen del `outcome_gravity.db` que aparecía de vez
        # en cuando en el vault y que no había conseguido reproducir.
        real_record = market_vc.vledger.record_outcome
        market_vc.vledger.record_outcome = lambda o: False
        try:
            market_vc.run_market_verification()
        finally:
            market_vc.vledger.record_outcome = real_record

        market_vc.run_market_verification()

        assert sig.signal_id in market_vc._load_verified_ids()
        assert len(vledger.load_outcomes("market")) == 1
        # La gravedad NO se duplica: el reintento la reconoce por su clave.
        assert _verdicts(index, "market:AAPL") == ["win"]
        assert og.applied_count(fingerprint="market:AAPL") == 1

    def test_only_the_rejected_one_stays_unmarked(self, index, monkeypatch):
        """Un rechazo puntual no arrastra a las demás señales del lote."""
        sigs = [_win_signal(i) for i in range(4)]
        TestALockedStoreNeverLosesTheResult._install_recorder(monkeypatch, sigs)
        _make_star(index, "market:AAPL", "market")

        rejected = "sig-win-AAPL-2"
        monkeypatch.setattr(
            market_vc.vledger, "record_outcome",
            lambda o: o.prediction_id != rejected,
        )

        market_vc.run_market_verification()

        marked = market_vc._load_verified_ids()
        assert rejected not in marked
        assert all(s.signal_id in marked for s in sigs if s.signal_id != rejected)


# ===========================================================================
# 13. Los dos fallos de la revisión de 35a52f8
# ===========================================================================

class TestAFailedMarkerDoesNotDuplicateTheLedger:
    """Fallo 9: si fallaba la escritura del marcador, el resultado ya estaba en
    el ledger. Al ciclo siguiente la señal se volvía a presentar —correcto, el
    marcador no se escribió— y el resultado entraba por SEGUNDA vez: 1
    resultado, 2 filas, mientras la gravedad conservaba una sola.

    El `verification_ledger` es el único eslabón sin llave de idempotencia: es
    un JSONL append-only y el DomainScore acumulado cuenta las dos filas. No se
    corrige en el núcleo —cybersecurity depende de poder appendear una fila que
    supersede a otra— sino en market, que sí exige una escritura por señal.
    """

    @staticmethod
    def _install_recorder(monkeypatch, signals):
        import connectors.etoro.signal_recorder as rec

        class _Status:
            class PENDING:
                value = "pending"

        monkeypatch.setattr(rec, "load_signals", lambda *a, **k: list(signals),
                            raising=False)
        monkeypatch.setattr(rec, "SignalStatus", _Status, raising=False)

    @staticmethod
    def _break_the_marker():
        real = market_vc._mark_verified
        market_vc._mark_verified = lambda ids: False
        return lambda: setattr(market_vc, "_mark_verified", real)

    def test_one_result_does_not_become_two_ledger_rows(self, index, monkeypatch):
        sig = _win_signal(1)
        self._install_recorder(monkeypatch, [sig])
        _make_star(index, "market:AAPL", "market")

        restore = self._break_the_marker()
        try:
            market_vc.run_market_verification()
        finally:
            restore()

        assert len(vledger.load_outcomes("market")) == 1
        assert sig.signal_id not in market_vc._load_verified_ids()

        # El ciclo siguiente vuelve a presentarla: no debe duplicar el ledger.
        market_vc.run_market_verification()

        assert len(vledger.load_outcomes("market")) == 1, "el ledger se duplicó"
        assert _verdicts(index, "market:AAPL") == ["win"]
        assert og.applied_count(fingerprint="market:AAPL") == 1
        assert sig.signal_id in market_vc._load_verified_ids()

    def test_the_accumulated_score_is_not_inflated(self, index, monkeypatch):
        """Lo que hacía grave al duplicado: falseaba el desempeño real."""
        sigs = [_win_signal(i) for i in range(3)] + [_loss_signal(i) for i in range(1)]
        self._install_recorder(monkeypatch, sigs)
        _make_star(index, "market:AAPL", "market")

        restore = self._break_the_marker()
        try:
            market_vc.run_market_verification()
        finally:
            restore()
        self._install_recorder(monkeypatch, sigs)
        market_vc.run_market_verification()

        score = market_vc.verified_score()
        assert score.n_decisive == 4, score
        assert score.win_rate == pytest.approx(75.0)

    def test_a_repeated_cycle_never_duplicates_even_if_the_marker_never_works(
        self, index, monkeypatch,
    ):
        sig = _win_signal(1)
        self._install_recorder(monkeypatch, [sig])
        _make_star(index, "market:AAPL", "market")

        restore = self._break_the_marker()
        try:
            for _ in range(5):
                self._install_recorder(monkeypatch, [sig])
                market_vc.run_market_verification()
        finally:
            restore()

        assert len(vledger.load_outcomes("market")) == 1
        assert _verdicts(index, "market:AAPL") == ["win"]

    def test_the_marker_failure_is_not_silent(self, index, monkeypatch, caplog):
        sig = _win_signal(1)
        self._install_recorder(monkeypatch, [sig])
        _make_star(index, "market:AAPL", "market")
        monkeypatch.setattr(market_vc, "_verified_path",
                            lambda: "/proc/imposible/market_verified.json")

        with caplog.at_level("WARNING", logger="vectrax.etoro.verification_cycle"):
            market_vc.run_market_verification()

        assert any("marcador" in r.message for r in caplog.records), [
            r.message for r in caplog.records
        ]


class TestAccountedRequiresTheExpectedTotal:
    """Fallo 10: `accounted()` aceptaba un recuento VACÍO como éxito.

    La comprobación era solo "no hay fallos declarados", y cero de todo pasa
    esa prueba. Un recuento que no cubre el lote entero no es un éxito: es un
    recuento que no sabe lo que pasó.
    """

    def test_an_empty_count_is_not_success(self):
        assert not og.accounted({}, 1)
        assert not og.accounted({k: 0 for k in og.RESULTS}, 1)

    def test_a_partial_count_is_not_success(self):
        counts = {k: 0 for k in og.RESULTS}
        counts[og.APPLIED] = 2
        assert not og.accounted(counts, 3)
        assert og.accounted(counts, 2)

    def test_a_declared_failure_is_never_success(self):
        counts = {k: 0 for k in og.RESULTS}
        counts[og.APPLIED] = 2
        counts[og.FAILED] = 1
        assert not og.accounted(counts, 3)

    def test_every_accounted_state_covers_the_batch(self):
        for state in og.ACCOUNTED:
            counts = {k: 0 for k in og.RESULTS}
            counts[state] = 4
            assert og.accounted(counts, 4), state

    def test_an_empty_batch_is_accounted(self):
        assert og.accounted({k: 0 for k in og.RESULTS}, 0)



# ===========================================================================
# 14. Los dos casos de FREIGHT (los mismos que se cerraron en market)
# ===========================================================================

def _booking_star(index) -> str:
    from core.domain_ingester import star_fingerprint

    fp = star_fingerprint("freight_logistics", "load_booking", BOOKING_DATA)
    _make_star(index, fp, "freight_logistics", intent="load_booking")
    return fp


class TestFreightDoesNotDuplicateItsLedger:
    """El mismo defecto que en market, y peor aquí: freight no tiene marcador,
    así que CUALQUIER re-verificación del lote —un ciclo que se reintenta, un
    proveedor que reemite— escribía la línea otra vez.

    Reproducido antes de corregir: 2 resultados -> 4 filas, n_decisive=4.
    """

    def test_reverifying_the_same_batch_does_not_duplicate_the_ledger(self, index):
        _booking_star(index)
        batch = [_delivery(1, True), _delivery(2, False)]

        freight_vc.verify_events(batch)
        freight_vc.verify_events(batch)

        assert len(vledger.load_outcomes("freight_logistics")) == 2

    def test_the_accumulated_score_is_not_inflated(self, index):
        fp = _booking_star(index)
        batch = [_delivery(i, True) for i in range(3)] + [_delivery(9, False)]

        freight_vc.verify_events(batch)
        freight_vc.verify_events(batch)

        score = freight_vc.verified_score()
        assert score.n_decisive == 4, score
        assert score.win_rate == pytest.approx(75.0)
        assert _verdicts(index, fp).count("win") == 3

    def test_many_repeats_stay_at_one_row_each(self, index):
        _booking_star(index)
        batch = [_delivery(1, True)]

        for _ in range(6):
            freight_vc.verify_events(batch)

        assert len(vledger.load_outcomes("freight_logistics")) == 1



class TestFreightRecoversFromTheLedger:
    """Freight no puede reintentar un evento: el proveedor lo emitió una vez y
    no vuelve. Por eso, ante un fallo del almacén de gravedad, escribe SIEMPRE
    el ledger —durable e independiente— y la reconciliación pone la gravedad al
    día después. Retener el ledger aquí, como hace market, no protegería el
    resultado: lo perdería del todo.
    """

    @staticmethod
    def _break_the_store():
        import sqlite3

        real = og._get_conn

        def _broken(*a, **k):
            raise sqlite3.OperationalError("database or disk is full")

        og._get_conn = _broken
        return lambda: setattr(og, "_get_conn", real)

    def test_a_store_failure_does_not_lose_the_result(self, index):
        fp = _booking_star(index)

        restore = self._break_the_store()
        try:
            freight_vc.verify_events([_delivery(1, True)])
        finally:
            restore()

        # El evento ya no existe, pero el ledger lo tiene.
        assert len(vledger.load_outcomes("freight_logistics")) == 1
        assert _verdicts(index, fp) == []

        # El ciclo siguiente, SIN eventos nuevos, lo recupera del ledger.
        freight_vc.verify_events([])

        assert _verdicts(index, fp) == ["win"]
        assert len(vledger.load_outcomes("freight_logistics")) == 1
        assert og.applied_count(domain="freight_logistics") == 1

    def test_the_reconciliation_is_idempotent(self, index):
        fp = _booking_star(index)
        freight_vc.verify_events([_delivery(1, True), _delivery(2, False)])

        for _ in range(4):
            freight_vc.verify_events([])

        assert _verdicts(index, fp) == ["win", "loss"]
        assert og.applied_count(domain="freight_logistics") == 2
        assert len(vledger.load_outcomes("freight_logistics")) == 2

    def test_the_recovered_result_qualifies_the_pattern(self, index):
        """Recuperar no es archivar: el patrón acaba cualificando."""
        fp = _booking_star(index)
        batch = [_delivery(i, True) for i in range(16)] + \
                [_delivery(100 + i, False) for i in range(4)]

        restore = self._break_the_store()
        try:
            freight_vc.verify_events(batch)
        finally:
            restore()
        assert _verdicts(index, fp) == []

        freight_vc.verify_events([])

        stats = cl.qualify_pattern(fp, cl.build_production_policy("freight_logistics"))
        assert stats.qualified, stats.reason
        assert stats.sample_size == 20
        assert stats.win_rate == pytest.approx(80.0)

    def test_the_subject_rebuilds_the_very_same_star(self):
        """La reconciliación parte del `subject` del ledger, no del `data`.

        Si los dos caminos no dieran el mismo fingerprint, el resultado
        recuperado aterrizaría en una estrella distinta —o en ninguna— y la
        recuperación sería silenciosamente inútil.
        """
        from_data = freight_vc.star_fingerprint_for(_delivery(1, True).data)
        from_subject = freight_vc.star_fingerprint_for_subject("Southeast|FastHaul")

        assert from_data == from_subject
        assert from_subject.endswith("region=Southeast|carrier=FastHaul")

    def test_a_malformed_subject_invents_nothing(self):
        for bad in ("", "solo-region", "|", "Southeast|", "|FastHaul", "a|b|c"):
            assert freight_vc.star_fingerprint_for_subject(bad) is None, bad


class TestTheContractIsTheSameInBothDomains:
    """El contrato común: identidad estable del resultado, confirmación de las
    escrituras necesarias, y reintento sin pérdida ni duplicación.

    Se comprueba sobre los dos dominios que este PR conecta a la gravedad. No
    cubre `florida_real_estate`, que NO alimenta la gravedad y cuyos resultados
    no llevan `prediction_id` — su duplicación en el ledger está reproducida y
    documentada en la revisión del PR, pendiente de un cambio aparte.
    """

    def test_both_domains_give_every_outcome_a_stable_identity(self, index):
        _make_star(index, "market:AAPL", "market")
        _booking_star(index)

        market_vc.verify_signals([_win_signal(1)])
        freight_vc.verify_events([_delivery(1, True)])

        for domain in ("market", "freight_logistics"):
            ids = [o.prediction_id for o in vledger.load_outcomes(domain)]
            assert ids and all(ids), f"{domain}: resultado sin identidad {ids}"

    def test_the_identity_is_deterministic_in_both_domains(self, index):
        _make_star(index, "market:AAPL", "market")
        _booking_star(index)

        market_vc.verify_signals([_win_signal(1)])
        freight_vc.verify_events([_delivery(1, True)])
        first = {o.prediction_id for o in vledger.load_outcomes("market")} | \
                {o.prediction_id for o in vledger.load_outcomes("freight_logistics")}

        market_vc.verify_signals([_win_signal(1)])
        freight_vc.verify_events([_delivery(1, True)])
        second = {o.prediction_id for o in vledger.load_outcomes("market")} | \
                 {o.prediction_id for o in vledger.load_outcomes("freight_logistics")}

        assert first == second, "la identidad cambió entre pasadas"

