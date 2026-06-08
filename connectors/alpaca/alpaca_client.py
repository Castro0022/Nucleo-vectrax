"""
Vectrax Alpaca Client — Direct broker connector
==================================================
Single file. No abstractions. Does everything eToro was supposed to do.

Functions:
  health_check()                → {connected, environment, balance}
  get_account()                 → {equity, cash, buying_power}
  get_positions()               → [{symbol, qty, avg_entry, current, pnl}]
  get_price(symbol)             → {bid, ask, mid, last}
  get_bars(symbol, tf, count)   → [{open, high, low, close, volume, time}]
  submit_order(symbol, side, notional) → {order_id, status, filled_price}
  close_position(symbol)        → {success, order_id}
  list_orders(status)           → [{order_id, symbol, side, status}]

Risk limits (hardcoded):
  MAX_ORDER_USD       = 20
  MAX_POSITIONS       = 5
  MAX_EXPOSURE_USD    = 100
  No margin, no leverage.
  Kill switch: ALPACA_HALT=true

Audit: every operation → ~/.vectrax/alpaca_trade_audit.jsonl

Creado: 2026-06-08
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.alpaca")

# ── Risk limits (hardcoded) ───────────────────────────────────────────
MAX_ORDER_USD = 20.0
MAX_POSITIONS = 5
MAX_EXPOSURE_USD = 100.0

# ── Audit ─────────────────────────────────────────────────────────────
_AUDIT_FILE = os.path.join(
    os.path.expanduser("~"), ".vectrax", "alpaca_trade_audit.jsonl"
)


def _log_audit(record: Dict) -> None:
    try:
        os.makedirs(os.path.dirname(_AUDIT_FILE), exist_ok=True)
        record["ts"] = time.time()
        with open(_AUDIT_FILE, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        logger.warning("alpaca audit log failed: %s", e)


# ── Client singletons ────────────────────────────────────────────────

def _get_keys():
    api_key = os.environ.get("ALPACA_API_KEY", "").strip()
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "").strip()
    if not api_key or not secret_key:
        raise EnvironmentError(
            "ALPACA_API_KEY y ALPACA_SECRET_KEY deben estar configurados en .env"
        )
    return api_key, secret_key


def _is_paper() -> bool:
    return os.environ.get("ALPACA_PAPER", "true").lower() != "false"


def _is_halted() -> bool:
    return os.environ.get("ALPACA_HALT", "").lower() == "true"


def _trading_client():
    from alpaca.trading.client import TradingClient
    api_key, secret_key = _get_keys()
    return TradingClient(api_key, secret_key, paper=_is_paper())


def _data_client():
    from alpaca.data.historical import StockHistoricalDataClient
    api_key, secret_key = _get_keys()
    return StockHistoricalDataClient(api_key, secret_key)


# ── Public API ────────────────────────────────────────────────────────

def health_check() -> Dict[str, Any]:
    """Verify Alpaca connectivity and return account summary."""
    t0 = time.time()
    try:
        client = _trading_client()
        account = client.get_account()
        latency = round((time.time() - t0) * 1000, 1)
        return {
            "success": True,
            "connected": True,
            "environment": "paper" if _is_paper() else "live",
            "equity": float(account.equity),
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
            "latency_ms": latency,
        }
    except Exception as exc:
        return {"success": False, "connected": False, "error": str(exc)}


def get_account() -> Dict[str, Any]:
    """Get account details: equity, cash, buying power, positions count."""
    try:
        client = _trading_client()
        a = client.get_account()
        return {
            "success": True,
            "equity": float(a.equity),
            "cash": float(a.cash),
            "buying_power": float(a.buying_power),
            "portfolio_value": float(a.portfolio_value),
            "currency": a.currency,
            "trading_blocked": a.trading_blocked,
            "pattern_day_trader": a.pattern_day_trader,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def get_positions() -> Dict[str, Any]:
    """Get all open positions."""
    try:
        client = _trading_client()
        positions = client.get_all_positions()
        result = []
        for p in positions:
            result.append({
                "symbol": p.symbol,
                "qty": float(p.qty),
                "side": str(p.side),
                "avg_entry": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
            })
        return {"success": True, "positions": result, "count": len(result)}
    except Exception as exc:
        return {"success": False, "positions": [], "error": str(exc)}


def get_price(symbol: str) -> Dict[str, Any]:
    """Get latest quote for a symbol."""
    try:
        from alpaca.data.requests import StockLatestQuoteRequest
        client = _data_client()
        quote = client.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=symbol)
        )
        q = quote[symbol]
        bid = float(q.bid_price)
        ask = float(q.ask_price)
        return {
            "success": True,
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "mid": round((bid + ask) / 2, 5),
            "bid_size": float(q.bid_size),
            "ask_size": float(q.ask_size),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def get_bars(
    symbol: str,
    timeframe: str = "1Hour",
    count: int = 60,
) -> Dict[str, Any]:
    """Get historical OHLCV bars.

    Timeframe: '1Min', '5Min', '15Min', '30Min', '1Hour', '4Hour', '1Day', '1Week'
    """
    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        tf_map = {
            "1Min": TimeFrame.Minute, "5Min": TimeFrame(5, "Min"),
            "15Min": TimeFrame(15, "Min"), "30Min": TimeFrame(30, "Min"),
            "1Hour": TimeFrame.Hour, "4Hour": TimeFrame(4, "Hour"),
            "1Day": TimeFrame.Day, "1Week": TimeFrame.Week,
            # Legacy eToro names
            "OneMinute": TimeFrame.Minute, "FiveMinutes": TimeFrame(5, "Min"),
            "OneHour": TimeFrame.Hour, "FourHours": TimeFrame(4, "Hour"),
            "OneDay": TimeFrame.Day, "OneWeek": TimeFrame.Week,
        }
        tf = tf_map.get(timeframe, TimeFrame.Hour)

        end = datetime.utcnow()
        # Estimate start based on count and timeframe
        if tf == TimeFrame.Minute:
            start = end - timedelta(minutes=count + 10)
        elif tf == TimeFrame.Hour:
            start = end - timedelta(hours=count + 10)
        elif tf == TimeFrame.Day:
            start = end - timedelta(days=count + 10)
        elif tf == TimeFrame.Week:
            start = end - timedelta(weeks=count + 2)
        else:
            start = end - timedelta(hours=count * 2)

        client = _data_client()
        bars_data = client.get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf,
                start=start,
                limit=count,
            )
        )
        bars = bars_data[symbol]
        candles = []
        for b in bars:
            candles.append({
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": float(b.volume),
                "time": b.timestamp.isoformat(),
            })
        return {
            "success": True,
            "symbol": symbol,
            "timeframe": timeframe,
            "candles": candles,
            "count": len(candles),
        }
    except Exception as exc:
        return {"success": False, "candles": [], "error": str(exc)}


def submit_order(
    symbol: str,
    side: str,
    notional: float = MAX_ORDER_USD,
) -> Dict[str, Any]:
    """Submit a market order by notional amount (USD).

    Enforces all risk limits before sending.
    """
    audit_id = f"ALP-{int(time.time())}-{symbol[:4].upper()}"

    # Kill switch
    if _is_halted():
        _log_audit({"id": audit_id, "action": "blocked", "reason": "HALT"})
        return {"success": False, "error": "ALPACA_HALT activo. Ejecución bloqueada."}

    # Risk: max order size
    if notional > MAX_ORDER_USD:
        _log_audit({"id": audit_id, "action": "blocked", "reason": f"notional ${notional} > ${MAX_ORDER_USD}"})
        return {"success": False, "error": f"Monto ${notional} excede límite ${MAX_ORDER_USD}"}

    # Risk: max positions
    pos = get_positions()
    if pos.get("success") and pos["count"] >= MAX_POSITIONS:
        _log_audit({"id": audit_id, "action": "blocked", "reason": f"positions {pos['count']} >= {MAX_POSITIONS}"})
        return {"success": False, "error": f"Máximo {MAX_POSITIONS} posiciones alcanzado ({pos['count']})"}

    # Risk: max exposure
    if pos.get("success"):
        total_exposure = sum(abs(p.get("market_value", 0)) for p in pos["positions"])
        if total_exposure + notional > MAX_EXPOSURE_USD:
            _log_audit({"id": audit_id, "action": "blocked", "reason": f"exposure ${total_exposure}+${notional} > ${MAX_EXPOSURE_USD}"})
            return {
                "success": False,
                "error": f"Exposición ${total_exposure:.0f} + ${notional:.0f} excede ${MAX_EXPOSURE_USD}",
            }

    # Pre-execution audit
    _log_audit({
        "id": audit_id, "action": "submit_intent",
        "symbol": symbol, "side": side, "notional": notional,
        "environment": "paper" if _is_paper() else "live",
    })

    try:
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        order_data = MarketOrderRequest(
            symbol=symbol,
            notional=notional,
            side=order_side,
            time_in_force=TimeInForce.DAY,
        )

        client = _trading_client()
        order = client.submit_order(order_data=order_data)

        result = {
            "success": True,
            "order_id": str(order.id),
            "symbol": symbol,
            "side": side,
            "notional": notional,
            "status": str(order.status),
            "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
            "environment": "paper" if _is_paper() else "live",
        }
        _log_audit({"id": audit_id, "action": "submit_result", **result})

        # Log to observation ledger
        try:
            from core.self_observation.observation_ledger import record
            record(
                domain="market",
                obs_type="alpaca_order",
                summary=f"Alpaca {side.upper()} {symbol} ${notional} — {order.status}",
                star_id=f"market:{symbol}",
                evidence={"order_id": str(order.id), "notional": notional},
            )
        except Exception:
            pass

        logger.info(
            "[ALPACA] ORDER %s | %s %s $%.0f | status=%s | id=%s",
            audit_id, side.upper(), symbol, notional, order.status, order.id,
        )
        return result

    except Exception as exc:
        _log_audit({"id": audit_id, "action": "submit_error", "error": str(exc)})
        logger.error("[ALPACA] ORDER FAILED %s: %s", audit_id, exc)
        return {"success": False, "error": str(exc)}


def close_position(symbol: str) -> Dict[str, Any]:
    """Close an open position by symbol."""
    audit_id = f"ALP-CLOSE-{int(time.time())}-{symbol[:4].upper()}"

    if _is_halted():
        return {"success": False, "error": "ALPACA_HALT activo."}

    _log_audit({"id": audit_id, "action": "close_intent", "symbol": symbol})

    try:
        client = _trading_client()
        order = client.close_position(symbol)

        result = {
            "success": True,
            "order_id": str(order.id) if hasattr(order, "id") else str(order),
            "symbol": symbol,
            "status": str(order.status) if hasattr(order, "status") else "submitted",
        }
        _log_audit({"id": audit_id, "action": "close_result", **result})

        try:
            from core.self_observation.observation_ledger import record
            record(
                domain="market",
                obs_type="alpaca_close",
                summary=f"Alpaca CLOSE {symbol}",
                star_id=f"market:{symbol}",
            )
        except Exception:
            pass

        logger.info("[ALPACA] CLOSE %s | %s", audit_id, symbol)
        return result

    except Exception as exc:
        _log_audit({"id": audit_id, "action": "close_error", "error": str(exc)})
        return {"success": False, "error": str(exc)}


def list_orders(status: str = "all") -> Dict[str, Any]:
    """List orders. Status: 'open', 'closed', 'all'."""
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        status_map = {
            "open": QueryOrderStatus.OPEN,
            "closed": QueryOrderStatus.CLOSED,
            "all": QueryOrderStatus.ALL,
        }
        client = _trading_client()
        orders = client.get_orders(
            filter=GetOrdersRequest(status=status_map.get(status, QueryOrderStatus.ALL))
        )

        result = []
        for o in orders:
            result.append({
                "order_id": str(o.id),
                "symbol": o.symbol,
                "side": str(o.side),
                "notional": float(o.notional) if o.notional else None,
                "qty": float(o.qty) if o.qty else None,
                "status": str(o.status),
                "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "filled_at": o.filled_at.isoformat() if o.filled_at else None,
            })
        return {"success": True, "orders": result, "count": len(result)}

    except Exception as exc:
        return {"success": False, "orders": [], "error": str(exc)}
