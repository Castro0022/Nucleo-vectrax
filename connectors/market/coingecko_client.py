"""
Vectrax Market Data — CoinGecko Client (fallback / cross-validation).

Uses CoinGecko Demo API (free tier).
API key is optional for demo tier (set COINGECKO_API_KEY in env).
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
from typing import Any, Dict, Optional

from connectors.market import validate_host

logger = logging.getLogger("vectrax.market.coingecko")

BASE_URL = "https://api.coingecko.com/api/v3"
HOST = "api.coingecko.com"
REQUEST_TIMEOUT = 15

# CoinGecko uses coin IDs, not ticker symbols
SYMBOL_TO_ID = {
    "BTC": "bitcoin",
    "BTCUSDT": "bitcoin",
    "ETH": "ethereum",
    "ETHUSDT": "ethereum",
    "SOL": "solana",
    "ADA": "cardano",
    "DOT": "polkadot",
    "AVAX": "avalanche-2",
    "MATIC": "matic-network",
    "LINK": "chainlink",
    "BNB": "binancecoin",
    "XRP": "ripple",
}


def _get_api_key() -> Optional[str]:
    return os.environ.get("COINGECKO_API_KEY")


def _get(endpoint: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Execute a GET request against CoinGecko API with ledger logging."""
    if not validate_host(HOST):
        return {"success": False, "error": "Host blocked by governance"}

    url = f"{BASE_URL}{endpoint}"
    if params is None:
        params = {}

    api_key = _get_api_key()
    if api_key:
        params["x_cg_demo_api_key"] = api_key

    if params:
        url += "?" + urllib.parse.urlencode(params)

    t0 = time.time()
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "Vectrax/1.0")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
            latency_ms = (time.time() - t0) * 1000
            data = json.loads(body)
            logger.info(
                "[LEDGER] coingecko GET %s — %dms — OK",
                endpoint, latency_ms,
            )
            return {"success": True, "data": data, "latency_ms": round(latency_ms, 1)}
    except urllib.error.HTTPError as exc:
        latency_ms = (time.time() - t0) * 1000
        error_body = exc.read().decode("utf-8", errors="replace")
        logger.error(
            "[LEDGER] coingecko GET %s — %dms — HTTP %d: %s",
            endpoint, latency_ms, exc.code, error_body[:200],
        )
        return {
            "success": False,
            "error": f"HTTP {exc.code}",
            "details": error_body[:200],
            "latency_ms": round(latency_ms, 1),
        }
    except Exception as exc:
        latency_ms = (time.time() - t0) * 1000
        logger.error(
            "[LEDGER] coingecko GET %s — %dms — ERROR: %s",
            endpoint, latency_ms, exc,
        )
        return {"success": False, "error": str(exc), "latency_ms": round(latency_ms, 1)}


def _resolve_id(symbol: str) -> str:
    """Convert a ticker symbol to CoinGecko coin ID."""
    return SYMBOL_TO_ID.get(symbol.upper().replace("USDT", ""), symbol.lower())


# ── Public Functions ────────────────────────────────────────────────

def get_spot_price(symbol: str = "BTCUSDT", vs_currency: str = "usd") -> Dict[str, Any]:
    """Get current spot price from CoinGecko."""
    coin_id = _resolve_id(symbol)
    result = _get("/simple/price", {
        "ids": coin_id,
        "vs_currencies": vs_currency,
        "include_24hr_change": "true",
        "include_24hr_vol": "true",
        "include_market_cap": "true",
    })
    if result["success"]:
        coin_data = result["data"].get(coin_id, {})
        return {
            "success": True,
            "symbol": symbol.upper(),
            "price": coin_data.get(vs_currency, 0),
            "change_pct_24h": coin_data.get(f"{vs_currency}_24h_change", 0),
            "volume_24h": coin_data.get(f"{vs_currency}_24h_vol", 0),
            "market_cap": coin_data.get(f"{vs_currency}_market_cap", 0),
            "source": "coingecko",
            "latency_ms": result["latency_ms"],
        }
    return result


def get_coin_details(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    """Get detailed coin information."""
    coin_id = _resolve_id(symbol)
    result = _get(f"/coins/{coin_id}", {
        "localization": "false",
        "tickers": "false",
        "community_data": "false",
        "developer_data": "false",
        "sparkline": "false",
    })
    if result["success"]:
        d = result["data"]
        market = d.get("market_data", {})
        return {
            "success": True,
            "symbol": symbol.upper(),
            "name": d.get("name", ""),
            "price": market.get("current_price", {}).get("usd", 0),
            "market_cap": market.get("market_cap", {}).get("usd", 0),
            "market_cap_rank": market.get("market_cap_rank", 0),
            "volume_24h": market.get("total_volume", {}).get("usd", 0),
            "high_24h": market.get("high_24h", {}).get("usd", 0),
            "low_24h": market.get("low_24h", {}).get("usd", 0),
            "change_pct_24h": market.get("price_change_percentage_24h", 0),
            "change_pct_7d": market.get("price_change_percentage_7d", 0),
            "ath": market.get("ath", {}).get("usd", 0),
            "circulating_supply": market.get("circulating_supply", 0),
            "total_supply": market.get("total_supply", 0),
            "source": "coingecko",
            "latency_ms": result["latency_ms"],
        }
    return result


def get_market_overview(limit: int = 10) -> Dict[str, Any]:
    """Get top coins by market cap."""
    result = _get("/coins/markets", {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": str(limit),
        "page": "1",
        "sparkline": "false",
        "price_change_percentage": "24h,7d",
    })
    if result["success"]:
        coins = []
        for c in result["data"]:
            coins.append({
                "symbol": c.get("symbol", "").upper(),
                "name": c.get("name", ""),
                "price": c.get("current_price", 0),
                "market_cap": c.get("market_cap", 0),
                "rank": c.get("market_cap_rank", 0),
                "change_pct_24h": c.get("price_change_percentage_24h", 0),
                "volume_24h": c.get("total_volume", 0),
            })
        return {
            "success": True,
            "coins": coins,
            "count": len(coins),
            "source": "coingecko",
            "latency_ms": result["latency_ms"],
        }
    return result


def healthcheck() -> bool:
    """Ping CoinGecko API."""
    result = _get("/ping")
    return result.get("success", False)
