"""
connectors/etoro/trading.py — eToro Supervised Trading
========================================================
Trade execution with mandatory creator approval.

Flow:
  1. propose_trade() → builds trade proposal, returns formatted message
  2. Creator approves via Telegram ("aprobar TRADE-xxx")
  3. execute_trade() → sends order to eToro API

Safety:
  - All trades require explicit approval (no auto-execution)
  - Maximum position size enforced
  - Only creator can approve
  - All actions logged
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from connectors.etoro.client import get_etoro_client
from connectors.etoro.market import resolve_instrument_id

logger = logging.getLogger("vectrax.etoro.trading")

# Hard limits — never exceeded
MAX_POSITION_USD = 5000.0  # max single position
MAX_LEVERAGE = 2           # max leverage

# Pending trade proposals (in-memory, cleared on restart)
_pending_trades: Dict[str, "TradeProposal"] = {}


@dataclass
class TradeProposal:
    """A proposed trade awaiting creator approval."""
    trade_id: str
    symbol: str
    instrument_id: int
    is_buy: bool
    amount: float           # USD
    leverage: int = 1
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    proposed_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending | approved | rejected | executed | failed

    @property
    def direction(self) -> str:
        return "BUY" if self.is_buy else "SELL"

    def to_message(self) -> str:
        """Format as Telegram message for approval."""
        lines = [
            f"📋 Trade Proposal: {self.trade_id}",
            "",
            f"  {self.direction} {self.symbol}",
            f"  Amount: ${self.amount:,.2f}",
            f"  Leverage: {self.leverage}x",
        ]
        if self.stop_loss:
            lines.append(f"  Stop Loss: ${self.stop_loss:,.2f}")
        if self.take_profit:
            lines.append(f"  Take Profit: ${self.take_profit:,.2f}")
        lines.extend([
            "",
            f"Para aprobar: aprobar {self.trade_id}",
            f"Para rechazar: rechazar {self.trade_id}",
        ])
        return "\n".join(lines)


def propose_trade(
    symbol: str,
    is_buy: bool = True,
    amount: float = 100.0,
    leverage: int = 1,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
) -> Optional[TradeProposal]:
    """Create a trade proposal. Does NOT execute — requires approval."""
    # Validate limits
    if amount > MAX_POSITION_USD:
        logger.warning("Trade amount $%.2f exceeds max $%.2f", amount, MAX_POSITION_USD)
        return None
    if leverage > MAX_LEVERAGE:
        leverage = MAX_LEVERAGE

    # Resolve instrument
    instrument_id = resolve_instrument_id(symbol)
    if instrument_id is None:
        logger.warning("Could not resolve eToro instrument: %s", symbol)
        return None

    trade_id = f"TRADE-{uuid.uuid4().hex[:8].upper()}"
    proposal = TradeProposal(
        trade_id=trade_id,
        symbol=symbol.upper(),
        instrument_id=instrument_id,
        is_buy=is_buy,
        amount=amount,
        leverage=leverage,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )
    _pending_trades[trade_id] = proposal
    logger.info("Trade proposed: %s %s $%.2f (id=%s)", proposal.direction, symbol, amount, trade_id)
    return proposal


def approve_trade(trade_id: str) -> Optional[Dict[str, Any]]:
    """Approve and execute a pending trade proposal."""
    trade_id = trade_id.upper()
    proposal = _pending_trades.get(trade_id)
    if not proposal:
        return {"error": f"Trade {trade_id} not found"}
    if proposal.status != "pending":
        return {"error": f"Trade {trade_id} already {proposal.status}"}

    proposal.status = "approved"
    result = _execute_order(proposal)
    if result and not result.get("error"):
        proposal.status = "executed"
        logger.info("Trade executed: %s → %s", trade_id, result)
    else:
        proposal.status = "failed"
        logger.error("Trade execution failed: %s → %s", trade_id, result)

    return result


def reject_trade(trade_id: str) -> bool:
    """Reject a pending trade proposal."""
    trade_id = trade_id.upper()
    proposal = _pending_trades.get(trade_id)
    if not proposal or proposal.status != "pending":
        return False
    proposal.status = "rejected"
    logger.info("Trade rejected: %s", trade_id)
    return True


def get_pending_trades() -> list:
    """Return all pending trade proposals."""
    return [p for p in _pending_trades.values() if p.status == "pending"]


def close_position(position_id: str, instrument_id: int) -> Optional[Dict]:
    """Close an existing position by ID."""
    c = get_etoro_client()
    result = c.post(
        f"{c.execution_prefix}/market-close-orders/positions/{position_id}",
        body={"InstrumentId": instrument_id, "UnitsToDeduct": None},
    )
    if result:
        logger.info("Position closed: %s", position_id)
    return result


def _execute_order(proposal: TradeProposal) -> Optional[Dict]:
    """Execute an approved trade on eToro."""
    c = get_etoro_client()
    body: Dict[str, Any] = {
        "InstrumentID": proposal.instrument_id,
        "IsBuy": proposal.is_buy,
        "Amount": proposal.amount,
        "Leverage": proposal.leverage,
    }
    if proposal.stop_loss is not None:
        body["StopLossRate"] = proposal.stop_loss
    if proposal.take_profit is not None:
        body["TakeProfitRate"] = proposal.take_profit

    return c.post(
        f"{c.execution_prefix}/market-open-orders/by-amount",
        body=body,
    )
