"""
connectors/etoro/position_manager.py — Gestor de posiciones abiertas.

Evalúa condiciones de salida para posiciones PAPER abiertas:
  - Stop loss alcanzado
  - Take profit alcanzado
  - Pérdida de coherencia (gravity engine cc_score cae)
  - Señal contraria detectada
  - Tiempo máximo de posición expirado

Se ejecuta como parte del learning cycle.
No cierra posiciones LIVE automáticamente — solo propone.

API pública:
    check_open_positions() -> List[Dict]   (acciones tomadas)
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List

logger = logging.getLogger("vectrax.etoro.position_manager")


def check_open_positions() -> List[Dict[str, Any]]:
    """
    Check all open paper positions for exit conditions.
    Returns list of actions taken (closed positions).
    """
    from connectors.etoro.auto_executor import (
        get_paper_trades, get_config, record_trade_result,
    )

    cfg = get_config()
    max_hold_h = cfg.get("max_hold_hours", 24)
    actions = []

    open_trades = [t for t in get_paper_trades(limit=100) if t.status == "open"]
    if not open_trades:
        return actions

    for trade in open_trades:
        close_reason = _evaluate_exit(trade, max_hold_h)
        if close_reason:
            result = _close_paper_trade(trade, close_reason)
            if result:
                actions.append(result)
                # Record PnL
                pnl = result.get("pnl_usd", 0)
                record_trade_result(pnl, is_paper=True)
                # Log to observation ledger
                _log_exit_observation(trade, close_reason, result)

    if actions:
        logger.info(
            "[POSITION_MGR] Closed %d positions: %s",
            len(actions),
            [a.get("reason", "?") for a in actions],
        )

    return actions


def _evaluate_exit(trade, max_hold_h: float) -> str:
    """
    Evaluate exit conditions for a single trade.
    Returns reason string if should close, empty string if keep open.
    """
    now = time.time()
    elapsed_h = (now - trade.timestamp) / 3600

    # 1. Time expiry
    if elapsed_h >= max_hold_h:
        return f"tiempo_expirado ({elapsed_h:.1f}h >= {max_hold_h}h)"

    # 2. Get current price
    current_price = _get_current_price(trade.symbol)
    if current_price is None:
        return ""  # Can't evaluate without price

    # 3. Stop loss
    if trade.direction == "buy":
        if current_price <= trade.stop_loss:
            return f"stop_loss ({current_price:.5g} <= SL {trade.stop_loss:.5g})"
        if current_price >= trade.take_profit:
            return f"take_profit ({current_price:.5g} >= TP {trade.take_profit:.5g})"
    else:  # sell
        if current_price >= trade.stop_loss:
            return f"stop_loss ({current_price:.5g} >= SL {trade.stop_loss:.5g})"
        if current_price <= trade.take_profit:
            return f"take_profit ({current_price:.5g} <= TP {trade.take_profit:.5g})"

    # 4. Coherence loss (gravity engine cc_score dropped)
    if _check_coherence_loss(trade.symbol):
        return "coherencia_perdida (cc_score cayó en gravity engine)"

    # 5. Contrary signal
    if _check_contrary_signal(trade.symbol, trade.direction):
        opposite = "sell" if trade.direction == "buy" else "buy"
        return f"señal_contraria (nueva señal {opposite} detectada)"

    return ""


def _get_current_price(symbol: str) -> float | None:
    """Get current price for symbol."""
    try:
        from connectors.etoro.market import get_price
        p = get_price(symbol)
        if p and "last" in p:
            return float(p["last"])
    except Exception:
        pass
    # Fallback: try market observer
    try:
        from connectors.etoro.market_observer import evaluate
        result = evaluate(symbol=symbol, direction="buy")
        if result.price > 0:
            return result.price
    except Exception:
        pass
    return None


def _check_coherence_loss(symbol: str) -> bool:
    """Check if gravity engine coherence score dropped significantly."""
    try:
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
        rec = gi.get(f"market:{symbol.upper()}")
        if rec and rec.cc_score < 0.1:
            return True
    except Exception:
        pass
    return False


def _check_contrary_signal(symbol: str, direction: str) -> bool:
    """Check if a contrary signal was recorded in last 2 hours."""
    try:
        from connectors.etoro.signal_recorder import load_signals
        opposite = "sell" if direction == "buy" else "buy"
        cutoff = time.time() - 7200  # 2h
        signals = load_signals()
        return any(
            s.symbol == symbol.upper()
            and s.direction == opposite
            and s.timestamp > cutoff
            and s.scenario == "OPERABLE"
            for s in signals
        )
    except Exception:
        return False


def _close_paper_trade(trade, reason: str) -> Dict[str, Any] | None:
    """Close a paper trade by updating its status in the JSONL file."""
    try:
        from connectors.etoro.auto_executor import _PAPER_LOG_FILE
        current_price = _get_current_price(trade.symbol)
        if current_price is None:
            current_price = trade.entry_price  # fallback

        # Calculate PnL
        if trade.direction == "buy":
            pnl_pct = (current_price - trade.entry_price) / trade.entry_price * 100
        else:
            pnl_pct = (trade.entry_price - current_price) / trade.entry_price * 100
        pnl_usd = trade.amount_usd * (pnl_pct / 100)

        status = "closed_win" if pnl_usd > 0 else "closed_loss" if pnl_usd < 0 else "closed_neutral"

        # Update the trade in the JSONL file
        if os.path.exists(_PAPER_LOG_FILE):
            lines = []
            with open(_PAPER_LOG_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        if d.get("trade_id") == trade.trade_id:
                            d["status"] = status
                            d["exit_price"] = current_price
                            d["pnl_usd"] = round(pnl_usd, 2)
                            d["pnl_pct"] = round(pnl_pct, 3)
                        lines.append(json.dumps(d, ensure_ascii=False))
                    except Exception:
                        lines.append(line)
            with open(_PAPER_LOG_FILE, "w") as f:
                f.write("\n".join(lines) + "\n")

        logger.info(
            "[PAPER_CLOSE] %s | %s %s | PnL=$%.2f (%.2f%%) | reason=%s",
            trade.trade_id, trade.direction.upper(), trade.symbol,
            pnl_usd, pnl_pct, reason,
        )

        return {
            "trade_id": trade.trade_id,
            "symbol": trade.symbol,
            "direction": trade.direction,
            "entry_price": trade.entry_price,
            "exit_price": current_price,
            "pnl_usd": round(pnl_usd, 2),
            "pnl_pct": round(pnl_pct, 3),
            "status": status,
            "reason": reason,
        }
    except Exception as exc:
        logger.error("close_paper_trade error: %s", exc)
        return None


def _log_exit_observation(trade, reason: str, result: Dict) -> None:
    """Log position exit to observation ledger."""
    try:
        from core.self_observation.observation_ledger import record
        pnl = result.get("pnl_usd", 0)
        icon = "✅" if pnl > 0 else "❌" if pnl < 0 else "➖"
        record(
            domain="market",
            obs_type="position_closed",
            summary=(
                f"{icon} Posición cerrada: {trade.direction.upper()} {trade.symbol} "
                f"PnL=${pnl:.2f} | Razón: {reason[:60]}"
            ),
            star_id=f"market:{trade.symbol}",
            evidence={
                "trade_id": trade.trade_id,
                "pnl_usd": pnl,
                "reason": reason,
                "status": result.get("status"),
            },
        )
    except Exception:
        pass


def get_open_positions_summary() -> str:
    """Format open positions for Telegram display."""
    from connectors.etoro.auto_executor import get_paper_trades

    open_trades = [t for t in get_paper_trades(limit=100) if t.status == "open"]
    if not open_trades:
        return "Sin posiciones abiertas."

    lines = [f"📊 {len(open_trades)} posición(es) abierta(s):\n"]
    for t in open_trades:
        elapsed_h = (time.time() - t.timestamp) / 3600
        current = _get_current_price(t.symbol)
        pnl_str = ""
        if current:
            if t.direction == "buy":
                pnl = (current - t.entry_price) / t.entry_price * 100
            else:
                pnl = (t.entry_price - current) / t.entry_price * 100
            icon = "🟢" if pnl > 0 else "🔴"
            pnl_str = f" | {icon} {pnl:+.2f}%"

        lines.append(
            f"  {t.trade_id}\n"
            f"    {t.direction.upper()} {t.symbol} ${t.amount_usd:.0f}\n"
            f"    Entry: {t.entry_price:.5g} | SL: {t.stop_loss:.5g} | TP: {t.take_profit:.5g}\n"
            f"    Tiempo: {elapsed_h:.1f}h{pnl_str}"
        )
    return "\n".join(lines)
