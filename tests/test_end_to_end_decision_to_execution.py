"""
tests/test_end_to_end_decision_to_execution.py — la prueba de
integración final antes de habilitar LIVE: el ciclo completo
criterio + seguridad + idempotencia, orquestado tal como lo hará la
futura conexión LIVE de position_manager.py (todavía no existe esa
conexión — este test compone las piezas directamente para probar que
ya encajan).

    evidencia cambia
    → TradeDecisionEngine = EXIT
    → RiskGate = PASS
    → position_state = CLOSING
    → execution_adapter.submit_close(intent_id estable)
    → broker timeout
    → OrderExecution = UNKNOWN
    → RECONCILE
    → jamás un segundo CLOSE ciego

Si este test pasa, el ciclo autónomo completo (criterio + seguridad +
idempotencia) es correcto de punta a punta.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from connectors.etoro import execution_adapter, trade_decision_engine
from connectors.etoro.trade_executor import ExecutionResult
from core.trading import risk_gate
from core.trading.contracts import (
    AccountRiskSnapshot,
    EntryThesis,
    ExecutionStatus,
    OrderAction,
    OrderIntent,
    PositionRecord,
    PositionRiskSnapshot,
    PositionStatus,
    RiskAction,
    RiskLimits,
    RiskMode,
    TradeAction,
)
from core.trading.position_state import apply_decision, apply_execution_update

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)

LIMITS = RiskLimits(
    max_loss_per_trade_usd=50.0,
    max_daily_loss_usd=200.0,
    max_open_positions=3,
    max_total_exposure_usd=1000.0,
)


def _thesis() -> EntryThesis:
    return EntryThesis(
        position_id="pos-1",
        symbol="BTCUSD",
        side="buy",
        created_at=T0,
        thesis="ruptura + volumen + cc_score alto",
        invalidation_conditions=("cc_score de market:BTCUSD cae bajo 0.1",),
        confidence_at_entry=0.75,
        evidence_snapshot={"cc_score": 0.6},
        evidence_snapshot_id="snap-1",
        evidence_refs=("market:BTCUSD",),
    )


def _holding_record() -> PositionRecord:
    return PositionRecord(
        position_id="pos-1",
        entry_thesis=_thesis(),
        status=PositionStatus.HOLDING,
        remaining_quantity=100.0,
        current_intent=None,
        last_execution=None,
        updated_at=T0,
    )


def _account() -> AccountRiskSnapshot:
    return AccountRiskSnapshot(
        equity_usd=10_000.0,
        realized_pnl_today_usd=0.0,
        open_positions_count=1,
        total_exposure_usd=100.0,
        kill_switch=RiskMode.NORMAL,
        positions_sync_ok=True,
        broker_execution_ok=True,
    )


@pytest.fixture(autouse=True)
def _tmp_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(execution_adapter, "_EXECUTIONS_LOG_FILE", str(tmp_path / "executions.jsonl"))


@pytest.fixture(autouse=True)
def _no_real_evidence_by_default(monkeypatch):
    monkeypatch.setattr(trade_decision_engine, "_contrary_signal_detected", lambda symbol, side: False)


class TestFullCycleWithBrokerTimeout:
    def test_evidence_change_to_reconcile_never_sends_a_second_close(self, monkeypatch):
        holding = _holding_record()
        account = _account()

        # 1. Evidencia cambia: cc_score cae -> TradeDecisionEngine propone EXIT.
        monkeypatch.setattr(trade_decision_engine, "_coherence_lost", lambda symbol: True)
        decision = trade_decision_engine.propose_decision(
            holding.entry_thesis, current_price=50_100.0, now=T0.timestamp() + 60,
        )
        assert decision.action == TradeAction.EXIT

        # 2. RiskGate: nada anómalo, no hay stop cruzado ni límite excedido -> PASS.
        position_snapshot = PositionRiskSnapshot(
            position_id="pos-1", symbol="BTCUSD", side="buy",
            entry_price=50_000.0, current_price=50_100.0,
            hard_stop_price=49_000.0, size_usd=100.0,
        )
        verdict = risk_gate.check_open_position(position_snapshot, account, LIMITS)
        assert verdict.forced_action == RiskAction.PASS

        # 3. position_state: HOLDING -> CLOSING, con un intent_id estable.
        intent = OrderIntent(
            intent_id="intent-close-pos-1", position_id="pos-1",
            action=OrderAction.CLOSE, quantity=100.0, created_at=T0,
        )
        closing = apply_decision(holding, decision, intent, T0)
        assert closing.status == PositionStatus.CLOSING

        # 4. execution_adapter.submit_close: el broker da timeout -> indeterminate.
        broker_calls = []

        def _timeout_close(**kwargs):
            broker_calls.append(kwargs)
            return ExecutionResult(
                success=False, action="close", symbol="BTCUSD", environment="demo",
                error="Timeout: simulated", indeterminate=True,
            )

        monkeypatch.setattr(execution_adapter, "execute_close", _timeout_close)
        execution = execution_adapter.submit_close(
            intent, user_id="creator", broker_position_id="broker-pos-1",
        )
        assert execution.status == ExecutionStatus.UNKNOWN
        assert len(broker_calls) == 1

        # 5. Ese OrderExecution entra a position_state -> RECONCILING, nunca CLOSED.
        reconciling = apply_execution_update(closing, execution, T0)
        assert reconciling.status == PositionStatus.RECONCILING

        # 6. apply_decision se niega a emitir un CLOSE nuevo mientras se reconcilia.
        with pytest.raises(ValueError):
            apply_decision(
                reconciling, decision,
                OrderIntent(
                    intent_id="intent-close-pos-1-RETRY", position_id="pos-1",
                    action=OrderAction.CLOSE, quantity=100.0, created_at=T0,
                ),
                T0,
            )

        # 7. Y aunque alguien vuelva a llamar a submit_close con el MISMO
        #    intent_id creyendo que "falló", execution_adapter tampoco
        #    llama al broker de nuevo.
        second_execution = execution_adapter.submit_close(
            intent, user_id="creator", broker_position_id="broker-pos-1",
        )
        assert len(broker_calls) == 1, "jamás un segundo CLOSE ciego"
        assert second_execution.status == ExecutionStatus.UNKNOWN

        # 8. La reconciliación real: el broker responde tarde, para el
        #    MISMO intent_id, y el ciclo se resuelve limpio.
        real_reconciled_execution = execution.__class__(
            intent_id="intent-close-pos-1",
            broker_order_id="broker-order-1",
            status=ExecutionStatus.FILLED,
            filled_quantity=100.0,
            avg_fill_price=50_100.0,
            last_update_at=T0,
        )
        closed = apply_execution_update(reconciling, real_reconciled_execution, T0)
        assert closed.status == PositionStatus.CLOSED
