"""
tests/test_risk_gate.py — cobertura de core/trading/risk_gate.py (paso 3:
el módulo público que envuelve risk_rules.py; sin lógica nueva).

Cubre: que la fachada delega exactamente a risk_rules (mismo resultado,
mismos rule_id), y — la garantía arquitectónica central de todo este
diseño — que ningún archivo de core/trading importa core.nucleus ni
core.learn. Si alguna vez aparece uno de esos imports ahí, es la señal
de que se coló criterio en la jaula de seguridad.
"""
from __future__ import annotations

import ast
from pathlib import Path

from core.trading import risk_gate, risk_rules
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
    base = dict(symbol="BTCUSD", price=50_000.0, price_is_stale=False, broker_tradable=True)
    base.update(overrides)
    return MarketExecutionSnapshot(**base)


def _proposal() -> TradeDecision:
    return TradeDecision(action=TradeAction.ENTER, position_id=None, reason="r", confidence=0.7)


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


class TestRiskGateDelegatesToRiskRules:
    def test_check_entry_matches_risk_rules_directly(self):
        account = _account(kill_switch=RiskMode.HALT_NEW_RISK)
        via_gate = risk_gate.check_entry(_proposal(), account, _market(), LIMITS)
        via_rules = risk_rules.check_entry(_proposal(), account, _market(), LIMITS)
        assert via_gate == via_rules

    def test_check_open_position_matches_risk_rules_directly(self):
        account = _account()
        position = _position(current_price=48_000.0, hard_stop_price=49_000.0)
        via_gate = risk_gate.check_open_position(position, account, LIMITS)
        via_rules = risk_rules.check_open_position(position, account, LIMITS)
        assert via_gate == via_rules
        assert via_gate.forced_action == RiskAction.OVERRIDE_EXIT

    def test_current_risk_mode_reads_without_side_effects(self):
        account = _account(realized_pnl_today_usd=-LIMITS.max_daily_loss_usd)
        assert risk_gate.current_risk_mode(account, LIMITS) == RiskMode.HALT_NEW_RISK

    def test_pass_through_end_to_end(self):
        verdict = risk_gate.check_entry(_proposal(), _account(), _market(), LIMITS)
        assert verdict.passed is True


class TestRiskGateHasNoCriterionImports:
    """Garantía estática: RiskGate nunca consulta el Núcleo, memoria,
    gravity engine, señales o patrones. Si lo hiciera, la jaula dejaría
    de ser ciega y volvería a mezclar seguridad con criterio."""

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

    def test_risk_gate_module_has_no_forbidden_imports(self):
        path = Path(risk_gate.__file__)
        modules = self._imported_modules(path)
        for forbidden in self.FORBIDDEN_PREFIXES:
            offending = [m for m in modules if m == forbidden or m.startswith(forbidden + ".")]
            assert not offending, f"{path} importa {offending} — RiskGate no puede consultar criterio"

    def test_risk_rules_module_has_no_forbidden_imports(self):
        path = Path(risk_rules.__file__)
        modules = self._imported_modules(path)
        for forbidden in self.FORBIDDEN_PREFIXES:
            offending = [m for m in modules if m == forbidden or m.startswith(forbidden + ".")]
            assert not offending, f"{path} importa {offending} — RiskGate no puede consultar criterio"
