"""
tests/test_position_manager_live_integration.py — cobertura de
connectors/etoro/position_manager.py::check_open_live_positions()
(paso 8: conexión LIVE — position_manager -> execution_adapter ->
trade_executor).

Cubre el objetivo estrecho que se pidió: una TradeDecision(EXIT) ya
aprobada por RiskGate genera el OrderIntent, pasa por el adapter
idempotente y consume el OrderExecution real — incluido el caso de
timeout (nunca un segundo CLOSE ciego, ni siquiera entre dos llamadas
separadas a check_open_live_positions(), como ocurriría en producción
entre dos ciclos del loop).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from connectors.etoro import (
    execution_adapter,
    live_position_store,
    position_manager,
    trade_decision_engine,
)
from connectors.etoro.trade_executor import ExecutionResult
from core.trading.contracts import EntryThesis, PositionRecord, PositionStatus

T0 = time.time() - 3600  # entró hace 1h


def _cfg(**overrides):
    base = dict(
        halt=False,
        max_position_usd=1000.0,
        max_daily_loss_usd=200.0,
        stop_loss_pct=1.5,
        max_positions_open=5,
        max_hold_hours=24,
        daily_loss_usd=0.0,
    )
    base.update(overrides)
    return base


def _thesis(**overrides) -> EntryThesis:
    base = dict(
        position_id="pos-1", symbol="BTCUSD", side="buy",
        created_at=datetime.fromtimestamp(T0, tz=timezone.utc),
        thesis="ruptura + volumen", invalidation_conditions=("cc_score de market:BTCUSD cae bajo 0.1",),
        confidence_at_entry=0.7, evidence_snapshot={}, evidence_snapshot_id="snap-1",
    )
    base.update(overrides)
    return EntryThesis(**base)


def _open_entry(**overrides) -> live_position_store.LivePositionEntry:
    thesis = _thesis()
    record = PositionRecord(
        position_id="pos-1", entry_thesis=thesis, status=PositionStatus.HOLDING,
        remaining_quantity=100.0, current_intent=None, last_execution=None,
        updated_at=thesis.created_at,
    )
    base = dict(
        record=record, broker_position_id="broker-pos-1", instrument_id=42,
        entry_price=50_000.0, hard_stop_price=49_000.0,
    )
    base.update(overrides)
    return live_position_store.LivePositionEntry(**base)


@pytest.fixture
def wired(monkeypatch, tmp_path):
    monkeypatch.setattr(live_position_store, "_LIVE_POSITIONS_FILE", str(tmp_path / "live.jsonl"))
    monkeypatch.setattr(execution_adapter, "_EXECUTIONS_LOG_FILE", str(tmp_path / "executions.jsonl"))
    monkeypatch.setattr(trade_decision_engine, "_coherence_lost", lambda symbol: False)
    monkeypatch.setattr(trade_decision_engine, "_contrary_signal_detected", lambda symbol, side: False)

    state = {"cfg": _cfg(), "price": 50_100.0, "results": []}

    def _get_config():
        return dict(state["cfg"])

    def _record_trade_result(pnl_usd, is_paper=True, trade_id=None):
        state["results"].append({"pnl_usd": pnl_usd, "is_paper": is_paper})

    def _get_current_price(symbol):
        return state["price"]

    import connectors.etoro.auto_executor as auto_executor_module
    monkeypatch.setattr(auto_executor_module, "get_config", _get_config)
    monkeypatch.setattr(auto_executor_module, "record_trade_result", _record_trade_result)
    monkeypatch.setattr(position_manager, "_get_current_price", _get_current_price)

    return state


def _fill_result(order_id="order-1") -> ExecutionResult:
    return ExecutionResult(success=True, action="close", symbol="BTCUSD", environment="demo", order_id=order_id)


def _timeout_result() -> ExecutionResult:
    return ExecutionResult(
        success=False, action="close", symbol="BTCUSD", environment="demo",
        error="Timeout: simulated", indeterminate=True,
    )


class TestHold:
    def test_hold_does_nothing_and_position_stays_open(self, wired):
        live_position_store.save(_open_entry())
        actions = position_manager.check_open_live_positions(user_id="creator")
        assert actions == []
        assert wired["results"] == []
        assert len(live_position_store.open_positions()) == 1


class TestExitFillsAndClosesForReal:
    def test_hard_stop_exit_calls_real_broker_and_closes(self, wired, monkeypatch):
        calls = []

        def _spy_execute_close(**kw):
            calls.append(kw)
            return _fill_result()

        monkeypatch.setattr(execution_adapter, "execute_close", _spy_execute_close)

        live_position_store.save(_open_entry())
        wired["price"] = 48_000.0  # cruza el stop duro (49000)

        actions = position_manager.check_open_live_positions(user_id="creator")

        assert len(calls) == 1
        assert calls[0]["position_id"] == "broker-pos-1"
        assert calls[0]["instrument_id"] == 42
        assert len(actions) == 1
        assert actions[0]["status"] == "closed_loss"
        assert "hard_stop" in actions[0]["reason"]
        assert wired["results"] == [{"pnl_usd": actions[0]["pnl_usd"], "is_paper": False}]

        remaining_open = live_position_store.open_positions()
        assert remaining_open == []
        closed = live_position_store.get("pos-1")
        assert closed.record.status == PositionStatus.CLOSED

    def test_criterion_exit_calls_real_broker_and_closes(self, wired, monkeypatch):
        monkeypatch.setattr(trade_decision_engine, "_coherence_lost", lambda symbol: True)
        monkeypatch.setattr(execution_adapter, "execute_close", lambda **kw: _fill_result())

        live_position_store.save(_open_entry())
        wired["price"] = 50_100.0  # sin cruzar el stop

        actions = position_manager.check_open_live_positions(user_id="creator")
        assert len(actions) == 1
        assert "cc_score" in actions[0]["reason"]


class TestReduceIsNeverActedOn:
    def test_exposure_breach_reduce_does_not_close_or_call_broker(self, wired, monkeypatch):
        calls = []
        monkeypatch.setattr(execution_adapter, "execute_close", lambda **kw: calls.append(kw))
        wired["cfg"] = _cfg(max_position_usd=10.0, max_positions_open=1)  # exposición máx = 10
        live_position_store.save(_open_entry(record=PositionRecord(
            position_id="pos-1", entry_thesis=_thesis(), status=PositionStatus.HOLDING,
            remaining_quantity=100.0, current_intent=None, last_execution=None,
            updated_at=_thesis().created_at,
        )))
        wired["price"] = 50_100.0

        actions = position_manager.check_open_live_positions(user_id="creator")
        assert actions == []
        assert calls == []
        assert len(live_position_store.open_positions()) == 1


class TestTimeoutNeverProducesASecondCloseAcrossCycles:
    def test_timeout_then_second_cycle_never_calls_broker_twice(self, wired, monkeypatch):
        broker_calls = []

        def _spy_execute_close(**kw):
            broker_calls.append(kw)
            return _timeout_result()

        monkeypatch.setattr(execution_adapter, "execute_close", _spy_execute_close)

        live_position_store.save(_open_entry())
        wired["price"] = 48_000.0  # dispara EXIT por stop duro

        # Ciclo 1: timeout -> UNKNOWN -> RECONCILING, ninguna acción cerrada.
        actions_1 = position_manager.check_open_live_positions(user_id="creator")
        assert actions_1 == []
        assert len(broker_calls) == 1
        entry_after_1 = live_position_store.get("pos-1")
        assert entry_after_1.record.status == PositionStatus.RECONCILING

        # Ciclo 2 (como si el loop de 1-5min volviera a correr): NO debe
        # volver a llamar al broker — el intent anterior sigue sin resolver.
        actions_2 = position_manager.check_open_live_positions(user_id="creator")
        assert actions_2 == []
        assert len(broker_calls) == 1, "un segundo ciclo con la misma UNKNOWN nunca reenvía"
        entry_after_2 = live_position_store.get("pos-1")
        assert entry_after_2.record.status == PositionStatus.RECONCILING
        assert entry_after_2.record.current_intent.intent_id == entry_after_1.record.current_intent.intent_id

        assert wired["results"] == []  # nunca se contó ningún resultado — no cerró de verdad
