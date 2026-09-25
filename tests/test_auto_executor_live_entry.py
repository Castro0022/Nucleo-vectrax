"""
tests/test_auto_executor_live_entry.py — cobertura de la conexión ENTER
(paso 10): auto_executor.execute_proposal() -> live_position_store.py.

Cubre el objetivo pedido: una apertura LIVE CONFIRMADA (success=True,
con position_id real) crea inmediatamente su PositionRecord persistido
y queda bajo supervisión autónoma. Y, deliberadamente, el caso que se
dejó fuera de alcance: un OPEN indeterminado (timeout) NUNCA crea un
PositionRecord — correlacionar una posición nueva del portfolio con esa
propuesta sin un identificador real sería inventar una atribución.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from connectors.etoro import auto_executor, live_position_store, trade_executor
from connectors.etoro.auto_executor import AutoMode
from connectors.etoro.trade_executor import ExecutionResult
from core.trading.contracts import PositionStatus


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_executor, "_CONFIG_FILE", str(tmp_path / "auto_cfg.json"))
    monkeypatch.setattr(auto_executor, "_PAPER_LOG_FILE", str(tmp_path / "paper_trades.jsonl"))
    monkeypatch.setattr(live_position_store, "_LIVE_POSITIONS_FILE", str(tmp_path / "live.jsonl"))
    auto_executor.update_config({
        "mode": AutoMode.LIVE.value,
        "max_position_usd": 200.0,
        "daily_loss_usd": 0.0,
        "max_daily_loss_usd": 100.0,
        "halt": False,
    })
    monkeypatch.setenv("TELEGRAM_CREATOR_CHAT_ID", "999")
    return tmp_path


@dataclass
class _FakeProposal:
    proposal_id: str = "prop-1"
    timestamp: float = 0.0
    symbol: str = "BTCUSD"
    direction: str = "buy"
    entry_price: float = 50_000.0
    stop_loss: float = 0.0
    take_profit: float = 60_000.0
    confidence: str = "HIGH"
    win_rate: float = 65.0
    expectancy: float = 1.2
    pattern_n: int = 40
    scenario_state: str = "OPERABLE"
    conditions_met: int = 4
    reasoning: str = "ruptura confirmada + volumen + cc_score alto"
    status: str = "pending"


@pytest.fixture(autouse=True)
def _wired_proposal(monkeypatch):
    proposal = _FakeProposal()
    from connectors.etoro import learning_engine

    monkeypatch.setattr(learning_engine, "load_proposals", lambda limit=200, status_filter="pending": [proposal])
    calls = {"updated_status": []}
    monkeypatch.setattr(
        learning_engine, "update_proposal_status",
        lambda pid, status: calls["updated_status"].append((pid, status)),
    )
    return proposal, calls


def _execute_open_success(**kwargs) -> ExecutionResult:
    return ExecutionResult(
        success=True, action="open", symbol="BTCUSD", environment="real",
        order_id="order-1", position_id="broker-pos-1", instrument_id=42,
        amount=100.0, is_buy=True,
    )


def _execute_open_indeterminate(**kwargs) -> ExecutionResult:
    return ExecutionResult(
        success=False, action="open", symbol="BTCUSD", environment="real",
        error="Timeout: simulated", indeterminate=True,
    )


def _execute_open_rejected(**kwargs) -> ExecutionResult:
    return ExecutionResult(
        success=False, action="open", symbol="BTCUSD", environment="real",
        error="HTTP 400", indeterminate=False,
    )


class TestConfirmedOpenCreatesSupervisedPosition:
    def test_success_persists_a_holding_position_record(self, isolated, monkeypatch):
        monkeypatch.setattr(trade_executor, "execute_open", _execute_open_success)

        result = auto_executor.execute_proposal("prop-1")

        assert result["success"] is True
        entry = live_position_store.get("broker-pos-1")
        assert entry is not None
        assert entry.record.status == PositionStatus.HOLDING
        assert entry.broker_position_id == "broker-pos-1"
        assert entry.instrument_id == 42
        assert entry.entry_price == 50_000.0

    def test_thesis_carries_real_evidence_from_the_proposal(self, isolated, monkeypatch):
        monkeypatch.setattr(trade_executor, "execute_open", _execute_open_success)
        auto_executor.execute_proposal("prop-1")

        thesis = live_position_store.get("broker-pos-1").record.entry_thesis
        assert thesis.thesis == "ruptura confirmada + volumen + cc_score alto"
        assert thesis.confidence_at_entry == 0.85  # HIGH
        assert thesis.evidence_snapshot["win_rate"] == 65.0
        assert thesis.evidence_snapshot_id == "proposal:prop-1"
        assert thesis.symbol == "BTCUSD"
        assert thesis.side == "buy"

    def test_position_is_immediately_open_for_the_live_supervision_loop(self, isolated, monkeypatch):
        monkeypatch.setattr(trade_executor, "execute_open", _execute_open_success)
        auto_executor.execute_proposal("prop-1")
        assert len(live_position_store.open_positions()) == 1

    def test_proposal_marked_executed(self, isolated, monkeypatch, _wired_proposal):
        monkeypatch.setattr(trade_executor, "execute_open", _execute_open_success)
        _, calls = _wired_proposal
        auto_executor.execute_proposal("prop-1")
        assert calls["updated_status"] == [("prop-1", "executed")]


class TestIndeterminateOpenNeverCreatesAPosition:
    def test_timeout_creates_no_position_record(self, isolated, monkeypatch):
        monkeypatch.setattr(trade_executor, "execute_open", _execute_open_indeterminate)

        result = auto_executor.execute_proposal("prop-1")

        assert result["success"] is False
        assert result["indeterminate"] is True
        assert live_position_store.open_positions() == []

    def test_timeout_does_not_mark_proposal_executed(self, isolated, monkeypatch, _wired_proposal):
        monkeypatch.setattr(trade_executor, "execute_open", _execute_open_indeterminate)
        _, calls = _wired_proposal
        auto_executor.execute_proposal("prop-1")
        assert calls["updated_status"] == []


class TestExplicitRejectionNeverCreatesAPosition:
    def test_rejection_creates_no_position_and_is_not_marked_indeterminate(self, isolated, monkeypatch):
        monkeypatch.setattr(trade_executor, "execute_open", _execute_open_rejected)

        result = auto_executor.execute_proposal("prop-1")

        assert result["success"] is False
        assert result.get("indeterminate", False) is False
        assert live_position_store.open_positions() == []


class TestMissingPositionIdDespiteSuccessIsDefensive:
    def test_no_position_id_does_not_crash_and_does_not_persist(self, isolated, monkeypatch):
        def _success_without_position_id(**kwargs) -> ExecutionResult:
            return ExecutionResult(
                success=True, action="open", symbol="BTCUSD", environment="real",
                order_id="order-1", position_id=None, instrument_id=42,
            )

        monkeypatch.setattr(trade_executor, "execute_open", _success_without_position_id)

        result = auto_executor.execute_proposal("prop-1")

        assert result["success"] is True  # la orden sí se reporta ejecutada
        assert live_position_store.open_positions() == []  # pero no hay con qué supervisarla
