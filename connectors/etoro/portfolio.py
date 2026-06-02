"""
connectors/etoro/portfolio.py — eToro Portfolio Tracking
=========================================================
Read portfolio positions, PnL, equity, and trade history.
All read-only operations — no trades executed here.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from connectors.etoro.client import get_etoro_client

logger = logging.getLogger("vectrax.etoro.portfolio")


def get_pnl() -> Optional[Dict[str, Any]]:
    """Get full PnL snapshot: positions, equity, available balance."""
    c = get_etoro_client()
    return c.get(f"{c.info_prefix}/pnl")


def get_positions() -> List[Dict[str, Any]]:
    """Get list of open positions."""
    pnl = get_pnl()
    if not pnl:
        return []
    return pnl.get("positions", [])


def get_equity() -> Optional[float]:
    """Get current account equity."""
    pnl = get_pnl()
    if not pnl:
        return None
    return pnl.get("equity") or pnl.get("Equity")


def get_available_balance() -> Optional[float]:
    """Get available balance for trading."""
    pnl = get_pnl()
    if not pnl:
        return None
    return pnl.get("availableBalance") or pnl.get("AvailableBalance")


def get_trade_history(min_date: str = "2024-01-01") -> Optional[List[Dict]]:
    """Get closed trade history since min_date (YYYY-MM-DD)."""
    c = get_etoro_client()
    return c.get(
        f"{c.info_prefix}/trade-history",
        params={"minDate": min_date},
    )


def format_portfolio_summary() -> str:
    """Build a human-readable portfolio summary for Telegram."""
    pnl = get_pnl()
    if not pnl:
        return "No se pudo obtener el portafolio de eToro."

    equity = pnl.get("equity", 0)
    available = pnl.get("availableBalance", 0)
    positions = pnl.get("positions", [])
    total_pnl = sum(p.get("netProfit", 0) for p in positions)
    pnl_pct = (total_pnl / max(equity - total_pnl, 1)) * 100

    lines = [
        "📊 eToro Portfolio",
        "",
        f"💰 Equity: ${equity:,.2f}",
        f"💵 Disponible: ${available:,.2f}",
        f"📈 P&L: ${total_pnl:,.2f} ({pnl_pct:+.1f}%)",
        f"📂 Posiciones: {len(positions)}",
    ]

    if positions:
        lines.append("")
        # Sort by absolute P&L
        sorted_pos = sorted(positions, key=lambda p: abs(p.get("netProfit", 0)), reverse=True)
        for p in sorted_pos[:10]:
            symbol = p.get("instrumentName") or p.get("symbol") or f"ID:{p.get('instrumentId', '?')}"
            direction = "🟢 LONG" if p.get("isBuy") else "🔴 SHORT"
            profit = p.get("netProfit", 0)
            invested = p.get("investedAmount") or p.get("amount", 0)
            pct = (profit / max(invested, 1)) * 100
            lines.append(f"  {symbol} {direction} ${invested:,.0f} → {profit:+,.2f} ({pct:+.1f}%)")

    return "\n".join(lines)
