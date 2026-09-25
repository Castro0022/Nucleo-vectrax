"""
tests/test_order_idempotency.py — cobertura de la regla inviolable de
core/trading/order_idempotency.py: el mismo intent_id nunca crea una
segunda orden real, y UNKNOWN nunca se trata como "reintentar".
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.trading.contracts import ExecutionStatus, OrderExecution
from core.trading.order_idempotency import (
    can_send_new_intent,
    is_terminal,
    requires_reconciliation,
)


def _execution(status: ExecutionStatus) -> OrderExecution:
    return OrderExecution(
        intent_id="intent-1",
        broker_order_id="broker-order-1",
        status=status,
        filled_quantity=0.01 if status == ExecutionStatus.FILLED else 0.0,
        avg_fill_price=50_000.0 if status == ExecutionStatus.FILLED else None,
        last_update_at=datetime(2026, 9, 25, 12, 5, tzinfo=timezone.utc),
    )


class TestIsTerminal:
    @pytest.mark.parametrize("status", [ExecutionStatus.FILLED, ExecutionStatus.REJECTED])
    def test_terminal_statuses(self, status):
        assert is_terminal(status) is True

    @pytest.mark.parametrize(
        "status",
        [ExecutionStatus.PENDING, ExecutionStatus.ACKED, ExecutionStatus.PARTIAL, ExecutionStatus.UNKNOWN],
    )
    def test_non_terminal_statuses(self, status):
        assert is_terminal(status) is False


class TestRequiresReconciliation:
    def test_only_unknown_requires_reconciliation(self):
        assert requires_reconciliation(ExecutionStatus.UNKNOWN) is True

    @pytest.mark.parametrize(
        "status",
        [
            ExecutionStatus.PENDING,
            ExecutionStatus.ACKED,
            ExecutionStatus.PARTIAL,
            ExecutionStatus.FILLED,
            ExecutionStatus.REJECTED,
        ],
    )
    def test_other_statuses_do_not_require_reconciliation(self, status):
        assert requires_reconciliation(status) is False


class TestCanSendNewIntent:
    def test_no_previous_intent_allows_sending(self):
        assert can_send_new_intent(None) is True

    @pytest.mark.parametrize("status", [ExecutionStatus.FILLED, ExecutionStatus.REJECTED])
    def test_terminal_previous_execution_allows_new_intent(self, status):
        assert can_send_new_intent(_execution(status)) is True

    @pytest.mark.parametrize(
        "status", [ExecutionStatus.PENDING, ExecutionStatus.ACKED, ExecutionStatus.PARTIAL]
    )
    def test_in_flight_previous_execution_blocks_new_intent(self, status):
        assert can_send_new_intent(_execution(status)) is False

    def test_the_double_exit_scenario_unknown_never_authorizes_a_resend(self):
        """El escenario exacto que motivó este contrato: CLOSE -> timeout
        -> status UNKNOWN. Vectrax NO debe poder mandar un segundo CLOSE
        (con un intent_id nuevo) mientras no sepa qué pasó con el
        primero — eso es lo que abriría una posición contraria por
        accidente si el primer CLOSE sí había ejecutado."""
        unknown = _execution(ExecutionStatus.UNKNOWN)
        assert can_send_new_intent(unknown) is False
        assert requires_reconciliation(unknown.status) is True
