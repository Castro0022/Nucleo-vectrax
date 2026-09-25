"""
tests/test_portfolio_reconciler.py — cobertura de
connectors/etoro/portfolio_reconciler.py: comparación pura contra un
snapshot de portfolio, y el wrapper con I/O real (mockeado).

Cubre el ciclo completo que motivó este archivo: un CLOSE en UNKNOWN se
reconcilia observando si la posición sigue existiendo — nunca
reenviando la orden. Y fija, con nombre propio, que OPEN nunca se
reconcilia por inferencia.
"""
from __future__ import annotations

from connectors.etoro import portfolio_reconciler
from connectors.etoro.portfolio_reconciler import (
    ReconciliationOutcome,
    reconcile_close,
    reconcile_close_from_portfolio,
    reconcile_open,
)


def _portfolio_position(position_id="P123", amount=100.0):
    return {"positionID": position_id, "instrumentID": 42, "amount": amount}


class TestReconcileCloseFromPortfolioPure:
    def test_position_absent_confirms_closed(self):
        outcome, amount = reconcile_close_from_portfolio(
            "P123", expected_quantity=100.0, portfolio_positions=[],
        )
        assert outcome == ReconciliationOutcome.CONFIRMED_CLOSED
        assert amount is None

    def test_position_absent_among_others_confirms_closed(self):
        outcome, _ = reconcile_close_from_portfolio(
            "P123", expected_quantity=100.0,
            portfolio_positions=[_portfolio_position("P999", 50.0)],
        )
        assert outcome == ReconciliationOutcome.CONFIRMED_CLOSED

    def test_position_still_open_with_full_amount(self):
        outcome, amount = reconcile_close_from_portfolio(
            "P123", expected_quantity=100.0,
            portfolio_positions=[_portfolio_position("P123", 100.0)],
        )
        assert outcome == ReconciliationOutcome.STILL_OPEN_FULL
        assert amount == 100.0

    def test_position_still_open_with_amount_greater_than_expected(self):
        """Más grande de lo esperado — no se infiere una reducción."""
        outcome, amount = reconcile_close_from_portfolio(
            "P123", expected_quantity=100.0,
            portfolio_positions=[_portfolio_position("P123", 150.0)],
        )
        assert outcome == ReconciliationOutcome.STILL_OPEN_FULL

    def test_position_reduced(self):
        outcome, amount = reconcile_close_from_portfolio(
            "P123", expected_quantity=100.0,
            portfolio_positions=[_portfolio_position("P123", 40.0)],
        )
        assert outcome == ReconciliationOutcome.REDUCED_UNATTRIBUTED
        assert amount == 40.0

    def test_float_noise_within_epsilon_counts_as_full(self):
        outcome, _ = reconcile_close_from_portfolio(
            "P123", expected_quantity=100.0,
            portfolio_positions=[_portfolio_position("P123", 99.999)],
        )
        assert outcome == ReconciliationOutcome.STILL_OPEN_FULL

    def test_matching_is_string_safe_across_int_and_str_ids(self):
        outcome, _ = reconcile_close_from_portfolio(
            "123", expected_quantity=100.0,
            portfolio_positions=[{"positionID": 123, "instrumentID": 42, "amount": 100.0}],
        )
        assert outcome == ReconciliationOutcome.STILL_OPEN_FULL

    def test_missing_amount_field_does_not_infer_a_reduction(self):
        outcome, amount = reconcile_close_from_portfolio(
            "P123", expected_quantity=100.0,
            portfolio_positions=[{"positionID": "P123", "instrumentID": 42}],
        )
        assert outcome == ReconciliationOutcome.STILL_OPEN_FULL
        assert amount is None


class TestReconcileCloseWrapper:
    def test_portfolio_unavailable_is_surfaced_not_swallowed(self, monkeypatch):
        monkeypatch.setattr(
            "connectors.etoro.etoro_client.get_portfolio",
            lambda: {"success": False, "error": "HTTP 500"},
        )
        outcome, amount = reconcile_close("P123", expected_quantity=100.0)
        assert outcome == ReconciliationOutcome.PORTFOLIO_UNAVAILABLE
        assert amount is None

    def test_confirmed_closed_end_to_end(self, monkeypatch):
        monkeypatch.setattr(
            "connectors.etoro.etoro_client.get_portfolio",
            lambda: {"success": True, "positions": []},
        )
        outcome, _ = reconcile_close("P123", expected_quantity=100.0)
        assert outcome == ReconciliationOutcome.CONFIRMED_CLOSED

    def test_reduced_end_to_end(self, monkeypatch):
        monkeypatch.setattr(
            "connectors.etoro.etoro_client.get_portfolio",
            lambda: {"success": True, "positions": [_portfolio_position("P123", 30.0)]},
        )
        outcome, amount = reconcile_close("P123", expected_quantity=100.0)
        assert outcome == ReconciliationOutcome.REDUCED_UNATTRIBUTED
        assert amount == 30.0


class TestReconcileOpenNeverInfers:
    def test_reconcile_open_always_cannot_confirm(self):
        assert reconcile_open("any-intent-id") == ReconciliationOutcome.CANNOT_CONFIRM

    def test_reconcile_open_is_deterministic_regardless_of_input(self):
        for intent_id in ("i1", "", "a-very-different-intent-id"):
            assert reconcile_open(intent_id) == ReconciliationOutcome.CANNOT_CONFIRM
