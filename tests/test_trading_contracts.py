"""
tests/test_trading_contracts.py — cobertura de los contratos tipados de
core/trading/contracts.py (paso 1 de la arquitectura RiskGate /
TradeDecisionEngine / PositionManager).

Cubre: inmutabilidad (frozen), el requisito position_id en TradeDecision
para todo lo que no sea ENTER, y el roundtrip to_dict/from_dict de
EntryThesis (necesario porque la tesis se persiste junto a la posición).
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from core.trading.contracts import (
    EntryThesis,
    ExecutionStatus,
    OrderAction,
    OrderExecution,
    OrderIntent,
    RiskAction,
    RiskVerdict,
    TradeAction,
    TradeDecision,
)


def _sample_thesis() -> EntryThesis:
    return EntryThesis(
        position_id="pos-1",
        symbol="BTCUSD",
        side="buy",
        created_at=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
        thesis="Ruptura confirmada + volumen + estrella market:BTCUSD cc_score alto",
        invalidation_conditions=(
            "cc_score de market:BTCUSD cae bajo 0.1",
            "señal contraria OPERABLE en las próximas 2h",
        ),
        confidence_at_entry=0.78,
        evidence_snapshot={"cc_score": 0.62, "pattern": "breakout"},
        evidence_snapshot_id="snap-pos-1-1758801600",
        evidence_refs=("market:BTCUSD", "pattern:breakout:BTCUSD"),
    )


class TestEntryThesisIsFrozen:
    def test_cannot_mutate_thesis_field(self):
        thesis = _sample_thesis()
        with pytest.raises(FrozenInstanceError):
            thesis.thesis = "otra tesis"  # type: ignore[misc]

    def test_roundtrip_to_dict_from_dict(self):
        thesis = _sample_thesis()
        restored = EntryThesis.from_dict(thesis.to_dict())
        assert restored == thesis

    def test_to_dict_serializes_tuples_as_lists(self):
        d = _sample_thesis().to_dict()
        assert isinstance(d["invalidation_conditions"], list)
        assert isinstance(d["evidence_refs"], list)


class TestTradeDecisionRequiresPositionId:
    def test_enter_allows_no_position_id(self):
        decision = TradeDecision(
            action=TradeAction.ENTER,
            position_id=None,
            reason="Ruptura + volumen confirmados",
            confidence=0.7,
        )
        assert decision.position_id is None

    @pytest.mark.parametrize(
        "action", [TradeAction.HOLD, TradeAction.REDUCE, TradeAction.EXIT]
    )
    def test_non_enter_actions_require_position_id(self, action):
        with pytest.raises(ValueError):
            TradeDecision(
                action=action,
                position_id=None,
                reason="tesis sigue vigente",
                confidence=0.6,
            )

    def test_non_enter_action_with_position_id_is_valid(self):
        decision = TradeDecision(
            action=TradeAction.HOLD,
            position_id="pos-1",
            reason="A y B siguen vigentes",
            confidence=0.6,
        )
        assert decision.position_id == "pos-1"

    def test_is_frozen(self):
        decision = TradeDecision(
            action=TradeAction.ENTER,
            position_id=None,
            reason="r",
            confidence=0.5,
        )
        with pytest.raises(FrozenInstanceError):
            decision.confidence = 0.9  # type: ignore[misc]


class TestRiskVerdictIsNotABoolean:
    def test_pass_verdict_reports_passed_true(self):
        verdict = RiskVerdict(
            forced_action=RiskAction.PASS,
            reason="dentro de límites",
            rule_id="none",
        )
        assert verdict.passed is True

    @pytest.mark.parametrize(
        "forced_action",
        [RiskAction.OVERRIDE_REDUCE, RiskAction.OVERRIDE_EXIT, RiskAction.BLOCK_ENTRY],
    )
    def test_overriding_verdicts_report_passed_false(self, forced_action):
        verdict = RiskVerdict(
            forced_action=forced_action,
            reason="límite duro activado",
            rule_id="max_daily_loss",
        )
        assert verdict.passed is False

    def test_override_exit_carries_rule_id_for_audit(self):
        verdict = RiskVerdict(
            forced_action=RiskAction.OVERRIDE_EXIT,
            reason="pérdida diaria máxima alcanzada",
            rule_id="max_daily_loss",
        )
        d = verdict.to_dict()
        assert d["forced_action"] == "OVERRIDE_EXIT"
        assert d["rule_id"] == "max_daily_loss"

    def test_is_frozen(self):
        verdict = RiskVerdict(
            forced_action=RiskAction.PASS, reason="ok", rule_id="none"
        )
        with pytest.raises(FrozenInstanceError):
            verdict.rule_id = "x"  # type: ignore[misc]


def _sample_intent() -> OrderIntent:
    return OrderIntent(
        intent_id="intent-1",
        position_id="pos-1",
        action=OrderAction.CLOSE,
        quantity=0.01,
        created_at=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
    )


def _sample_execution(status: ExecutionStatus) -> OrderExecution:
    return OrderExecution(
        intent_id="intent-1",
        broker_order_id="broker-order-1" if status != ExecutionStatus.PENDING else None,
        status=status,
        filled_quantity=0.01 if status == ExecutionStatus.FILLED else 0.0,
        avg_fill_price=50_000.0 if status == ExecutionStatus.FILLED else None,
        last_update_at=datetime(2026, 9, 25, 12, 5, tzinfo=timezone.utc),
    )


class TestOrderIntentIsFrozen:
    def test_cannot_mutate_quantity(self):
        intent = _sample_intent()
        with pytest.raises(FrozenInstanceError):
            intent.quantity = 1.0  # type: ignore[misc]

    def test_to_dict_serializes_action_and_datetime(self):
        d = _sample_intent().to_dict()
        assert d["action"] == "CLOSE"
        assert d["created_at"] == "2026-09-25T12:00:00+00:00"


class TestOrderExecutionIsFrozen:
    def test_cannot_mutate_status(self):
        execution = _sample_execution(ExecutionStatus.FILLED)
        with pytest.raises(FrozenInstanceError):
            execution.status = ExecutionStatus.REJECTED  # type: ignore[misc]

    def test_to_dict_serializes_status(self):
        d = _sample_execution(ExecutionStatus.UNKNOWN).to_dict()
        assert d["status"] == "UNKNOWN"
        assert d["broker_order_id"] == "broker-order-1"
