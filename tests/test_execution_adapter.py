"""
tests/test_execution_adapter.py — cobertura de
connectors/etoro/execution_adapter.py: OrderIntent -> trade_executor ->
OrderExecution, con el guard de idempotencia aplicado ANTES de llamar
al broker.

El escenario que justifica todo este archivo, con nombre propio:
un CLOSE que da timeout (UNKNOWN) nunca autoriza un segundo CLOSE con
otro intent_id mientras el primero no se reconcilia — ver
test_the_double_exit_scenario_never_calls_broker_twice.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from connectors.etoro import execution_adapter
from connectors.etoro.trade_executor import ExecutionResult
from core.trading.contracts import ExecutionStatus, OrderAction, OrderIntent

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _open_intent(intent_id="intent-open-1") -> OrderIntent:
    return OrderIntent(
        intent_id=intent_id, position_id="pos-1", action=OrderAction.OPEN,
        quantity=100.0, created_at=T0,
    )


def _close_intent(intent_id="intent-close-1") -> OrderIntent:
    return OrderIntent(
        intent_id=intent_id, position_id="pos-1", action=OrderAction.CLOSE,
        quantity=100.0, created_at=T0,
    )


@pytest.fixture(autouse=True)
def _tmp_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(
        execution_adapter, "_EXECUTIONS_LOG_FILE", str(tmp_path / "executions.jsonl")
    )


def _success_result(order_id="order-1") -> ExecutionResult:
    return ExecutionResult(
        success=True, action="open", symbol="BTCUSD", environment="demo",
        order_id=order_id, position_id="broker-pos-1",
    )


def _indeterminate_result() -> ExecutionResult:
    return ExecutionResult(
        success=False, action="open", symbol="BTCUSD", environment="demo",
        error="Timeout: simulated", indeterminate=True,
    )


def _rejected_result() -> ExecutionResult:
    return ExecutionResult(
        success=False, action="open", symbol="BTCUSD", environment="demo",
        error="HTTP 400", indeterminate=False,
    )


class TestSubmitOpenTranslation:
    def test_success_translates_to_filled_and_persists(self, monkeypatch):
        monkeypatch.setattr(execution_adapter, "execute_open", lambda **kw: _success_result())
        execution = execution_adapter.submit_open(_open_intent(), user_id="u1", symbol="BTCUSD", is_buy=True)
        assert execution.status == ExecutionStatus.FILLED
        assert execution.filled_quantity == 100.0
        assert execution.broker_order_id == "order-1"
        assert execution_adapter.latest_execution("intent-open-1") == execution

    def test_indeterminate_translates_to_unknown_never_rejected(self, monkeypatch):
        monkeypatch.setattr(execution_adapter, "execute_open", lambda **kw: _indeterminate_result())
        execution = execution_adapter.submit_open(_open_intent(), user_id="u1", symbol="BTCUSD", is_buy=True)
        assert execution.status == ExecutionStatus.UNKNOWN

    def test_explicit_rejection_translates_to_rejected(self, monkeypatch):
        monkeypatch.setattr(execution_adapter, "execute_open", lambda **kw: _rejected_result())
        execution = execution_adapter.submit_open(_open_intent(), user_id="u1", symbol="BTCUSD", is_buy=True)
        assert execution.status == ExecutionStatus.REJECTED

    def test_wrong_action_intent_raises(self):
        with pytest.raises(ValueError):
            execution_adapter.submit_open(_close_intent(), user_id="u1", symbol="BTCUSD", is_buy=True)


class TestIdempotencyGuardBlocksBeforeCallingBroker:
    def test_unresolved_unknown_execution_blocks_a_second_broker_call(self, monkeypatch):
        calls = []

        def _spy_execute_open(**kw):
            calls.append(kw)
            return _indeterminate_result()

        monkeypatch.setattr(execution_adapter, "execute_open", _spy_execute_open)

        intent = _open_intent()
        first = execution_adapter.submit_open(intent, user_id="u1", symbol="BTCUSD", is_buy=True)
        assert first.status == ExecutionStatus.UNKNOWN
        assert len(calls) == 1

        # Segundo intento con el MISMO intent_id: no debe volver a llamar al broker.
        second = execution_adapter.submit_open(intent, user_id="u1", symbol="BTCUSD", is_buy=True)
        assert len(calls) == 1, "un intent_id sin resolver nunca vuelve a llamar al broker"
        assert second == first

    def test_terminal_execution_allows_a_new_call_for_a_new_intent_id(self, monkeypatch):
        calls = []

        def _spy_execute_open(**kw):
            calls.append(kw)
            return _success_result()

        monkeypatch.setattr(execution_adapter, "execute_open", _spy_execute_open)

        execution_adapter.submit_open(_open_intent("intent-A"), user_id="u1", symbol="BTCUSD", is_buy=True)
        execution_adapter.submit_open(_open_intent("intent-B"), user_id="u1", symbol="BTCUSD", is_buy=True)
        assert len(calls) == 2  # intent_ids distintos, ambos legítimos


class TestTheDoubleExitScenario:
    def test_the_double_exit_scenario_never_calls_broker_twice(self, monkeypatch):
        """El escenario exacto que motivó esta capa: un CLOSE con timeout
        (UNKNOWN) nunca debe producir un segundo CLOSE real, aunque el
        caller vuelva a intentarlo con el mismo intent_id creyendo que
        el primero 'falló'."""
        calls = []

        def _spy_execute_close(**kw):
            calls.append(kw)
            return ExecutionResult(
                success=False, action="close", symbol="BTCUSD", environment="demo",
                error="Timeout: simulated", indeterminate=True,
            )

        monkeypatch.setattr(execution_adapter, "execute_close", _spy_execute_close)

        intent = _close_intent("intent-close-1")
        first = execution_adapter.submit_close(intent, user_id="u1", broker_position_id="broker-pos-1")
        assert first.status == ExecutionStatus.UNKNOWN
        assert len(calls) == 1

        # Vectrax "piensa que falló" y reintenta con el MISMO intent_id.
        second = execution_adapter.submit_close(intent, user_id="u1", broker_position_id="broker-pos-1")
        assert len(calls) == 1, (
            "un segundo CLOSE real habría podido abrir una posición contraria "
            "por accidente si el primero sí había ejecutado"
        )
        assert second.status == ExecutionStatus.UNKNOWN

    def test_wrong_action_intent_raises_for_close(self):
        with pytest.raises(ValueError):
            execution_adapter.submit_close(_open_intent(), user_id="u1", broker_position_id="broker-pos-1")


class TestPersistenceSurvivesAcrossCalls:
    def test_latest_execution_reads_back_from_disk(self, monkeypatch):
        monkeypatch.setattr(execution_adapter, "execute_open", lambda **kw: _success_result())
        execution_adapter.submit_open(_open_intent("intent-persist"), user_id="u1", symbol="BTCUSD", is_buy=True)

        # Simula un reinicio: nueva lectura desde disco, sin estado en RAM.
        reread = execution_adapter.latest_execution("intent-persist")
        assert reread is not None
        assert reread.status == ExecutionStatus.FILLED

    def test_unknown_execution_survives_and_still_blocks_after_reread(self, monkeypatch):
        monkeypatch.setattr(execution_adapter, "execute_close", lambda **kw: ExecutionResult(
            success=False, action="close", symbol="BTCUSD", environment="demo",
            error="Timeout", indeterminate=True,
        ))
        intent = _close_intent("intent-restart")
        execution_adapter.submit_close(intent, user_id="u1", broker_position_id="broker-pos-1")

        calls = []
        monkeypatch.setattr(execution_adapter, "execute_close", lambda **kw: calls.append(kw))
        execution_adapter.submit_close(intent, user_id="u1", broker_position_id="broker-pos-1")
        assert calls == []
