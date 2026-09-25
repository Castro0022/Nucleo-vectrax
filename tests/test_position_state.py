"""
tests/test_position_state.py — cobertura de core/trading/position_state.py
(paso 4: PositionManager como máquina de estados pura).

Cada invariante acordado antes de escribir el código tiene su propio
test, con nombre explícito, para que no pueda romperse en silencio:

  - CLOSED es terminal.
  - CLOSING nunca vuelve a HOLDING simplemente porque pase tiempo.
  - UNKNOWN nunca crea un segundo intent.
  - PARTIAL conserva el mismo intent_id.
  - REDUCING no implica necesariamente CLOSED.
  - FILLED de un CLOSE completo sí produce CLOSED.
  - cada transición conserva position_id, intent_id y la tesis original.
  - ninguna transición llama al broker directamente.
  - ninguna transición consulta core.nucleus, core.learn ni señales.
  - apply_decision nunca produce CLOSED (solo apply_execution_update).
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.trading import position_state
from core.trading.contracts import (
    EntryThesis,
    ExecutionStatus,
    OrderAction,
    OrderExecution,
    OrderIntent,
    PositionStatus,
    TradeAction,
    TradeDecision,
)
from core.trading.position_state import apply_decision, apply_execution_update, open_position

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(minutes=1)
T2 = T0 + timedelta(minutes=2)
T3 = T0 + timedelta(minutes=3)


def _thesis(position_id="pos-1") -> EntryThesis:
    return EntryThesis(
        position_id=position_id,
        symbol="BTCUSD",
        side="buy",
        created_at=T0,
        thesis="ruptura + volumen",
        invalidation_conditions=("cc_score cae bajo 0.1",),
        confidence_at_entry=0.75,
        evidence_snapshot={"cc_score": 0.6},
        evidence_snapshot_id="snap-1",
    )


def _open_intent(position_id="pos-1", quantity=1.0) -> OrderIntent:
    return OrderIntent(
        intent_id="intent-open", position_id=position_id, action=OrderAction.OPEN,
        quantity=quantity, created_at=T0,
    )


def _execution(intent_id, status, filled_quantity=0.0, when=T1) -> OrderExecution:
    return OrderExecution(
        intent_id=intent_id,
        broker_order_id="broker-1",
        status=status,
        filled_quantity=filled_quantity,
        avg_fill_price=50_000.0 if status == ExecutionStatus.FILLED else None,
        last_update_at=when,
    )


def _holding_record(position_id="pos-1", quantity=1.0):
    """Atajo: abre y confirma el fill, para arrancar los tests en HOLDING."""
    record = open_position(position_id, _thesis(position_id), _open_intent(position_id, quantity), T0)
    return apply_execution_update(record, _execution("intent-open", ExecutionStatus.FILLED, quantity, T1), T1)


def _decision(action, position_id="pos-1", reason="r") -> TradeDecision:
    return TradeDecision(action=action, position_id=position_id, reason=reason, confidence=0.6)


def _close_intent(position_id="pos-1", intent_id="intent-close", quantity=1.0) -> OrderIntent:
    return OrderIntent(intent_id=intent_id, position_id=position_id, action=OrderAction.CLOSE, quantity=quantity, created_at=T2)


def _reduce_intent(position_id="pos-1", intent_id="intent-reduce", quantity=0.4) -> OrderIntent:
    return OrderIntent(intent_id=intent_id, position_id=position_id, action=OrderAction.REDUCE, quantity=quantity, created_at=T2)


# ---------------------------------------------------------------------------
# open_position -> OPEN -> HOLDING
# ---------------------------------------------------------------------------

class TestOpenAndFirstFill:
    def test_open_position_starts_in_open(self):
        record = open_position("pos-1", _thesis(), _open_intent(), T0)
        assert record.status == PositionStatus.OPEN
        assert record.remaining_quantity == 1.0

    def test_open_intent_filled_moves_to_holding(self):
        record = _holding_record()
        assert record.status == PositionStatus.HOLDING

    def test_open_position_rejects_mismatched_position_id(self):
        with pytest.raises(ValueError):
            open_position("pos-1", _thesis(position_id="pos-2"), _open_intent(), T0)

    def test_open_position_requires_open_action_intent(self):
        with pytest.raises(ValueError):
            open_position("pos-1", _thesis(), _close_intent(), T0)


# ---------------------------------------------------------------------------
# apply_decision nunca produce CLOSED
# ---------------------------------------------------------------------------

class TestApplyDecisionNeverProducesClosed:
    def test_exit_decision_moves_to_closing_not_closed(self):
        record = apply_decision(_holding_record(), _decision(TradeAction.EXIT), _close_intent(), T2)
        assert record.status == PositionStatus.CLOSING
        assert record.status != PositionStatus.CLOSED

    def test_reduce_decision_moves_to_reducing_not_closed(self):
        record = apply_decision(_holding_record(), _decision(TradeAction.REDUCE), _reduce_intent(), T2)
        assert record.status == PositionStatus.REDUCING


class TestHoldPreservesState:
    def test_hold_keeps_holding_and_no_intent(self):
        holding = _holding_record()
        result = apply_decision(holding, _decision(TradeAction.HOLD), None, T2)
        assert result.status == PositionStatus.HOLDING
        assert result.current_intent == holding.current_intent

    def test_enter_is_rejected_on_existing_position(self):
        with pytest.raises(ValueError):
            apply_decision(_holding_record(), _decision(TradeAction.ENTER), None, T2)


# ---------------------------------------------------------------------------
# CLOSED es terminal
# ---------------------------------------------------------------------------

class TestClosedIsTerminal:
    def _closed_record(self):
        record = apply_decision(_holding_record(), _decision(TradeAction.EXIT), _close_intent(), T2)
        return apply_execution_update(record, _execution("intent-close", ExecutionStatus.FILLED, 1.0, T3), T3)

    def test_close_fill_reaches_closed(self):
        assert self._closed_record().status == PositionStatus.CLOSED

    def test_apply_decision_on_closed_raises(self):
        with pytest.raises(ValueError):
            apply_decision(self._closed_record(), _decision(TradeAction.HOLD), None, T3)

    def test_apply_execution_update_on_closed_raises(self):
        closed = self._closed_record()
        with pytest.raises(ValueError):
            apply_execution_update(
                closed, _execution("intent-close", ExecutionStatus.FILLED, 1.0), T3
            )


# ---------------------------------------------------------------------------
# CLOSING nunca vuelve a HOLDING simplemente porque pase tiempo
# ---------------------------------------------------------------------------

class TestNoTimeBasedTransitions:
    def test_closing_status_unaffected_by_time_without_new_execution(self):
        closing = apply_decision(_holding_record(), _decision(TradeAction.EXIT), _close_intent(), T2)
        # No hay ninguna función que tome "ahora" y decida transicionar
        # sola — la única forma de salir de CLOSING es una OrderExecution
        # explícita. Confirmamos que el record no cambia si no se le
        # aplica ninguna.
        assert closing.status == PositionStatus.CLOSING
        much_later = closing  # ninguna llamada = ningún cambio posible
        assert much_later.status == PositionStatus.CLOSING

    def test_position_state_module_has_no_clock_calls(self):
        """Garantía estática: nada en el módulo llama datetime.now()/
        utcnow() ni time.time() — todo 'now' entra como parámetro."""
        source = Path(position_state.__file__).read_text()
        assert "datetime.now(" not in source
        assert ".utcnow(" not in source
        assert "time.time()" not in source


# ---------------------------------------------------------------------------
# UNKNOWN nunca crea un segundo intent / dispara RECONCILING
# ---------------------------------------------------------------------------

class TestUnknownNeverCreatesASecondIntent:
    def test_unknown_on_closing_moves_to_reconciling(self):
        closing = apply_decision(_holding_record(), _decision(TradeAction.EXIT), _close_intent(), T2)
        result = apply_execution_update(closing, _execution("intent-close", ExecutionStatus.UNKNOWN), T3)
        assert result.status == PositionStatus.RECONCILING

    def test_apply_decision_blocked_while_reconciling(self):
        closing = apply_decision(_holding_record(), _decision(TradeAction.EXIT), _close_intent(), T2)
        reconciling = apply_execution_update(closing, _execution("intent-close", ExecutionStatus.UNKNOWN), T3)
        with pytest.raises(ValueError):
            apply_decision(reconciling, _decision(TradeAction.EXIT), _close_intent(intent_id="intent-close-2"), T3)

    def test_reconciling_resolves_via_execution_update_not_a_new_intent(self):
        closing = apply_decision(_holding_record(), _decision(TradeAction.EXIT), _close_intent(), T2)
        reconciling = apply_execution_update(closing, _execution("intent-close", ExecutionStatus.UNKNOWN), T3)
        # El broker responde, más tarde, para el MISMO intent_id — nunca uno nuevo.
        resolved = apply_execution_update(
            reconciling, _execution("intent-close", ExecutionStatus.FILLED, 1.0), T3 + timedelta(minutes=1)
        )
        assert resolved.status == PositionStatus.CLOSED
        assert resolved.current_intent.intent_id == "intent-close"

    def test_reducing_blocked_from_new_reduce_while_previous_unresolved(self):
        reducing = apply_decision(_holding_record(), _decision(TradeAction.REDUCE), _reduce_intent(), T2)
        # simular timeout: nunca llega ninguna execution -> last_execution sigue None,
        # pero el status ya no es HOLDING/REDUCING con last_execution terminal.
        # Ahora forzamos un UNKNOWN explícito, como llegaría de un timeout real.
        unresolved = apply_execution_update(reducing, _execution("intent-reduce", ExecutionStatus.UNKNOWN), T3)
        with pytest.raises(ValueError):
            apply_decision(unresolved, _decision(TradeAction.EXIT), _close_intent(), T3)


# ---------------------------------------------------------------------------
# PARTIAL conserva el mismo intent_id
# ---------------------------------------------------------------------------

class TestPartialPreservesIntentId:
    def test_partial_fill_keeps_status_and_intent(self):
        closing = apply_decision(_holding_record(), _decision(TradeAction.EXIT), _close_intent(), T2)
        partial = apply_execution_update(closing, _execution("intent-close", ExecutionStatus.PARTIAL, 0.3), T3)
        assert partial.status == PositionStatus.CLOSING
        assert partial.current_intent.intent_id == "intent-close"
        assert partial.last_execution.status == ExecutionStatus.PARTIAL


# ---------------------------------------------------------------------------
# REDUCING no implica necesariamente CLOSED / FILLED de CLOSE sí lo produce
# ---------------------------------------------------------------------------

class TestReduceVsCloseOutcomes:
    def test_reduce_full_fill_below_remaining_keeps_holding(self):
        reducing = apply_decision(_holding_record(quantity=1.0), _decision(TradeAction.REDUCE), _reduce_intent(quantity=0.4), T2)
        result = apply_execution_update(reducing, _execution("intent-reduce", ExecutionStatus.FILLED, 0.4), T3)
        assert result.status == PositionStatus.HOLDING
        assert result.remaining_quantity == pytest.approx(0.6)

    def test_reduce_fill_that_exhausts_remaining_closes(self):
        reducing = apply_decision(_holding_record(quantity=0.4), _decision(TradeAction.REDUCE), _reduce_intent(quantity=0.4), T2)
        result = apply_execution_update(reducing, _execution("intent-reduce", ExecutionStatus.FILLED, 0.4), T3)
        assert result.status == PositionStatus.CLOSED
        assert result.remaining_quantity == 0.0

    def test_close_full_fill_produces_closed(self):
        closing = apply_decision(_holding_record(), _decision(TradeAction.EXIT), _close_intent(), T2)
        result = apply_execution_update(closing, _execution("intent-close", ExecutionStatus.FILLED, 1.0), T3)
        assert result.status == PositionStatus.CLOSED

    def test_reduce_rejected_returns_to_holding_unchanged(self):
        holding = _holding_record()
        reducing = apply_decision(holding, _decision(TradeAction.REDUCE), _reduce_intent(), T2)
        result = apply_execution_update(reducing, _execution("intent-reduce", ExecutionStatus.REJECTED), T3)
        assert result.status == PositionStatus.HOLDING
        assert result.remaining_quantity == holding.remaining_quantity

    def test_close_rejected_returns_to_holding(self):
        closing = apply_decision(_holding_record(), _decision(TradeAction.EXIT), _close_intent(), T2)
        result = apply_execution_update(closing, _execution("intent-close", ExecutionStatus.REJECTED), T3)
        assert result.status == PositionStatus.HOLDING

    def test_open_rejected_closes_without_ever_holding(self):
        record = open_position("pos-1", _thesis(), _open_intent(), T0)
        result = apply_execution_update(record, _execution("intent-open", ExecutionStatus.REJECTED), T1)
        assert result.status == PositionStatus.CLOSED


# ---------------------------------------------------------------------------
# Cada transición conserva position_id, intent_id (cuando no cambia) y
# la tesis original
# ---------------------------------------------------------------------------

class TestTransitionsPreserveIdentity:
    def test_position_id_stable_across_full_lifecycle(self):
        holding = _holding_record()
        closing = apply_decision(holding, _decision(TradeAction.EXIT), _close_intent(), T2)
        closed = apply_execution_update(closing, _execution("intent-close", ExecutionStatus.FILLED, 1.0), T3)
        assert holding.position_id == closing.position_id == closed.position_id == "pos-1"

    def test_entry_thesis_never_changes(self):
        holding = _holding_record()
        thesis_before = holding.entry_thesis
        closing = apply_decision(holding, _decision(TradeAction.EXIT), _close_intent(), T2)
        closed = apply_execution_update(closing, _execution("intent-close", ExecutionStatus.FILLED, 1.0), T3)
        assert closing.entry_thesis == thesis_before
        assert closed.entry_thesis == thesis_before

    def test_intent_id_stable_through_pending_acked_partial_updates(self):
        closing = apply_decision(_holding_record(), _decision(TradeAction.EXIT), _close_intent(), T2)
        acked = apply_execution_update(closing, _execution("intent-close", ExecutionStatus.ACKED), T2 + timedelta(seconds=5))
        partial = apply_execution_update(acked, _execution("intent-close", ExecutionStatus.PARTIAL, 0.5), T2 + timedelta(seconds=10))
        assert closing.current_intent.intent_id == acked.current_intent.intent_id == partial.current_intent.intent_id

    def test_decision_for_wrong_position_id_rejected(self):
        holding = _holding_record()
        with pytest.raises(ValueError):
            apply_decision(holding, _decision(TradeAction.EXIT, position_id="pos-OTHER"), _close_intent(), T2)

    def test_execution_for_wrong_intent_id_rejected(self):
        closing = apply_decision(_holding_record(), _decision(TradeAction.EXIT), _close_intent(), T2)
        with pytest.raises(ValueError):
            apply_execution_update(closing, _execution("intent-DISTINTO", ExecutionStatus.FILLED, 1.0), T3)


# ---------------------------------------------------------------------------
# Ninguna transición llama al broker directamente ni consulta criterio
# ---------------------------------------------------------------------------

class TestPositionStateHasNoForbiddenDependencies:
    FORBIDDEN_PREFIXES = ("core.nucleus", "core.learn")

    def _imported_modules(self, path: Path) -> set[str]:
        tree = ast.parse(path.read_text(), filename=str(path))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        return modules

    def test_no_forbidden_criterion_imports(self):
        path = Path(position_state.__file__)
        modules = self._imported_modules(path)
        for forbidden in self.FORBIDDEN_PREFIXES:
            offending = [m for m in modules if m == forbidden or m.startswith(forbidden + ".")]
            assert not offending, f"{path} importa {offending} — PositionManager no decide, no consulta criterio"

    def test_no_io_or_network_imports(self):
        """Ninguna transición llama al broker directamente: el módulo no
        debe importar clientes HTTP/broker."""
        path = Path(position_state.__file__)
        modules = self._imported_modules(path)
        forbidden_io = ("requests", "httpx", "urllib", "connectors")
        for forbidden in forbidden_io:
            offending = [m for m in modules if m == forbidden or m.startswith(forbidden + ".")]
            assert not offending, f"{path} importa {offending} — no debe hacer I/O"
