"""
Vectrax Market Data — Binance REST (public endpoints only).

Endpoints used:
  GET /api/v3/ticker/price     — spot price
  GET /api/v3/ticker/24hr      — 24h ticker stats
  GET /api/v3/klines           — OHLCV candlesticks

No API key required for public market data.
All requests are GET-only (read-only mode).
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from connectors.market import ALLOWLIST, validate_host

logger = logging.getLogger("vectrax.market.binance_rest")

# Primary: Binance international. Fallback: Binance US (avoids HTTP 451
# geo-block on US datacenter IPs like Vultr).
_ENDPOINTS = [
    ("https://api.binance.com", "api.binance.com"),
    ("https://api.binance.us", "api.binance.us"),
]
REQUEST_TIMEOUT = 10
# Cache which endpoint works to avoid repeated failures
_working_endpoint_idx = 0


def _get(endpoint: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Execute a GET request against Binance API with automatic fallback.

    Tries the primary endpoint first. On HTTP 451 (geo-blocked) or
    connection error, falls back to the alternative endpoint.
    """
    global _working_endpoint_idx

    # Try the last-known working endpoint first, then the other
    order = [_working_endpoint_idx, 1 - _working_endpoint_idx]

    for idx in order:
        base_url, host = _ENDPOINTS[idx]
        if not validate_host(host):
            continue

        url = f"{base_url}{endpoint}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        t0 = time.time()
        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", "Vectrax/1.0")
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                body = resp.read().decode("utf-8")
                latency_ms = (time.time() - t0) * 1000
                data = json.loads(body)
                logger.info(
                    "[LEDGER] binance_rest GET %s%s — %dms — OK",
                    host.split(".")[-2], endpoint, latency_ms,
                )
                _working_endpoint_idx = idx
                return {"success": True, "data": data, "latency_ms": round(latency_ms, 1)}
        except urllib.error.HTTPError as exc:
            latency_ms = (time.time() - t0) * 1000
            error_body = exc.read().decode("utf-8", errors="replace")
            # HTTP 451 = geo-blocked — try next endpoint
            if exc.code == 451 and idx == order[0] and len(order) > 1:
                logger.info(
                    "[LEDGER] binance_rest %s HTTP 451 — falling back to %s",
                    host, _ENDPOINTS[order[1]][1],
                )
                continue
            logger.error(
                "[LEDGER] binance_rest GET %s%s — %dms — HTTP %d: %s",
                host, endpoint, latency_ms, exc.code, error_body[:200],
            )
            return {"success": False, "error": f"HTTP {exc.code}", "details": error_body[:200], "latency_ms": round(latency_ms, 1)}
        except Exception as exc:
            latency_ms = (time.time() - t0) * 1000
            # Connection error — try next endpoint
            if idx == order[0] and len(order) > 1:
                logger.info(
                    "[LEDGER] binance_rest %s failed (%s) — falling back to %s",
                    host, exc, _ENDPOINTS[order[1]][1],
                )
                continue
            logger.error(
                "[LEDGER] binance_rest GET %s%s — %dms — ERROR: %s",
                host, endpoint, latency_ms, exc,
            )
            return {"success": False, "error": str(exc), "latency_ms": round(latency_ms, 1)}

    return {"success": False, "error": "All Binance endpoints failed"}


# ── Public Functions ────────────────────────────────────────────────

def get_spot_price(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    """Get current spot price for a symbol."""
    result = _get("/api/v3/ticker/price", {"symbol": symbol.upper()})
    if result["success"]:
        raw = result["data"]
        return {
            "success": True,
            "symbol": raw.get("symbol", symbol.upper()),
            "price": float(raw.get("price", 0)),
            "source": "binance",
            "latency_ms": result["latency_ms"],
        }
    return result


def get_ticker_24h(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    """Get 24h ticker statistics."""
    result = _get("/api/v3/ticker/24hr", {"symbol": symbol.upper()})
    if result["success"]:
        d = result["data"]
        return {
            "success": True,
            "symbol": d.get("symbol", symbol.upper()),
            "price": float(d.get("lastPrice", 0)),
            "open": float(d.get("openPrice", 0)),
            "high": float(d.get("highPrice", 0)),
            "low": float(d.get("lowPrice", 0)),
            "volume": float(d.get("volume", 0)),
            "quote_volume": float(d.get("quoteVolume", 0)),
            "change_pct": float(d.get("priceChangePercent", 0)),
            "trades": int(d.get("count", 0)),
            "source": "binance",
            "latency_ms": result["latency_ms"],
        }
    return result


def get_ohlcv(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    limit: int = 100,
) -> Dict[str, Any]:
    """
    Get OHLCV candlestick data.

    Intervals: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M
    """
    result = _get("/api/v3/klines", {
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": limit,
    })
    if result["success"]:
        candles = []
        for c in result["data"]:
            candles.append({
                "open_time": c[0],
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
                "close_time": c[6],
            })
        return {
            "success": True,
            "symbol": symbol.upper(),
            "interval": interval,
            "candles": candles,
            "count": len(candles),
            "source": "binance",
            "latency_ms": result["latency_ms"],
        }
    return result


def healthcheck() -> bool:
    """Quick ping to verify Binance API is reachable."""
    result = _get("/api/v3/ping")
    return result.get("success", False)
