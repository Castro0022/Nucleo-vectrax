"""
Tests — fusión de conocimiento TA-Lib con la experiencia propia de market
(connectors/etoro/verification_cycle + ta_feature_gravity), corrección
2026-09-26.

Contexto: antes, `ta_feature_gravity` tenía su propio contrato, su propio
scan de señales y su propia clasificación de procedencia (basada en
`live_activated_at`), separado del ciclo que ya verifica `market` contra el
precio -- una capa paralela. Esta corrección la elimina: cada señal
verificada en `verification_cycle._verify()` alimenta, en el MISMO pase,
tanto la estrella del símbolo (`market:{symbol}`) como las estrellas de sus
condiciones TA-Lib activas (`market:{symbol}:ta:{condición}`) -- mismo
signal_id, mismo status, mismo origen. Como las señales de eToro no
declaran `source`/`provider`, `verification_cycle._origin_kind()` (sin
cambios) las clasifica 'real' -- así que una señal PAPER resuelta contra
precio real ahora cuenta como evidencia real para TA-Lib exactamente igual
que ya contaba para el símbolo.

Run:  python -m pytest tests/test_ta_lib_unified_knowledge.py -v
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from connectors.etoro import verification_cycle as VC
from connectors.etoro import ta_feature_gravity as TFG
from connectors.etoro import knowledge_ledger
from core.learn import verification_ledger as vledger
from core.learn.gravity_engine import GravityIndex


def _sig(sid, symbol, direction, entry, current, status, invalidation=None):
    """MarketSignal fake resuelta (solo los campos que usan el ciclo y la
    fusión TA-Lib: signal_id, symbol, entry_price/price, outcome_price,
    status)."""
    return SimpleNamespace(
        signal_id=sid, symbol=symbol, direction=direction,
        entry_price=entry, price=entry, invalidation_price=invalidation,
        outcome_price=current, status=status,
    )


def _attach_features(signal_id: str, rsi=None, macd_hist=None, timeframe="OneHour"):
    """Adjunta conocimiento TA-Lib mínimo a `signal_id`, en la MISMA forma
    que knowledge_snapshot.snapshot_at() produce (lo que
    _capture_knowledge_async ya escribe en producción)."""
    feats = {}
    if rsi is not None:
        feats["RSI"] = rsi
    if macd_hist is not None:
        feats["MACD_macdhist"] = macd_hist
    knowledge_ledger.record_features(
        signal_id, {timeframe: {"available": True, "features": feats}},
    )


@pytest.fixture(autouse=True)
def _tmp_vault(tmp_path, monkeypatch):
    # Ledger + estado de dedup + knowledge_ledger, todos en un vault temporal
    # (los tres leen VECTRAX_VAULT_DIR) -- hermético.
    monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path))
    vledger.clear_domain("market")
    knowledge_ledger.clear()
    yield
    vledger.clear_domain("market")
    knowledge_ledger.clear()


@pytest.fixture
def index(tmp_path, monkeypatch) -> GravityIndex:
    """Índice de gravedad REAL, en disco temporal -- nunca el de producción.
    Mismo patrón que tests/test_verified_outcomes_reach_learning.py."""
    import core.learn.gravity_engine as ge

    gi = GravityIndex(path=str(tmp_path / "gravity" / "gravity_index.json"))
    monkeypatch.setattr(ge, "get_gravity_index", lambda: gi, raising=False)
    monkeypatch.setattr(ge, "_index", gi, raising=False)
    return gi


def _precreate_symbol_star(index: GravityIndex, symbol: str) -> None:
    """`record_verified_outcome()` NUNCA crea una estrella que no existe --
    la aparca. En producción, `market:{symbol}` la crea por separado
    `learning_engine._feed_gravity()` (fuera de alcance de esta corrección);
    acá se replica el mismo `record_event()` mínimo para poder probar la
    fusión sin depender de ese otro mecanismo, no commiteado."""
    index.record_event(
        fingerprint=f"market:{symbol.upper()}", domain="market",
        outcome="observed", summary="pre-seed para test",
    )


class TestFusedOutcomeFeedsBothStars:
    def test_one_verified_signal_feeds_symbol_star_and_ta_condition_star(self, index):
        _precreate_symbol_star(index, "BTC")
        _attach_features("SIG-1", rsi=25.0)  # RSI:oversold:1H
        sig = _sig("SIG-1", "BTC", "buy", 100.0, 102.0, "win")

        score = VC.verify_signals([sig], record=True)
        assert score.n_decisive == 1  # el DomainScore del símbolo, sin inflar

        symbol_star = index.get("market:BTC")
        assert symbol_star is not None
        assert len(symbol_star.verified_outcomes) == 1
        assert symbol_star.verified_outcomes[0]["status"] == "win"

        ta_star = index.get("market:BTC:ta:RSI:oversold:1H")
        assert ta_star is not None, "ensure_condition_stars debió crearla"
        assert ta_star.domain == "market_ta_features"
        assert len(ta_star.verified_outcomes) == 1
        assert ta_star.verified_outcomes[0]["status"] == "win"

    def test_same_origin_kind_on_both_stars(self, index):
        """Pedido explícito: una señal PAPER resuelta contra precio real
        cuenta como evidencia real para TA-Lib exactamente igual que ya
        cuenta para el símbolo -- mismo origen, misma clasificación."""
        _precreate_symbol_star(index, "TSLA")
        _attach_features("SIG-2", rsi=80.0)  # RSI:overbought:1H
        sig = _sig("SIG-2", "TSLA", "buy", 200.0, 190.0, "loss")

        VC.verify_signals([sig], record=True)

        symbol_kind = index.get("market:TSLA").verified_outcomes[0]["origin_kind"]
        ta_kind = index.get("market:TSLA:ta:RSI:overbought:1H").verified_outcomes[0]["origin_kind"]
        assert symbol_kind == ta_kind == "real"

    def test_signal_without_attached_knowledge_only_feeds_symbol_star(self, index):
        # Sin _attach_features -- simula que la captura async no terminó.
        _precreate_symbol_star(index, "AAPL")
        sig = _sig("SIG-3", "AAPL", "buy", 150.0, 152.0, "win")
        VC.verify_signals([sig], record=True)

        assert index.get("market:AAPL") is not None
        assert index.get("market:AAPL:ta:RSI:oversold:1H") is None

    def test_multiple_conditions_on_one_signal_all_fused(self, index):
        _attach_features("SIG-4", rsi=25.0, macd_hist=-1.5)  # oversold + hist_negative
        sig = _sig("SIG-4", "NVDA", "buy", 400.0, 410.0, "win")
        VC.verify_signals([sig], record=True)

        assert index.get("market:NVDA:ta:RSI:oversold:1H") is not None
        assert index.get("market:NVDA:ta:MACD:hist_negative:1H") is not None

    def test_pending_signal_produces_no_ta_outcomes(self, index):
        _attach_features("SIG-5", rsi=25.0)
        sig = _sig("SIG-5", "BTC", "buy", 100.0, None, "pending")
        VC.verify_signals([sig], record=True)
        assert index.get("market:BTC:ta:RSI:oversold:1H") is None


class TestDedup:
    def test_reverifying_does_not_double_count_ta_outcomes(self, index):
        _attach_features("SIG-6", rsi=25.0)
        sig = _sig("SIG-6", "BTC", "buy", 100.0, 102.0, "win")

        with patch("connectors.etoro.signal_recorder.load_signals", return_value=[sig]):
            s1 = VC.run_market_verification()
            assert s1.n_decisive == 1
            s2 = VC.run_market_verification()  # ya verificada -> nada nuevo
            assert s2.n_total == 0

        ta_star = index.get("market:BTC:ta:RSI:oversold:1H")
        assert len(ta_star.verified_outcomes) == 1  # no se duplicó


class TestGraduatedVerdictNowReachable:
    """Antes de esta corrección, graduated_verdict() no podía devolver nada
    distinto de None nunca -- toda la evidencia quedaba 'simulated'. Ahora,
    con suficiente evidencia real (MIN_SAMPLE del sistema, sin tocarlo), sí
    puede graduar -- para bien (favorable, no bloquea) o para mal
    (desfavorable, bloquea)."""

    def _verify_many(self, symbol, condition_rsi, n_losses, n_wins):
        sigs = []
        for i in range(n_losses):
            sid = f"L-{i}"
            _attach_features(sid, rsi=condition_rsi)
            sigs.append(_sig(sid, symbol, "buy", 100.0, 95.0, "loss"))
        for i in range(n_wins):
            sid = f"W-{i}"
            _attach_features(sid, rsi=condition_rsi)
            sigs.append(_sig(sid, symbol, "buy", 100.0, 105.0, "win"))
        VC.verify_signals(sigs, record=True)

    def test_below_min_sample_stays_silent(self, index):
        from core.domain_knowledge import MIN_SAMPLE
        self._verify_many("BTC", 25.0, n_losses=MIN_SAMPLE - 1, n_wins=0)
        conditions = ["RSI:oversold:1H"]
        assert TFG.graduated_verdict("BTC", conditions) is None

    def test_enough_real_losing_evidence_graduates_unfavorable(self, index):
        from core.domain_knowledge import MIN_SAMPLE
        # Todas pérdidas, sobre el mínimo -- debe graduar desfavorable.
        self._verify_many("BTC", 25.0, n_losses=MIN_SAMPLE, n_wins=0)
        conditions = ["RSI:oversold:1H"]
        verdict = TFG.graduated_verdict("BTC", conditions)
        assert verdict is not None
        assert verdict["condition_id"] == "RSI:oversold:1H"
        assert verdict["win_rate_pct"] == 0.0
        assert verdict["sample_size"] == MIN_SAMPLE

    def test_enough_real_winning_evidence_graduates_favorable_and_stays_silent(self, index):
        from core.domain_knowledge import MIN_SAMPLE
        # Todas ganancias, sobre el mínimo -- gradúa, pero favorable no bloquea.
        self._verify_many("BTC", 25.0, n_losses=0, n_wins=MIN_SAMPLE)
        conditions = ["RSI:oversold:1H"]
        assert TFG.graduated_verdict("BTC", conditions) is None
