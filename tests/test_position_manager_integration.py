"""
tests/test_position_manager_integration.py — cobertura de la migración
de connectors/etoro/position_manager.py a la arquitectura RiskGate +
position_state.py (paso 5).

Dos garantías, además del comportamiento funcional:

  1. AST estática: `_check_coherence_loss`/`_check_contrary_signal` ya
     NO existen en position_manager.py — salieron por completo (viven
     en trade_decision_engine.py, el TradeDecisionEngine real), y
     position_manager.py no importa gravity_engine ni signal_recorder
     directamente.

  2. Invariante de migración: ningún camino cierra una posición por
     fuera de `position_state.py`. Se verifica interceptando
     `apply_execution_update` (la única función que puede producir
     CLOSED) y comprobando que `_close_paper_trade` se invoca si y solo
     si esa función devolvió CLOSED.
"""
from __future__ import annotations

import ast
import json
import time
from pathlib import Path

import pytest

from connectors.etoro import position_manager, trade_decision_engine
from connectors.etoro.auto_executor import PaperTrade
from core.trading.contracts import PositionStatus
from core.trading import position_state as _position_state_module


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


def _trade(**overrides) -> PaperTrade:
    base = dict(
        trade_id="PAPER-1",
        timestamp=time.time(),
        symbol="BTCUSD",
        direction="buy",
        amount_usd=100.0,
        entry_price=50_000.0,
        stop_loss=49_000.0,
        take_profit=60_000.0,
        proposal_id="prop-1",
        status="open",
    )
    base.update(overrides)
    return PaperTrade(**base)


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Conecta position_manager.check_open_positions() a dobles de
    prueba: config/trades/log en memoria o tmp, sin I/O real de mercado
    ni de causal_learning/observation_ledger."""
    paper_log = tmp_path / "paper_trades.jsonl"

    state = {"cfg": _cfg(), "trades": [], "results": [], "price": 50_100.0}

    def _get_config():
        return dict(state["cfg"])

    def _get_paper_trades(limit=100):
        return list(state["trades"])

    def _record_trade_result(pnl, is_paper=True, trade_id=None):
        state["results"].append({"pnl": pnl, "trade_id": trade_id})

    def _get_current_price(symbol):
        return state["price"]

    import connectors.etoro.auto_executor as auto_executor_module

    monkeypatch.setattr(auto_executor_module, "get_config", _get_config)
    monkeypatch.setattr(auto_executor_module, "get_paper_trades", _get_paper_trades)
    monkeypatch.setattr(auto_executor_module, "record_trade_result", _record_trade_result)
    monkeypatch.setattr(auto_executor_module, "_PAPER_LOG_FILE", str(paper_log))
    monkeypatch.setattr(position_manager, "_get_current_price", _get_current_price)

    def _seed(trades):
        state["trades"] = trades
        paper_log.write_text(
            "\n".join(json.dumps(t.to_dict(), ensure_ascii=False) for t in trades) + "\n"
        )

    state["seed"] = _seed
    return state


# ---------------------------------------------------------------------------
# AST — el criterio ya no vive en position_manager.py
# ---------------------------------------------------------------------------

class TestCriterionFullyRemoved:
    PATH = Path("connectors/etoro/position_manager.py")

    def _defined_function_names(self) -> set[str]:
        tree = ast.parse(self.PATH.read_text())
        return {
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }

    def _imported_modules(self) -> set[str]:
        tree = ast.parse(self.PATH.read_text())
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        return modules

    def test_coherence_and_contrary_signal_functions_are_gone(self):
        names = self._defined_function_names()
        assert "_check_coherence_loss" not in names
        assert "_check_contrary_signal" not in names

    def test_no_direct_gravity_or_signal_imports(self):
        modules = self._imported_modules()
        assert not any(m.startswith("core.learn.gravity_engine") for m in modules)
        assert not any(m.startswith("connectors.etoro.signal_recorder") for m in modules)


# ---------------------------------------------------------------------------
# Comportamiento: HOLD / EXIT vía criterio / EXIT vía RiskGate / REDUCE
# ---------------------------------------------------------------------------

class TestCheckOpenPositionsBehavior:
    def test_hold_does_not_close_or_record_anything(self, wired):
        wired["seed"]([_trade()])
        wired["price"] = 50_100.0  # ni stop ni take_profit
        actions = position_manager.check_open_positions()
        assert actions == []
        assert wired["results"] == []

    def test_thesis_invalidation_via_trade_decision_engine_closes(self, wired, monkeypatch):
        """El take-profit de precio fijo se retiró del criterio — la
        salida por ganancia ahora depende de evidencia (aquí, pérdida de
        coherencia), no de cruzar un precio fijado al entrar."""
        monkeypatch.setattr(trade_decision_engine, "_coherence_lost", lambda symbol: True)
        wired["seed"]([_trade()])
        wired["price"] = 61_000.0  # posición ganadora; igual sale por invalidación
        actions = position_manager.check_open_positions()
        assert len(actions) == 1
        assert "cc_score" in actions[0]["reason"]
        assert actions[0]["status"] == "closed_win"
        assert wired["results"] == [{"pnl": actions[0]["pnl_usd"], "trade_id": "PAPER-1"}]

    def test_large_favorable_move_alone_does_not_close_the_position(self, wired):
        """Sin invalidación de tesis ni límite de tiempo, una posición
        muy ganadora simplemente se mantiene — ya no hay take-profit
        fijo que la cierre por sí solo."""
        wired["seed"]([_trade()])
        wired["price"] = 61_000.0  # muy por encima de la entrada
        actions = position_manager.check_open_positions()
        assert actions == []

    def test_hard_stop_via_risk_gate_closes_even_if_criterion_never_runs(self, wired):
        wired["seed"]([_trade()])
        wired["price"] = 48_000.0  # < stop_loss=49000
        actions = position_manager.check_open_positions()
        assert len(actions) == 1
        assert "hard_stop" in actions[0]["reason"]
        assert actions[0]["status"] == "closed_loss"

    def test_risk_gate_is_always_consulted_before_criterion(self, wired, monkeypatch):
        """RiskGate se consulta SIEMPRE primero — este test confirma que
        su veredicto llega con el rule_id correcto y sin pasar por
        trade_decision_engine, aunque el criterio también habría
        disparado un EXIT por su cuenta."""
        monkeypatch.setattr(trade_decision_engine, "_coherence_lost", lambda symbol: True)
        wired["seed"]([_trade()])
        wired["price"] = 48_000.0  # cruza el stop duro
        actions = position_manager.check_open_positions()
        assert actions[0]["reason"].startswith("RiskGate:")

    def test_exposure_breach_produces_reduce_and_does_not_close(self, wired):
        wired["cfg"] = _cfg(max_position_usd=10.0, max_positions_open=1)  # max_total_exposure=10
        wired["seed"]([_trade(amount_usd=100.0)])  # exposición 100 > límite 10
        wired["price"] = 50_100.0  # sin cruzar stop ni take_profit
        actions = position_manager.check_open_positions()
        assert actions == []  # nunca cierra por un REDUCE
        assert wired["results"] == []

    def test_multiple_open_trades_are_each_evaluated_independently(self, wired, monkeypatch):
        monkeypatch.setattr(trade_decision_engine, "_coherence_lost", lambda symbol: True)
        a = _trade(trade_id="PAPER-A", symbol="BTCUSD")
        b = _trade(trade_id="PAPER-B", symbol="ETHUSD", entry_price=3_000.0, stop_loss=2_900.0, take_profit=3_500.0)
        wired["seed"]([a, b])
        wired["price"] = 61_000.0  # muy por encima de ambos stops; ambas salen por invalidación
        actions = position_manager.check_open_positions()
        assert {a["trade_id"] for a in actions} == {"PAPER-A", "PAPER-B"}


# ---------------------------------------------------------------------------
# Invariante de migración: nada cierra por fuera de position_state.py
# ---------------------------------------------------------------------------

class TestNothingClosesOutsidePositionState:
    def test_close_paper_trade_only_called_when_apply_execution_update_says_closed(
        self, wired, monkeypatch
    ):
        monkeypatch.setattr(trade_decision_engine, "_coherence_lost", lambda symbol: True)
        calls = {"apply_execution_update": [], "close_paper_trade": []}

        real_apply_execution_update = _position_state_module.apply_execution_update

        def _spy_apply_execution_update(record, execution, now):
            result = real_apply_execution_update(record, execution, now)
            calls["apply_execution_update"].append(result.status)
            return result

        real_close_paper_trade = position_manager._close_paper_trade

        def _spy_close_paper_trade(trade, reason, current_price):
            calls["close_paper_trade"].append(trade.trade_id)
            return real_close_paper_trade(trade, reason, current_price)

        monkeypatch.setattr(position_manager, "apply_execution_update", _spy_apply_execution_update)
        monkeypatch.setattr(position_manager, "_close_paper_trade", _spy_close_paper_trade)

        wired["seed"]([_trade()])
        wired["price"] = 61_000.0  # invalidación de tesis -> EXIT -> debería cerrar
        position_manager.check_open_positions()

        assert calls["apply_execution_update"] == [PositionStatus.CLOSED]
        assert calls["close_paper_trade"] == ["PAPER-1"]

    def test_close_paper_trade_never_called_when_apply_execution_update_not_invoked(
        self, wired, monkeypatch
    ):
        """Camino HOLD: ni siquiera se llega a construir un OrderIntent ni
        a invocar position_state — _close_paper_trade no se llama."""
        calls = []
        real_close_paper_trade = position_manager._close_paper_trade

        def _spy_close_paper_trade(trade, reason, current_price):
            calls.append(trade.trade_id)
            return real_close_paper_trade(trade, reason, current_price)

        monkeypatch.setattr(position_manager, "_close_paper_trade", _spy_close_paper_trade)

        wired["seed"]([_trade()])
        wired["price"] = 50_100.0  # HOLD
        position_manager.check_open_positions()

        assert calls == []
