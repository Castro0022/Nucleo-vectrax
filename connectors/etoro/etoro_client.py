"""
Vectrax eToro Client — Authenticated REST API
==============================================
Wraps the eToro Public API v1 with:
  - Automatic auth headers (x-api-key, x-user-key, x-request-id)
  - Demo/real environment switching via ETORO_ENVIRONMENT
  - Exponential backoff on 429 (rate limit)
  - All operations logged

Read endpoints (no authorization gate):
  get_portfolio(), get_pnl(), get_price(), get_candles(), get_instrument_id()

Write endpoints (caller must verify creator authorization before calling):
  open_position(), close_position()
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from connectors.etoro import BASE_URL, validate_host

logger = logging.getLogger("vectrax.etoro.client")

REQUEST_TIMEOUT = 15
MAX_RETRIES     = 3
_HOST           = "public-api.etoro.com"


def _get_credentials() -> tuple[str, str]:
    """Load API keys from environment. Raises if missing."""
    api_key  = os.environ.get("ETORO_API_KEY", "").strip()
    user_key = os.environ.get("ETORO_USER_KEY", "").strip()
    if not api_key or not user_key:
        raise EnvironmentError(
            "ETORO_API_KEY y ETORO_USER_KEY deben estar configurados en .env"
        )
    return api_key, user_key


def _get_env_prefix() -> str:
    """Return 'demo' or '' for real, used in endpoint paths."""
    env = os.environ.get("ETORO_ENVIRONMENT", "demo").strip().lower()
    return "demo" if env != "real" else ""


def _request(
    method: str,
    endpoint: str,
    body: Optional[Dict] = None,
    params: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Execute an authenticated request to the eToro API.
    Returns: {"success": True, "data": ..., "latency_ms": ...}
          or {"success": False, "error": ..., "status": ...}
    """
    if not validate_host(_HOST):
        return {"success": False, "error": "Host not in allowlist"}

    try:
        api_key, user_key = _get_credentials()
    except EnvironmentError as e:
        return {"success": False, "error": str(e)}

    url = f"{BASE_URL}{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    headers = {
        "x-api-key":      api_key,
        "x-user-key":     user_key,
        "x-request-id":   str(uuid.uuid4()),
        "Content-Type":   "application/json",
        "Accept":         "application/json",
        "User-Agent":     "Vectrax/1.0",
    }

    data = json.dumps(body).encode("utf-8") if body else None
    delay = 1.0

    for attempt in range(1, MAX_RETRIES + 1):
        t0 = time.time()
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                body_bytes = resp.read()
                latency_ms = round((time.time() - t0) * 1000, 1)
                result = json.loads(body_bytes) if body_bytes else {}
                logger.info(
                    "[LEDGER] etoro %s %s — %dms — OK",
                    method, endpoint, latency_ms,
                )
                return {"success": True, "data": result, "latency_ms": latency_ms}

        except urllib.error.HTTPError as exc:
            latency_ms = round((time.time() - t0) * 1000, 1)
            error_body = exc.read().decode("utf-8", errors="replace")

            # Rate limit — exponential backoff
            if exc.code == 429 and attempt < MAX_RETRIES:
                retry_after = float(exc.headers.get("Retry-After", delay))
                logger.warning(
                    "[LEDGER] etoro 429 rate-limited — sleeping %.1fs (attempt %d/%d)",
                    retry_after, attempt, MAX_RETRIES,
                )
                time.sleep(retry_after)
                delay *= 2
                continue

            logger.error(
                "[LEDGER] etoro %s %s — %dms — HTTP %d: %s",
                method, endpoint, latency_ms, exc.code, error_body[:300],
            )
            return {
                "success": False,
                "error": f"HTTP {exc.code}",
                "details": error_body[:300],
                "status": exc.code,
                "latency_ms": latency_ms,
            }

        except Exception as exc:
            latency_ms = round((time.time() - t0) * 1000, 1)
            logger.error(
                "[LEDGER] etoro %s %s — %dms — ERROR: %s",
                method, endpoint, latency_ms, exc,
            )
            if attempt < MAX_RETRIES:
                time.sleep(delay)
                delay *= 2
                continue
            return {"success": False, "error": str(exc), "latency_ms": latency_ms}

    return {"success": False, "error": "Max retries exceeded"}


# ── Read Endpoints ───────────────────────────────────────────────────

def get_portfolio() -> Dict[str, Any]:
    """
    Fetch portfolio. Uses demo or real endpoint based on ETORO_ENVIRONMENT.
    Returns positions, credit, orders, pnl.
    """
    env = _get_env_prefix()
    if env == "demo":
        result = _request("GET", "/trading/info/demo/pnl")
    else:
        result = _request("GET", "/trading/info/real/pnl")

    if not result["success"]:
        return result

    portfolio = result["data"].get("clientPortfolio", {})
    positions = portfolio.get("positions", [])
    mirrors   = portfolio.get("mirrors", [])
    credit    = portfolio.get("credit", 0)
    orders    = portfolio.get("orders", [])

    # Collect all positions (direct + mirror)
    all_positions = list(positions)
    for mirror in mirrors:
        all_positions.extend(mirror.get("positions", []))

    # Calculate total unrealized PnL
    pnl_total = sum(
        p.get("unrealizedPnL", {}).get("pnL", 0)
        for p in all_positions
    )
    # Add closed mirror profits
    for mirror in mirrors:
        pnl_total += mirror.get("closedPositionsNetProfit", 0)

    # Total invested
    invested = sum(p.get("amount", 0) for p in all_positions)
    for mirror in mirrors:
        invested += mirror.get("availableAmount", 0)

    return {
        "success":    True,
        "environment": "demo" if env == "demo" else "real",
        "credit":     round(credit, 2),
        "invested":   round(invested, 2),
        "pnl":        round(pnl_total, 2),
        "equity":     round(credit + invested + pnl_total, 2),
        "positions":  all_positions,
        "n_positions": len(all_positions),
        "orders":     orders,
        "latency_ms": result["latency_ms"],
    }


def get_instrument_id(symbol: str) -> Optional[int]:
    """Resolve instrument ID from symbol (e.g. 'BTCUSD', 'AAPL')."""
    result = _request(
        "GET",
        "/market-data/search",
        params={
            "internalSymbolFull": symbol.upper(),
            "fields": "instrumentId,internalSymbolFull,displayname",
        },
    )
    if not result["success"]:
        logger.warning("etoro search failed for %s: %s", symbol, result.get("error"))
        return None

    items = result["data"].get("items", [])
    if not items:
        return None

    # Return exact match first
    for item in items:
        iid = item.get("instrumentId") or item.get("InstrumentID")
        sym = item.get("internalSymbolFull", "")
        if sym.upper() == symbol.upper() and iid:
            return int(iid)

    # Fallback: first result
    first = items[0]
    iid = first.get("instrumentId") or first.get("InstrumentID")
    return int(iid) if iid else None


def get_price(instrument_id: int) -> Dict[str, Any]:
    """Get current bid/ask for an instrument."""
    result = _request(
        "GET",
        "/market-data/instruments/rates",
        params={"instrumentIds": instrument_id},
    )
    if not result["success"]:
        return result

    rates = result["data"].get("rates", [])
    if not rates:
        return {"success": False, "error": "No rates returned"}

    r = rates[0]
    return {
        "success":       True,
        "instrument_id": instrument_id,
        "ask":           r.get("ask", 0),
        "bid":           r.get("bid", 0),
        "last":          r.get("lastExecution", 0),
        "mid":           round((r.get("ask", 0) + r.get("bid", 0)) / 2, 5),
        "latency_ms":    result["latency_ms"],
    }


# Interval name mapping: legacy names → eToro API names
_INTERVAL_MAP = {
    "Minute1": "OneMinute",   "OneMinute": "OneMinute",
    "Minute5": "FiveMinutes", "FiveMinutes": "FiveMinutes",
    "Minute15": "FifteenMinutes", "FifteenMinutes": "FifteenMinutes",
    "Minute30": "ThirtyMinutes",  "ThirtyMinutes": "ThirtyMinutes",
    "Hour1": "OneHour",      "OneHour": "OneHour",
    "Hour4": "FourHours",    "FourHours": "FourHours",
    "Day1": "OneDay",        "OneDay": "OneDay",
    "Week1": "OneWeek",      "OneWeek": "OneWeek",
}


def get_candles(
    instrument_id: int,
    interval: str = "OneHour",
    count: int = 100,
    direction: str = "desc",
) -> Dict[str, Any]:
    """
    Get OHLCV candles for an instrument.
    Supported intervals: OneMinute, FiveMinutes, FifteenMinutes,
        ThirtyMinutes, OneHour, FourHours, OneDay, OneWeek
    Legacy names (Hour1, Day1, etc.) are auto-mapped.
    """
    api_interval = _INTERVAL_MAP.get(interval, interval)
    result = _request(
        "GET",
        f"/market-data/instruments/{instrument_id}/history/candles/{direction}/{api_interval}/{count}",
    )
    if not result["success"]:
        return result

    # eToro response: {"interval": ..., "candles": [{"instrumentId": ..., "candles": [...OHLCV...]}]}
    # Double-nested: data.candles[0].candles contains the actual OHLCV bars.
    raw = result["data"]
    raw_candles = []
    if isinstance(raw, dict):
        outer = raw.get("candles", [])
        if isinstance(outer, list) and outer:
            first = outer[0]
            if isinstance(first, dict) and "candles" in first:
                raw_candles = first["candles"]  # inner candles = actual OHLCV
            else:
                raw_candles = outer
        else:
            raw_candles = outer
    elif isinstance(raw, list) and raw:
        first = raw[0]
        if isinstance(first, dict) and "candles" in first:
            raw_candles = first["candles"]
        else:
            raw_candles = raw

    candles = []
    for c in raw_candles:
        candles.append({
            "open":   c.get("open",   c.get("o", 0)),
            "high":   c.get("high",   c.get("h", 0)),
            "low":    c.get("low",    c.get("l", 0)),
            "close":  c.get("close",  c.get("c", 0)),
            "volume": c.get("volume", c.get("v", 0)),
            "time":   c.get("fromDate", c.get("time", "")),
        })

    return {
        "success":       True,
        "instrument_id": instrument_id,
        "interval":      api_interval,
        "candles":       candles,
        "count":         len(candles),
        "latency_ms":    result["latency_ms"],
    }


# ── Write Endpoints (caller must verify creator authorization) ───────

def open_position(
    instrument_id: int,
    amount: float,
    is_buy: bool,
    leverage: int = 1,
    stop_loss_rate: Optional[float] = None,
    take_profit_rate: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Open a market position by amount.
    CALLER IS RESPONSIBLE for creator authorization before calling this.
    Uses demo or real based on ETORO_ENVIRONMENT.
    """
    env = _get_env_prefix()
    path = (
        f"/trading/execution/{'demo/' if env == 'demo' else ''}"
        f"market-open-orders/by-amount"
    )

    body: Dict[str, Any] = {
        "InstrumentID": instrument_id,
        "IsBuy":        is_buy,
        "Leverage":     leverage,
        "Amount":       amount,
    }
    if stop_loss_rate is not None:
        body["StopLossRate"] = stop_loss_rate
    if take_profit_rate is not None:
        body["TakeProfitRate"] = take_profit_rate

    result = _request("POST", path, body=body)

    if result["success"]:
        order = result["data"].get("orderForOpen", result["data"])
        logger.info(
            "[LEDGER] etoro OPEN %s %s $%.2f lev=%d env=%s",
            "BUY" if is_buy else "SELL",
            instrument_id, amount, leverage,
            "demo" if env == "demo" else "real",
        )
        return {
            "success":      True,
            "order_id":     order.get("orderID"),
            "position_id":  order.get("positionID"),
            "instrument_id": instrument_id,
            "amount":       amount,
            "is_buy":       is_buy,
            "leverage":     leverage,
            "status":       order.get("statusID"),
            "open_time":    order.get("openDateTime"),
            "environment":  "demo" if env == "demo" else "real",
            "latency_ms":   result["latency_ms"],
        }
    return result


def close_position(position_id: str, instrument_id: int) -> Dict[str, Any]:
    """
    Close a position fully by position ID.
    CALLER IS RESPONSIBLE for creator authorization before calling this.
    """
    env = _get_env_prefix()
    path = (
        f"/trading/execution/{'demo/' if env == 'demo' else ''}"
        f"market-close-orders/positions/{position_id}"
    )

    result = _request("POST", path, body={"InstrumentId": instrument_id})

    if result["success"]:
        logger.info(
            "[LEDGER] etoro CLOSE position=%s instrument=%s env=%s",
            position_id, instrument_id,
            "demo" if env == "demo" else "real",
        )
        return {
            "success":     True,
            "position_id": position_id,
            "environment": "demo" if env == "demo" else "real",
            "latency_ms":  result["latency_ms"],
        }
    return result


def healthcheck() -> Dict[str, Any]:
    """Verify API connectivity and credentials."""
    result = _request(
        "GET",
        "/market-data/search",
        params={"internalSymbolFull": "BTCUSD", "fields": "instrumentId,displayname"},
    )
    if result["success"]:
        items = result["data"].get("items", [])
        return {
            "success":     True,
            "connected":   True,
            "environment": os.environ.get("ETORO_ENVIRONMENT", "demo"),
            "latency_ms":  result["latency_ms"],
            "test_result": f"{len(items)} instruments found for BTCUSD",
        }
    return {
        "success":   False,
        "connected": False,
        "error":     result.get("error", "Unknown"),
    }
