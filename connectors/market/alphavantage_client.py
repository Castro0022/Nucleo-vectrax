"""
Vectrax Market Data — Alpha Vantage Client (stocks / ETFs / indices).

Free-tier: 25 requests/day. Set ALPHAVANTAGE_API_KEY in env.
Falls back gracefully when key is missing or rate-limited.
All requests are GET-only (read-only mode).
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from connectors.market import validate_host

logger = logging.getLogger("vectrax.market.alphavantage")

BASE_URL = "https://www.alphavantage.co/query"
HOST = "www.alphavantage.co"
REQUEST_TIMEOUT = 15


def _get_api_key() -> Optional[str]:
    return os.environ.get("ALPHAVANTAGE_API_KEY")


def _get(params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a GET request against Alpha Vantage with ledger logging."""
    if not validate_host(HOST):
        return {"success": False, "error": "Host blocked by governance"}

    api_key = _get_api_key()
    if not api_key:
        return {
            "success": False,
            "error": "ALPHAVANTAGE_API_KEY not set. Add it to .env",
            "fallback": True,
        }

    params["apikey"] = api_key
    url = BASE_URL + "?" + urllib.parse.urlencode(params)

    t0 = time.time()
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "Vectrax/1.0")
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
            latency_ms = (time.time() - t0) * 1000
            data = json.loads(body)

            # Alpha Vantage returns errors in JSON body
            if "Error Message" in data:
                logger.error(
                    "[LEDGER] alphavantage — %dms — ERROR: %s",
                    latency_ms, data["Error Message"],
                )
                return {
                    "success": False,
                    "error": data["Error Message"],
                    "latency_ms": round(latency_ms, 1),
                }
            if "Note" in data:
                # Rate limit message
                logger.warning(
                    "[LEDGER] alphavantage — %dms — RATE LIMITED: %s",
                    latency_ms, data["Note"][:100],
                )
                return {
                    "success": False,
                    "error": "rate_limited",
                    "details": data["Note"][:100],
                    "latency_ms": round(latency_ms, 1),
                }

            logger.info(
                "[LEDGER] alphavantage %s — %dms — OK",
                params.get("function", "?"), latency_ms,
            )
            return {"success": True, "data": data, "latency_ms": round(latency_ms, 1)}
    except urllib.error.HTTPError as exc:
        latency_ms = (time.time() - t0) * 1000
        error_body = exc.read().decode("utf-8", errors="replace")
        logger.error(
            "[LEDGER] alphavantage — %dms — HTTP %d: %s",
            latency_ms, exc.code, error_body[:200],
        )
        return {
            "success": False,
            "error": f"HTTP {exc.code}",
            "details": error_body[:200],
            "latency_ms": round(latency_ms, 1),
        }
    except Exception as exc:
        latency_ms = (time.time() - t0) * 1000
        logger.error("[LEDGER] alphavantage — %dms — ERROR: %s", latency_ms, exc)
        return {"success": False, "error": str(exc), "latency_ms": round(latency_ms, 1)}


# ── Public Functions ────────────────────────────────────────────────

def get_stock_quote(symbol: str = "SPY") -> Dict[str, Any]:
    """Get real-time (15min delayed on free tier) stock/ETF quote."""
    result = _get({
        "function": "GLOBAL_QUOTE",
        "symbol": symbol.upper(),
    })
    if result["success"]:
        quote = result["data"].get("Global Quote", {})
        if not quote:
            return {"success": False, "error": f"No quote data for {symbol}"}
        return {
            "success": True,
            "symbol": quote.get("01. symbol", symbol.upper()),
            "price": float(quote.get("05. price", 0)),
            "open": float(quote.get("02. open", 0)),
            "high": float(quote.get("03. high", 0)),
            "low": float(quote.get("04. low", 0)),
            "volume": int(quote.get("06. volume", 0)),
            "previous_close": float(quote.get("08. previous close", 0)),
            "change": float(quote.get("09. change", 0)),
            "change_pct": float(quote.get("10. change percent", "0").replace("%", "")),
            "latest_day": quote.get("07. latest trading day", ""),
            "source": "alphavantage",
            "latency_ms": result["latency_ms"],
        }
    return result


def get_daily_ohlcv(symbol: str = "SPY", outputsize: str = "compact") -> Dict[str, Any]:
    """
    Get daily OHLCV for a stock/ETF.
    outputsize: 'compact' (100 days) or 'full' (20+ years).
    """
    result = _get({
        "function": "TIME_SERIES_DAILY",
        "symbol": symbol.upper(),
        "outputsize": outputsize,
    })
    if result["success"]:
        time_series = result["data"].get("Time Series (Daily)", {})
        candles = []
        for date_str, values in sorted(time_series.items(), reverse=True):
            candles.append({
                "date": date_str,
                "open": float(values.get("1. open", 0)),
                "high": float(values.get("2. high", 0)),
                "low": float(values.get("3. low", 0)),
                "close": float(values.get("4. close", 0)),
                "volume": int(values.get("5. volume", 0)),
            })
        return {
            "success": True,
            "symbol": symbol.upper(),
            "interval": "daily",
            "candles": candles,
            "count": len(candles),
            "source": "alphavantage",
            "latency_ms": result["latency_ms"],
        }
    return result


def get_intraday_ohlcv(
    symbol: str = "SPY",
    interval: str = "15min",
    outputsize: str = "compact",
) -> Dict[str, Any]:
    """
    Get intraday OHLCV for a stock/ETF.
    interval: 1min, 5min, 15min, 30min, 60min
    """
    result = _get({
        "function": "TIME_SERIES_INTRADAY",
        "symbol": symbol.upper(),
        "interval": interval,
        "outputsize": outputsize,
    })
    if result["success"]:
        series_key = f"Time Series ({interval})"
        time_series = result["data"].get(series_key, {})
        candles = []
        for ts, values in sorted(time_series.items(), reverse=True):
            candles.append({
                "timestamp": ts,
                "open": float(values.get("1. open", 0)),
                "high": float(values.get("2. high", 0)),
                "low": float(values.get("3. low", 0)),
                "close": float(values.get("4. close", 0)),
                "volume": int(values.get("5. volume", 0)),
            })
        return {
            "success": True,
            "symbol": symbol.upper(),
            "interval": interval,
            "candles": candles,
            "count": len(candles),
            "source": "alphavantage",
            "latency_ms": result["latency_ms"],
        }
    return result


def healthcheck() -> bool:
    """Check if Alpha Vantage is available (requires API key)."""
    if not _get_api_key():
        return False
    result = get_stock_quote("IBM")  # IBM is the AV test ticker
    return result.get("success", False)
