"""
tests/test_trade_decision_engine.py — cobertura exhaustiva de
connectors/etoro/trade_decision_engine.py (paso 7: TradeDecisionEngine
real, reemplaza legacy_criterion_engine.py).

Cubre HOLD/EXIT y, sobre todo, invalidación de tesis: cada
invalidation_condition se dispara por su propia evidencia, ninguna
depende de un take_profit fijo (ese criterio se retiró a propósito —
ver el docstring del módulo).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from connectors.etoro import trade_decision_engine
from core.trading.contracts import EntryThesis, TradeAction

T0 = time.time() - 3600  # entró hace 1h


def _thesis(**overrides) -> EntryThesis:
    base = dict(
        position_id="pos-1",
        symbol="BTCUSD",
        side="buy",
        created_at=datetime.fromtimestamp(T0, tz=timezone.utc),
        thesis="ruptura + volumen + cc_score alto",
        invalidation_conditions=(
            "cc_score de market:BTCUSD cae bajo 0.1",
            "señal contraria OPERABLE detectada en las últimas 2h",
        ),
        confidence_at_entry=0.75,
        evidence_snapshot={"cc_score": 0.6},
        evidence_snapshot_id="snap-1",
        evidence_refs=("market:BTCUSD",),
    )
    base.update(overrides)
    return EntryThesis(**base)


@pytest.fixture(autouse=True)
def _no_real_evidence(monkeypatch):
    """Por defecto ninguna fuente de evidencia dispara nada — cada test
    activa explícitamente la que necesita."""
    monkeypatch.setattr(trade_decision_engine, "_coherence_lost", lambda symbol: False)
    monkeypatch.setattr(trade_decision_engine, "_contrary_signal_detected", lambda symbol, side: False)


class TestHoldWhenThesisStillHolds:
    def test_hold_when_nothing_invalidated(self):
        decision = trade_decision_engine.propose_decision(_thesis(), current_price=50_100.0, now=T0 + 60)
        assert decision.action == TradeAction.HOLD
        assert decision.position_id == "pos-1"
        assert "ruptura + volumen" in decision.reason

    def test_hold_carries_entry_confidence(self):
        decision = trade_decision_engine.propose_decision(
            _thesis(confidence_at_entry=0.83), current_price=50_100.0, now=T0 + 60,
        )
        assert decision.confidence == 0.83

    def test_hold_carries_original_evidence_refs(self):
        decision = trade_decision_engine.propose_decision(_thesis(), current_price=50_100.0, now=T0 + 60)
        assert decision.evidence_refs == ("market:BTCUSD",)


class TestNoPriceAbstainsAsHold:
    def test_missing_price_holds_with_zero_confidence(self):
        decision = trade_decision_engine.propose_decision(_thesis(), current_price=None, now=T0 + 60)
        assert decision.action == TradeAction.HOLD
        assert decision.confidence == 0.0


class TestInvalidationByCoherenceLoss:
    def test_coherence_loss_triggers_exit(self, monkeypatch):
        monkeypatch.setattr(trade_decision_engine, "_coherence_lost", lambda symbol: True)
        decision = trade_decision_engine.propose_decision(_thesis(), current_price=50_100.0, now=T0 + 60)
        assert decision.action == TradeAction.EXIT
        assert "cc_score" in decision.reason
        assert decision.evidence_refs == ("gravity:market:BTCUSD",)

    def test_coherence_loss_checks_the_right_symbol(self, monkeypatch):
        seen = []

        def _spy(symbol):
            seen.append(symbol)
            return False

        monkeypatch.setattr(trade_decision_engine, "_coherence_lost", _spy)
        trade_decision_engine.propose_decision(_thesis(symbol="ETHUSD"), current_price=3_000.0, now=T0 + 60)
        assert seen == ["ETHUSD"]


class TestInvalidationByContrarySignal:
    def test_contrary_signal_triggers_exit(self, monkeypatch):
        monkeypatch.setattr(trade_decision_engine, "_contrary_signal_detected", lambda symbol, side: True)
        decision = trade_decision_engine.propose_decision(_thesis(), current_price=50_100.0, now=T0 + 60)
        assert decision.action == TradeAction.EXIT
        assert "contraria" in decision.reason
        assert decision.evidence_refs == ("signal:BTCUSD:contrary",)

    def test_coherence_checked_before_contrary_signal(self, monkeypatch):
        """La primera condición de la tesis, en orden, es la que se
        reporta si varias están rotas a la vez."""
        monkeypatch.setattr(trade_decision_engine, "_coherence_lost", lambda symbol: True)
        monkeypatch.setattr(trade_decision_engine, "_contrary_signal_detected", lambda symbol, side: True)
        decision = trade_decision_engine.propose_decision(_thesis(), current_price=50_100.0, now=T0 + 60)
        assert "cc_score" in decision.reason


class TestNoTakeProfitCriterion:
    """El take-profit de precio fijo se retiró del criterio a propósito
    — 'ya ganó suficiente' no lo decide un precio fijado al entrar."""

    def test_large_favorable_price_move_does_not_force_exit_by_itself(self):
        decision = trade_decision_engine.propose_decision(
            _thesis(), current_price=200_000.0, now=T0 + 60,  # 4x el precio de entrada implícito
        )
        assert decision.action == TradeAction.HOLD


class TestTimeSafetyNet:
    def test_exceeding_max_hold_hours_forces_exit(self):
        decision = trade_decision_engine.propose_decision(
            _thesis(), current_price=50_100.0, now=T0 + 3600 * 25, max_hold_hours=24.0,
        )
        assert decision.action == TradeAction.EXIT
        assert "seguro_tiempo_maximo" in decision.reason
        assert decision.evidence_refs == ("time_safety_net",)

    def test_time_safety_net_takes_precedence_over_a_still_valid_thesis(self):
        """Es un seguro extremo: se dispara aunque nada esté invalidado."""
        decision = trade_decision_engine.propose_decision(
            _thesis(), current_price=50_100.0, now=T0 + 3600 * 25, max_hold_hours=24.0,
        )
        assert decision.action == TradeAction.EXIT

    def test_within_max_hold_hours_does_not_trigger_safety_net(self):
        decision = trade_decision_engine.propose_decision(
            _thesis(), current_price=50_100.0, now=T0 + 3600 * 23, max_hold_hours=24.0,
        )
        assert decision.action == TradeAction.HOLD


class TestNeverProposesEnter:
    def test_no_public_path_produces_enter(self):
        """propose_decision solo vigila una posición YA abierta — nunca
        emite ENTER (eso es un problema distinto, fuera de alcance)."""
        decision = trade_decision_engine.propose_decision(_thesis(), current_price=50_100.0, now=T0 + 60)
        assert decision.action != TradeAction.ENTER
