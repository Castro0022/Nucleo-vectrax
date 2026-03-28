"""
Vectrax Market Data — Market Router Service.

Unified entry point for all market data queries.
Handles source selection, fallback between providers,
and cache integration. Read-only, no trading.

Functions:
  get_crypto_spot(symbol)        — spot price (Binance → CoinGecko)
  get_stock_quote(symbol)        — stock quote (Alpha Vantage)
  get_ohlcv(symbol, timeframe)   — OHLCV candles
  get_market_snapshot()          — full watchlist overview
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from connectors.market import CRYPTO_WATCHLIST, STOCK_WATCHLIST
from connectors.market import binance_rest, coingecko_client, alphavantage_client
from connectors.market.binance_stream import get_binance_stream
from services.market_cache import (
    get_market_cache,
    TTL_SPOT,
    TTL_TICKER,
    TTL_OHLCV,
    TTL_QUOTE,
    TTL_SNAPSHOT,
)

logger = logging.getLogger("vectrax.market.router")

# ── Symbol Classification ───────────────────────────────────────────

CRYPTO_SUFFIXES = ("USDT", "BUSD", "BTC", "ETH", "USDC")
KNOWN_CRYPTO = {"BTC", "ETH", "SOL", "ADA", "DOT", "AVAX", "MATIC", "LINK", "BNB", "XRP"}


def is_crypto(symbol: str) -> bool:
    """Determine if a symbol is crypto or stock."""
    s = symbol.upper()
    if any(s.endswith(suffix) for suffix in CRYPTO_SUFFIXES):
        return True
    if s in KNOWN_CRYPTO:
        return True
    return False


def normalize_crypto_symbol(symbol: str) -> str:
    """Ensure crypto symbol ends with USDT for Binance."""
    s = symbol.upper()
    if s in KNOWN_CRYPTO:
        return f"{s}USDT"
    return s


# ── Core Router Functions ───────────────────────────────────────────

def get_crypto_spot(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    """
    Get crypto spot price with fallback chain:
    1. WebSocket stream (if running)
    2. Cache
    3. Binance REST
    4. CoinGecko (fallback)
    """
    symbol = normalize_crypto_symbol(symbol)
    cache = get_market_cache()
    cache_key = cache.spot_key(symbol)

    # 1. Try WebSocket stream first (fastest, real-time)
    stream = get_binance_stream()
    if stream.is_running:
        tick = stream.get_latest(symbol.lower())
        if tick:
            result = {
                "success": True,
                "symbol": tick["symbol"],
                "price": tick["price"],
                "high": tick.get("high", 0),
                "low": tick.get("low", 0),
                "volume": tick.get("volume", 0),
                "source": "binance_stream",
                "realtime": True,
            }
            cache.set(cache_key, result, TTL_SPOT, "binance_stream")
            return result

    # 2. Try cache
    cached = cache.get(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    # 3. Binance REST
    result = binance_rest.get_spot_price(symbol)
    if result.get("success"):
        cache.set(cache_key, result, TTL_SPOT, "binance_rest")
        return result

    # 4. CoinGecko fallback
    logger.info("[FALLBACK] Binance failed for %s, trying CoinGecko", symbol)
    result = coingecko_client.get_spot_price(symbol)
    if result.get("success"):
        cache.set(cache_key, result, TTL_SPOT, "coingecko")
        return result

    return {"success": False, "error": f"All sources failed for {symbol}", "symbol": symbol}


def get_crypto_ticker(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    """Get full 24h ticker with fallback."""
    symbol = normalize_crypto_symbol(symbol)
    cache = get_market_cache()
    cache_key = cache.ticker_key(symbol)

    cached = cache.get(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    result = binance_rest.get_ticker_24h(symbol)
    if result.get("success"):
        cache.set(cache_key, result, TTL_TICKER, "binance")
        return result

    # Fallback to CoinGecko
    result = coingecko_client.get_spot_price(symbol)
    if result.get("success"):
        cache.set(cache_key, result, TTL_TICKER, "coingecko")
        return result

    return {"success": False, "error": f"Ticker unavailable for {symbol}"}


def get_stock_quote(symbol: str = "SPY") -> Dict[str, Any]:
    """Get stock/ETF quote from Alpha Vantage (cached)."""
    cache = get_market_cache()
    cache_key = cache.quote_key(symbol)

    cached = cache.get(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    result = alphavantage_client.get_stock_quote(symbol)
    if result.get("success"):
        cache.set(cache_key, result, TTL_QUOTE, "alphavantage")
        return result

    return result


def get_ohlcv(symbol: str, timeframe: str = "1h", limit: int = 100) -> Dict[str, Any]:
    """
    Get OHLCV candles for any symbol.
    Routes to Binance (crypto) or Alpha Vantage (stocks).
    """
    cache = get_market_cache()
    cache_key = cache.ohlcv_key(symbol, timeframe)

    cached = cache.get(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    if is_crypto(symbol):
        sym = normalize_crypto_symbol(symbol)
        result = binance_rest.get_ohlcv(sym, timeframe, limit)
    else:
        # Map crypto-style timeframes to Alpha Vantage format
        av_interval_map = {
            "1m": "1min", "5m": "5min", "15m": "15min",
            "30m": "30min", "1h": "60min", "60m": "60min",
            "1d": "daily", "daily": "daily",
        }
        av_interval = av_interval_map.get(timeframe, "daily")
        if av_interval == "daily":
            result = alphavantage_client.get_daily_ohlcv(symbol)
        else:
            result = alphavantage_client.get_intraday_ohlcv(symbol, av_interval)

    if result.get("success"):
        cache.set(cache_key, result, TTL_OHLCV, result.get("source", ""))

    return result


def get_market_snapshot() -> Dict[str, Any]:
    """
    Full snapshot of the watchlist: all crypto + stocks in one call.
    """
    cache = get_market_cache()
    cached = cache.get("snapshot:full")
    if cached:
        cached["from_cache"] = True
        return cached

    t0 = time.time()
    crypto_data = []
    stock_data = []

    # Crypto from Binance
    for sym in CRYPTO_WATCHLIST:
        result = get_crypto_ticker(sym)
        if result.get("success"):
            crypto_data.append(result)
        else:
            crypto_data.append({"symbol": sym, "error": result.get("error", "unavailable")})

    # Stocks from Alpha Vantage
    for sym in STOCK_WATCHLIST:
        result = get_stock_quote(sym)
        if result.get("success"):
            stock_data.append(result)
        else:
            stock_data.append({"symbol": sym, "error": result.get("error", "unavailable")})

    snapshot = {
        "success": True,
        "timestamp": time.time(),
        "crypto": crypto_data,
        "stocks": stock_data,
        "total_latency_ms": round((time.time() - t0) * 1000, 1),
    }

    cache.set("snapshot:full", snapshot, TTL_SNAPSHOT, "router")
    return snapshot


# ── Status & Health ─────────────────────────────────────────────────

def market_status() -> Dict[str, Any]:
    """Return health status of all market data sources."""
    stream = get_binance_stream()
    cache = get_market_cache()

    return {
        "sources": {
            "binance_rest": binance_rest.healthcheck(),
            "binance_stream": stream.is_running,
            "coingecko": coingecko_client.healthcheck(),
            "alphavantage": alphavantage_client.healthcheck(),
        },
        "cache": cache.stats,
        "watchlist": {
            "crypto": CRYPTO_WATCHLIST,
            "stocks": STOCK_WATCHLIST,
        },
        "mode": "read_only",
        "domain": "market_data_authorized",
    }
