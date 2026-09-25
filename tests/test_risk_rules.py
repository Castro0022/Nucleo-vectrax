"""
tests/test_risk_rules.py — cobertura de la matriz de reglas y precedencia
de core/trading/risk_rules.py (paso 2 de la arquitectura: reglas fijadas
y probadas ANTES de escribir risk_gate.py como módulo público).

Fija en tests, para que no pueda romperse en silencio al implementar el
gate:
  - HALT_NEW_RISK bloquea entradas pero NUNCA toca posiciones abiertas.
  - EMERGENCY_FLATTEN gana sobre cualquier otra condición, en ambas APIs.
  - El stop duro es side-aware (buy vs sell).
  - Los datos de mercado inválidos son fail-safe (bloquean, no asumen).
"""
from __future__ import annotations

import pytest

from core.trading.contracts import (
    AccountRiskSnapshot,
    MarketExecutionSnapshot,
    PositionRiskSnapshot,
    RiskAction,
    RiskLimits,
    RiskMode,
    TradeAction,
    TradeDecision,
)
from core.trading.risk_rules import (
    RULE_BROKER_EXECUTION_INVALID,
    RULE_EXPOSURE_BREACHED,
    RULE_HARD_STOP,
    RULE_INVALID_MARKET_DATA,
    RULE_KILL_SWITCH_FLATTEN,
    RULE_KILL_SWITCH_HALT,
    RULE_KILL_SWITCH_RECONCILE,
    RULE_MAX_DAILY_LOSS,
    RULE_MAX_OPEN_POSITIONS,
    RULE_MAX_TOTAL_EXPOSURE,
    RULE_POSITIONS_SYNC_INVALID,
    check_entry,
    check_open_position,
    compute_risk_mode,
)

LIMITS = RiskLimits(
    max_loss_per_trade_usd=50.0,
    max_daily_loss_usd=200.0,
    max_open_positions=3,
    max_total_exposure_usd=1000.0,
)


def _account(**overrides) -> AccountRiskSnapshot:
    base = dict(
        equity_usd=10_000.0,
        realized_pnl_today_usd=0.0,
        open_positions_count=0,
        total_exposure_usd=0.0,
        kill_switch=RiskMode.NORMAL,
        positions_sync_ok=True,
        broker_execution_ok=True,
    )
    base.update(overrides)
    return AccountRiskSnapshot(**base)


def _market(**overrides) -> MarketExecutionSnapshot:
    base = dict(
        symbol="BTCUSD", price=50_000.0, price_is_stale=False, broker_tradable=True,
    )
    base.update(overrides)
    return MarketExecutionSnapshot(**base)


def _proposal() -> TradeDecision:
    return TradeDecision(
        action=TradeAction.ENTER, position_id=None, reason="setup válido", confidence=0.7,
    )


def _position(**overrides) -> PositionRiskSnapshot:
    base = dict(
        position_id="pos-1",
        symbol="BTCUSD",
        side="buy",
        entry_price=50_000.0,
        current_price=50_500.0,
        hard_stop_price=49_000.0,
        size_usd=500.0,
    )
    base.update(overrides)
    return PositionRiskSnapshot(**base)


# ---------------------------------------------------------------------------
# compute_risk_mode — precedencia
# ---------------------------------------------------------------------------

class TestComputeRiskModePrecedence:
    def test_normal_when_nothing_triggered(self):
        mode, rule_id = compute_risk_mode(_account(), LIMITS)
        assert mode == RiskMode.NORMAL

    def test_kill_switch_flatten_wins_over_everything(self):
        account = _account(
            kill_switch=RiskMode.EMERGENCY_FLATTEN,
            positions_sync_ok=False,
            broker_execution_ok=False,
            realized_pnl_today_usd=-1000.0,
        )
        mode, rule_id = compute_risk_mode(account, LIMITS)
        assert mode == RiskMode.EMERGENCY_FLATTEN
        assert rule_id == RULE_KILL_SWITCH_FLATTEN

    def test_positions_sync_invalid_forces_reconcile_never_flatten(self):
        """No liquidar a ciegas: un estado de posiciones no confiable
        fuerza RECONCILE, no EMERGENCY_FLATTEN."""
        mode, rule_id = compute_risk_mode(_account(positions_sync_ok=False), LIMITS)
        assert mode == RiskMode.RECONCILE
        assert rule_id == RULE_POSITIONS_SYNC_INVALID

    def test_broker_execution_invalid_forces_reconcile_never_flatten(self):
        mode, rule_id = compute_risk_mode(_account(broker_execution_ok=False), LIMITS)
        assert mode == RiskMode.RECONCILE
        assert rule_id == RULE_BROKER_EXECUTION_INVALID

    def test_manual_kill_switch_reconcile(self):
        mode, rule_id = compute_risk_mode(
            _account(kill_switch=RiskMode.RECONCILE), LIMITS
        )
        assert mode == RiskMode.RECONCILE
        assert rule_id == RULE_KILL_SWITCH_RECONCILE

    def test_reconcile_wins_over_halt_conditions(self):
        account = _account(
            positions_sync_ok=False, realized_pnl_today_usd=-1000.0,
        )
        mode, rule_id = compute_risk_mode(account, LIMITS)
        assert mode == RiskMode.RECONCILE
        assert rule_id == RULE_POSITIONS_SYNC_INVALID

    def test_flatten_only_from_explicit_manual_kill_switch(self):
        """EMERGENCY_FLATTEN nunca se deriva de incertidumbre — solo del
        kill switch manual explícito."""
        account = _account(positions_sync_ok=False, broker_execution_ok=False)
        mode, _ = compute_risk_mode(account, LIMITS)
        assert mode != RiskMode.EMERGENCY_FLATTEN

    def test_kill_switch_halt(self):
        mode, rule_id = compute_risk_mode(
            _account(kill_switch=RiskMode.HALT_NEW_RISK), LIMITS
        )
        assert mode == RiskMode.HALT_NEW_RISK
        assert rule_id == RULE_KILL_SWITCH_HALT

    def test_daily_loss_at_exact_limit_triggers_halt(self):
        account = _account(realized_pnl_today_usd=-LIMITS.max_daily_loss_usd)
        mode, rule_id = compute_risk_mode(account, LIMITS)
        assert mode == RiskMode.HALT_NEW_RISK
        assert rule_id == RULE_MAX_DAILY_LOSS

    def test_daily_loss_below_limit_stays_normal(self):
        account = _account(realized_pnl_today_usd=-(LIMITS.max_daily_loss_usd - 1))
        mode, _ = compute_risk_mode(account, LIMITS)
        assert mode == RiskMode.NORMAL

    def test_winning_day_never_triggers_halt(self):
        mode, _ = compute_risk_mode(_account(realized_pnl_today_usd=5000.0), LIMITS)
        assert mode == RiskMode.NORMAL


# ---------------------------------------------------------------------------
# check_entry
# ---------------------------------------------------------------------------

class TestCheckEntry:
    def test_passes_when_all_clear(self):
        verdict = check_entry(_proposal(), _account(), _market(), LIMITS)
        assert verdict.forced_action == RiskAction.PASS
        assert verdict.passed is True

    def test_halt_new_risk_blocks_entry(self):
        account = _account(realized_pnl_today_usd=-LIMITS.max_daily_loss_usd)
        verdict = check_entry(_proposal(), account, _market(), LIMITS)
        assert verdict.forced_action == RiskAction.BLOCK_ENTRY
        assert verdict.rule_id == RULE_MAX_DAILY_LOSS

    def test_emergency_flatten_blocks_entry(self):
        account = _account(kill_switch=RiskMode.EMERGENCY_FLATTEN)
        verdict = check_entry(_proposal(), account, _market(), LIMITS)
        assert verdict.forced_action == RiskAction.BLOCK_ENTRY

    def test_reconcile_blocks_entry(self):
        account = _account(positions_sync_ok=False)
        verdict = check_entry(_proposal(), account, _market(), LIMITS)
        assert verdict.forced_action == RiskAction.BLOCK_ENTRY
        assert verdict.rule_id == RULE_POSITIONS_SYNC_INVALID

    @pytest.mark.parametrize(
        "market_overrides",
        [
            {"price": None},
            {"price_is_stale": True},
            {"broker_tradable": False},
        ],
    )
    def test_invalid_market_data_blocks_entry_fail_safe(self, market_overrides):
        verdict = check_entry(_proposal(), _account(), _market(**market_overrides), LIMITS)
        assert verdict.forced_action == RiskAction.BLOCK_ENTRY
        assert verdict.rule_id == RULE_INVALID_MARKET_DATA

    def test_max_open_positions_blocks_entry(self):
        account = _account(open_positions_count=LIMITS.max_open_positions)
        verdict = check_entry(_proposal(), account, _market(), LIMITS)
        assert verdict.forced_action == RiskAction.BLOCK_ENTRY
        assert verdict.rule_id == RULE_MAX_OPEN_POSITIONS

    def test_below_max_open_positions_passes(self):
        account = _account(open_positions_count=LIMITS.max_open_positions - 1)
        verdict = check_entry(_proposal(), account, _market(), LIMITS)
        assert verdict.forced_action == RiskAction.PASS

    def test_max_total_exposure_blocks_entry(self):
        account = _account(total_exposure_usd=LIMITS.max_total_exposure_usd)
        verdict = check_entry(_proposal(), account, _market(), LIMITS)
        assert verdict.forced_action == RiskAction.BLOCK_ENTRY
        assert verdict.rule_id == RULE_MAX_TOTAL_EXPOSURE

    def test_market_data_precedes_position_and_exposure_limits(self):
        """Aunque también se hayan alcanzado otros límites, el rule_id
        reportado debe ser el de mayor precedencia (datos inválidos)."""
        account = _account(
            open_positions_count=LIMITS.max_open_positions,
            total_exposure_usd=LIMITS.max_total_exposure_usd,
        )
        verdict = check_entry(_proposal(), account, _market(price=None), LIMITS)
        assert verdict.rule_id == RULE_INVALID_MARKET_DATA


# ---------------------------------------------------------------------------
# check_open_position
# ---------------------------------------------------------------------------

class TestCheckOpenPosition:
    def test_passes_when_all_clear(self):
        verdict = check_open_position(_position(), _account(), LIMITS)
        assert verdict.forced_action == RiskAction.PASS

    def test_halt_new_risk_does_not_touch_open_positions(self):
        """La regla explícita: pérdida diaria máxima nunca cierra ni
        reduce una posición abierta por sí sola."""
        account = _account(realized_pnl_today_usd=-LIMITS.max_daily_loss_usd)
        verdict = check_open_position(_position(), account, LIMITS)
        assert verdict.forced_action == RiskAction.PASS
        assert verdict.passed is True

    def test_kill_switch_halt_does_not_touch_open_positions(self):
        account = _account(kill_switch=RiskMode.HALT_NEW_RISK)
        verdict = check_open_position(_position(), account, LIMITS)
        assert verdict.forced_action == RiskAction.PASS

    def test_reconcile_does_not_force_exit_even_with_hard_stop_crossed(self):
        """El caso crítico que motivó separar RECONCILE de
        EMERGENCY_FLATTEN: con el estado sin reconciliar, ni siquiera el
        stop duro se fuerza — el snapshot de la posición puede no ser de
        fiar, y forzar un cierre a ciegas puede producir un duplicado o
        una posición contraria accidental."""
        account = _account(positions_sync_ok=False)
        losing_position = _position(side="buy", hard_stop_price=49_000.0, current_price=48_000.0)
        verdict = check_open_position(losing_position, account, LIMITS)
        assert verdict.forced_action == RiskAction.PASS
        assert verdict.rule_id == RULE_POSITIONS_SYNC_INVALID

    def test_reconcile_from_broker_execution_invalid_does_not_force_exit(self):
        account = _account(broker_execution_ok=False)
        losing_position = _position(side="buy", hard_stop_price=49_000.0, current_price=48_000.0)
        verdict = check_open_position(losing_position, account, LIMITS)
        assert verdict.forced_action == RiskAction.PASS
        assert verdict.rule_id == RULE_BROKER_EXECUTION_INVALID

    def test_emergency_flatten_overrides_exit_even_on_a_winning_position(self):
        account = _account(kill_switch=RiskMode.EMERGENCY_FLATTEN)
        winning_position = _position(current_price=60_000.0)  # muy por encima del stop
        verdict = check_open_position(winning_position, account, LIMITS)
        assert verdict.forced_action == RiskAction.OVERRIDE_EXIT
        assert verdict.rule_id == RULE_KILL_SWITCH_FLATTEN

    def test_hard_stop_crossed_on_buy_triggers_exit(self):
        position = _position(side="buy", hard_stop_price=49_000.0, current_price=48_999.0)
        verdict = check_open_position(position, _account(), LIMITS)
        assert verdict.forced_action == RiskAction.OVERRIDE_EXIT
        assert verdict.rule_id == RULE_HARD_STOP

    def test_hard_stop_not_crossed_on_buy_passes(self):
        position = _position(side="buy", hard_stop_price=49_000.0, current_price=49_001.0)
        verdict = check_open_position(position, _account(), LIMITS)
        assert verdict.forced_action == RiskAction.PASS

    def test_hard_stop_crossed_on_sell_triggers_exit(self):
        position = _position(side="sell", entry_price=50_000.0, hard_stop_price=51_000.0, current_price=51_001.0)
        verdict = check_open_position(position, _account(), LIMITS)
        assert verdict.forced_action == RiskAction.OVERRIDE_EXIT
        assert verdict.rule_id == RULE_HARD_STOP

    def test_hard_stop_not_crossed_on_sell_passes(self):
        position = _position(side="sell", entry_price=50_000.0, hard_stop_price=51_000.0, current_price=50_999.0)
        verdict = check_open_position(position, _account(), LIMITS)
        assert verdict.forced_action == RiskAction.PASS

    def test_missing_current_price_does_not_falsely_trigger_hard_stop(self):
        position = _position(current_price=None)
        verdict = check_open_position(position, _account(), LIMITS)
        assert verdict.forced_action == RiskAction.PASS

    def test_exposure_breached_triggers_reduce_not_exit(self):
        account = _account(total_exposure_usd=LIMITS.max_total_exposure_usd + 1)
        verdict = check_open_position(_position(), account, LIMITS)
        assert verdict.forced_action == RiskAction.OVERRIDE_REDUCE
        assert verdict.rule_id == RULE_EXPOSURE_BREACHED

    def test_exposure_at_exact_limit_does_not_trigger_reduce(self):
        """check_entry bloquea en >=; check_open_position solo reduce si
        el límite ya fue superado (>), no simplemente alcanzado."""
        account = _account(total_exposure_usd=LIMITS.max_total_exposure_usd)
        verdict = check_open_position(_position(), account, LIMITS)
        assert verdict.forced_action == RiskAction.PASS

    def test_hard_stop_precedes_exposure_breach(self):
        account = _account(total_exposure_usd=LIMITS.max_total_exposure_usd + 1)
        position = _position(side="buy", hard_stop_price=49_000.0, current_price=48_999.0)
        verdict = check_open_position(position, account, LIMITS)
        assert verdict.forced_action == RiskAction.OVERRIDE_EXIT
        assert verdict.rule_id == RULE_HARD_STOP
