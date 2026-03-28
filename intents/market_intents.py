"""
Vectrax Market Data — Intent Handlers.

Maps natural language queries to market data functions.
Designed to integrate with the existing intent/router pipeline.

Intents:
  market_price      — "precio de bitcoin", "BTC price"
  market_snapshot    — "resumen del mercado", "market summary"
  market_trend       — "tendencia de BTC 1h"
  bitcoin_status     — "cómo está BTC"
  stock_status       — "cómo está NVDA hoy"
  watchlist_review   — "revisa mi watchlist"

Commands (internal):
  vx market status
  vx market test
  vx market watch
  vx market snapshot
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from services import market_router
from services.market_signals import detect_trend, detect_momentum, detect_breakout, full_analysis
from services.market_alerts import get_market_alerts
from connectors.market import CRYPTO_WATCHLIST, STOCK_WATCHLIST
from connectors.market.binance_stream import get_binance_stream

logger = logging.getLogger("vectrax.intents.market")

# ── Intent Detection Patterns ───────────────────────────────────────

# Standalone crypto ticker pattern ("btc", "bitcoin", "eth" as full message)
_STANDALONE_CRYPTO = re.compile(
    r"^\s*(?P<symbol>btc|bitcoin|ethereum|eth|bnb|sol|ada|dot|avax|matic|link|xrp)\s*[?]?\s*$",
    re.IGNORECASE,
)

MARKET_PATTERNS = {
    "market_price": re.compile(
        r"(?:"
        # "precio de btc", "price of bitcoin"
        r"(?:precio|price|cotizaci[oó]n|valor|cu[aá]nto (?:vale|cuesta|est[aá]))\s+"
        r"(?:de[l]?\s+)?(?:of\s+)?(?:el\s+)?(\w+)"
        # "btc precio", "bitcoin price" (reversed)
        r"|(\b(?:btc|bitcoin|eth|ethereum|bnb|sol|spy|qqq|aapl|tsla|nvda)\b)\s+"
        r"(?:precio|price|cotizaci[oó]n|valor)"
        # "a cuanto esta btc", "a cuanto esta el bitcoin"
        r"|a\s+cu[aá]nto\s+est[aá]\s+(?:el\s+)?(\w+)"
        r")",
        re.IGNORECASE,
    ),
    "bitcoin_status": re.compile(
        r"(?:"
        r"(?:c[oó]mo\s+(?:est[aá]|va)|status|estado)\s+(?:el\s+)?(?:de[l]?\s+)?(?:btc|bitcoin|eth|ethereum)"
        r"|(?:btc|bitcoin|eth|ethereum)\s+(?:hoy|now|ahora|status|today)"
        r")",
        re.IGNORECASE,
    ),
    "stock_status": re.compile(
        r"(?:c[oó]mo est[aá]|status|estado)\s+(?:de[l]?\s+)?(?:SPY|QQQ|AAPL|TSLA|NVDA|"
        r"apple|tesla|nvidia|spy|qqq)\b",
        re.IGNORECASE,
    ),
    "market_trend": re.compile(
        r"(?:tendencia|trend|direcci[oó]n)\s+(?:de[l]?\s+)?(\w+)\s*(\d+[mhd])?",
        re.IGNORECASE,
    ),
    "market_snapshot": re.compile(
        r"(?:resumen|snapshot|summary|overview|mercado)\s*(?:del?\s+)?(?:mercado)?",
        re.IGNORECASE,
    ),
    "watchlist_review": re.compile(
        r"(?:revisa|review|watchlist|lista)\s*(?:mi\s+)?(?:watchlist|lista)?",
        re.IGNORECASE,
    ),
}

# Symbol aliases for natural language
SYMBOL_ALIASES = {
    "bitcoin": "BTCUSDT", "btc": "BTCUSDT",
    "ethereum": "ETHUSDT", "eth": "ETHUSDT",
    "solana": "SOLUSDT", "sol": "SOLUSDT",
    "apple": "AAPL", "tesla": "TSLA", "nvidia": "NVDA",
    "spy": "SPY", "qqq": "QQQ",
}

TIMEFRAME_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "4h": "4h", "1d": "1d",
}


def detect_market_intent(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Detect if the user message is a market query.
    Returns (intent_name, params) or None.
    """
    text_lower = text.lower().strip()

    # VX commands
    if text_lower.startswith("vx market"):
        return _parse_vx_command(text_lower)

    # Standalone crypto ticker ("btc", "bitcoin", "eth", etc.)
    standalone = _STANDALONE_CRYPTO.match(text)
    if standalone:
        sym_raw = standalone.group("symbol").lower()
        symbol = SYMBOL_ALIASES.get(sym_raw, sym_raw.upper() + "USDT")
        return ("bitcoin_status" if sym_raw in ("btc", "bitcoin") else "market_price",
                {"symbol": symbol})

    # Pattern matching
    for intent, pattern in MARKET_PATTERNS.items():
        match = pattern.search(text)
        if match:
            params = _extract_params(intent, match, text)
            return (intent, params)

    return None


def _parse_vx_command(text: str) -> Tuple[str, Dict[str, Any]]:
    """Parse 'vx market ...' commands."""
    parts = text.split()
    if len(parts) < 3:
        return ("market_snapshot", {})

    cmd = parts[2]
    if cmd == "status":
        return ("vx_market_status", {})
    elif cmd == "test":
        return ("vx_market_test", {})
    elif cmd == "watch":
        return ("vx_market_watch", {})
    elif cmd == "snapshot":
        return ("market_snapshot", {})
    else:
        return ("market_snapshot", {})


def _extract_params(intent: str, match: re.Match, text: str) -> Dict[str, Any]:
    """Extract parameters from regex match."""
    params: Dict[str, Any] = {}

    if intent == "market_price":
        # Multiple capture groups: try each one (group 1, 2, 3)
        symbol_raw = None
        for i in range(1, match.lastindex + 1 if match.lastindex else 2):
            g = match.group(i)
            if g:
                symbol_raw = g.strip().lower()
                break
        if not symbol_raw:
            # Fallback: scan text for known aliases
            for alias in SYMBOL_ALIASES:
                if alias in text.lower():
                    symbol_raw = alias
                    break
            symbol_raw = symbol_raw or "btc"
        params["symbol"] = SYMBOL_ALIASES.get(symbol_raw, symbol_raw.upper())

    elif intent == "market_trend":
        symbol_raw = match.group(1).lower()
        params["symbol"] = SYMBOL_ALIASES.get(symbol_raw, symbol_raw.upper())
        tf = match.group(2) if match.lastindex and match.lastindex >= 2 else "1h"
        params["timeframe"] = TIMEFRAME_MAP.get(tf, "1h") if tf else "1h"

    elif intent in ("bitcoin_status", "stock_status"):
        # Extract symbol from text
        for alias, symbol in SYMBOL_ALIASES.items():
            if alias in text.lower():
                params["symbol"] = symbol
                break

    return params


# ── Intent Handlers ─────────────────────────────────────────────────

def handle_market_intent(intent: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Route a market intent to its handler."""
    handlers = {
        "market_price": _handle_price,
        "bitcoin_status": _handle_crypto_status,
        "stock_status": _handle_stock_status,
        "market_trend": _handle_trend,
        "market_snapshot": _handle_snapshot,
        "watchlist_review": _handle_watchlist,
        "vx_market_status": _handle_vx_status,
        "vx_market_test": _handle_vx_test,
        "vx_market_watch": _handle_vx_watch,
    }
    handler = handlers.get(intent)
    if not handler:
        return {"success": False, "error": f"Unknown market intent: {intent}"}
    return handler(params)


def _handle_price(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle: 'precio de bitcoin'"""
    symbol = params.get("symbol", "BTCUSDT")
    if market_router.is_crypto(symbol):
        data = market_router.get_crypto_ticker(symbol)
    else:
        data = market_router.get_stock_quote(symbol)

    if data.get("success"):
        return {
            "success": True,
            "intent": "market_price",
            "response": _format_price_response(data),
            "data": data,
        }
    return data


def _handle_crypto_status(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle: 'cómo está BTC'"""
    symbol = params.get("symbol", "BTCUSDT")
    analysis = full_analysis(symbol, "1h")
    ticker = market_router.get_crypto_ticker(symbol)

    response_parts = []
    sym_display = symbol.replace("USDT", "")

    if ticker.get("success"):
        price = ticker.get("price", 0)
        change = ticker.get("change_pct", 0)
        direction = "↑" if change >= 0 else "↓"
        response_parts.append(f"{sym_display}: ${price:,.2f} ({direction} {change:+.2f}%)")

    if analysis.get("trend", {}).get("trend"):
        t = analysis["trend"]
        response_parts.append(f"Tendencia 1h: {t['trend']} (fuerza: {t.get('strength', 0):.1%})")

    if analysis.get("momentum", {}).get("momentum"):
        m = analysis["momentum"]
        response_parts.append(f"Momentum: {m['momentum']} (ROC: {m.get('roc', 0):+.2f}%)")

    if analysis.get("breakout", {}).get("breakout") and analysis["breakout"]["breakout"] != "none":
        b = analysis["breakout"]
        response_parts.append(f"⚡ Breakout {b['breakout']} detectado")

    return {
        "success": True,
        "intent": "bitcoin_status",
        "response": "\n".join(response_parts) if response_parts else f"Sin datos disponibles para {sym_display}",
        "data": {"ticker": ticker, "analysis": analysis},
    }


def _handle_stock_status(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle: 'cómo está NVDA'"""
    symbol = params.get("symbol", "SPY")
    data = market_router.get_stock_quote(symbol)

    if data.get("success"):
        price = data.get("price", 0)
        change = data.get("change_pct", 0)
        direction = "↑" if change >= 0 else "↓"
        response = (
            f"{symbol}: ${price:,.2f} ({direction} {change:+.2f}%)\n"
            f"Rango: ${data.get('low', 0):,.2f} – ${data.get('high', 0):,.2f}\n"
            f"Volumen: {data.get('volume', 0):,}"
        )
        return {
            "success": True,
            "intent": "stock_status",
            "response": response,
            "data": data,
        }
    return data


def _handle_trend(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle: 'tendencia de BTC 1h'"""
    symbol = params.get("symbol", "BTCUSDT")
    timeframe = params.get("timeframe", "1h")
    trend = detect_trend(symbol, timeframe)

    if trend.get("success"):
        emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(trend["trend"], "⚪")
        response = (
            f"{emoji} {symbol.upper()} ({timeframe}): {trend['trend']}\n"
            f"Precio: ${trend['price']:,.2f}\n"
            f"SMA rápida: ${trend['sma_fast']:,.2f} | SMA lenta: ${trend['sma_slow']:,.2f}\n"
            f"Fuerza: {trend['strength']:.1%}"
        )
        return {
            "success": True,
            "intent": "market_trend",
            "response": response,
            "data": trend,
        }
    return trend


def _handle_snapshot(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle: 'resumen del mercado'"""
    snapshot = market_router.get_market_snapshot()
    if not snapshot.get("success"):
        return snapshot

    lines = ["📊 Resumen del mercado", ""]

    # Crypto
    lines.append("Crypto:")
    for c in snapshot.get("crypto", []):
        if "error" in c:
            lines.append(f"  {c.get('symbol', '?')}: no disponible")
        else:
            change = c.get("change_pct", 0)
            d = "↑" if change >= 0 else "↓"
            lines.append(f"  {c.get('symbol', '?')}: ${c.get('price', 0):,.2f} ({d} {change:+.2f}%)")

    # Stocks
    lines.append("")
    lines.append("Stocks:")
    for s in snapshot.get("stocks", []):
        if "error" in s:
            lines.append(f"  {s.get('symbol', '?')}: no disponible")
        else:
            change = s.get("change_pct", 0)
            d = "↑" if change >= 0 else "↓"
            lines.append(f"  {s.get('symbol', '?')}: ${s.get('price', 0):,.2f} ({d} {change:+.2f}%)")

    return {
        "success": True,
        "intent": "market_snapshot",
        "response": "\n".join(lines),
        "data": snapshot,
    }


def _handle_watchlist(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle: 'revisa mi watchlist'"""
    return _handle_snapshot(params)


def _handle_vx_status(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle: 'vx market status'"""
    status = market_router.market_status()
    alerts = get_market_alerts()
    status["alerts"] = alerts.stats
    return {"success": True, "intent": "vx_market_status", "data": status}


def _handle_vx_test(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle: 'vx market test' — quick smoke test of all sources."""
    results = {}

    # Test Binance REST
    from connectors.market import binance_rest
    results["binance_rest"] = binance_rest.healthcheck()

    # Test CoinGecko
    from connectors.market import coingecko_client
    results["coingecko"] = coingecko_client.healthcheck()

    # Test Alpha Vantage
    from connectors.market import alphavantage_client
    results["alphavantage"] = alphavantage_client.healthcheck()

    # Test a spot price fetch
    btc = market_router.get_crypto_spot("BTCUSDT")
    results["btc_spot"] = btc.get("success", False)
    results["btc_price"] = btc.get("price", 0) if btc.get("success") else None

    return {
        "success": True,
        "intent": "vx_market_test",
        "data": results,
    }


def _handle_vx_watch(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle: 'vx market watch' — start WebSocket stream."""
    stream = get_binance_stream()
    if stream.is_running:
        return {
            "success": True,
            "intent": "vx_market_watch",
            "response": "Stream ya activo",
            "data": {"running": True, "symbols": stream.symbols},
        }

    alerts = get_market_alerts()
    started = stream.start(on_tick=alerts.on_tick)
    return {
        "success": started,
        "intent": "vx_market_watch",
        "response": "Stream iniciado" if started else "Error al iniciar stream",
        "data": {"running": started, "symbols": stream.symbols},
    }


# ── Helpers ─────────────────────────────────────────────────────────

def _format_price_response(data: Dict[str, Any]) -> str:
    """Format a price result into a user-friendly string."""
    symbol = data.get("symbol", "?")
    price = data.get("price", 0)
    change = data.get("change_pct", 0)
    source = data.get("source", "")

    direction = "↑" if change >= 0 else "↓"
    parts = [f"{symbol}: ${price:,.2f}"]
    if change:
        parts.append(f"({direction} {change:+.2f}%)")

    high = data.get("high", 0)
    low = data.get("low", 0)
    if high and low:
        parts.append(f"\nRango 24h: ${low:,.2f} – ${high:,.2f}")

    volume = data.get("volume", 0)
    if volume:
        parts.append(f"\nVolumen: {volume:,.0f}")

    return " ".join(parts)
