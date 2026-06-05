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

# All known crypto tickers (used in standalone + embedded patterns)
_CRYPTO_TICKERS = (
    r"btc|bitcoin|ethereum|eth|bnb|binancecoin|sol|solana|ada|cardano"
    r"|dot|polkadot|avax|avalanche|matic|polygon|link|chainlink|xrp|ripple"
    r"|doge|dogecoin|shib|shibainu|ltc|litecoin|uni|uniswap|atom|cosmos"
    r"|near|algo|algorand|ftm|fantom|sand|mana|axs|aave|crv|curve"
)

# All known stock tickers
_STOCK_TICKERS = (
    r"aapl|apple|tsla|tesla|nvda|nvidia|spy|qqq|msft|microsoft|amzn|amazon"
    r"|goog|google|meta|facebook|baba|alibaba|nflx|netflix|dis|disney"
    r"|jpm|jpmorgan|gs|goldmansachs|v|visa|ma|mastercard|ko|cocacola"
)

_ALL_TICKERS = f"(?:{_CRYPTO_TICKERS}|{_STOCK_TICKERS})"

# Standalone crypto ticker pattern ("btc", "bitcoin", "eth" as full message)
_STANDALONE_CRYPTO = re.compile(
    rf"^\s*(?P<symbol>{_CRYPTO_TICKERS})\s*[?]?\s*$",
    re.IGNORECASE,
)

# Price/status query verbs — all supported languages
_STATUS_VERBS = (
    # Spanish
    r"c[oó]mo\s+(?:est[aá]|va|anda|qued[oó])|cu[aá]nto\s+(?:vale|cuesta|est[aá])"
    r"|d[ií]me\s+(?:el\s+)?(?:precio|estado|valor)|a\s+cu[aá]nto\s+est[aá]"
    r"|precio\s+(?:de[l]?\s+)?|cotizaci[oó]n|qu[eé]\s+tal\s+(?:est[aá]|va)"
    # English
    r"|how\s+is|how(?:'s|\s+is)\s+(?:the\s+)?|what(?:'s|\s+is)\s+(?:the\s+)?"
    r"|price\s+of|current\s+price|tell\s+me\s+(?:the\s+)?price"
    r"|check\s+|show\s+(?:me\s+)?(?:the\s+)?"
    # French
    r"|comment\s+(?:va|est)|quel\s+(?:est\s+le\s+)?prix|c'est\s+quoi\s+le\s+prix"
    r"|prix\s+(?:du?\s+)?|combien\s+(?:vaut|co.te)"
    # German
    r"|wie\s+(?:steht|ist|l.uft|viel\s+kostet)|preis\s+(?:von\s+)?|kurs\s+(?:von\s+)?"
    r"|was\s+(?:kostet|ist\s+der\s+kurs)"
    # Italian
    r"|come\s+(?:va|sta)|quanto\s+(?:vale|costa)|prezzo\s+(?:di\s+)?|che\s+prezzo"
    # Portuguese
    r"|como\s+est[aá]|quanto\s+(?:vale|custa|est[aá])|pre[cç]o\s+(?:do?\s+)?"
    r"|me\s+fala\s+(?:o\s+)?"
    # Dutch
    r"|hoe\s+(?:staat|is)|wat\s+is\s+de\s+koers|koers\s+(?:van\s+)?"
)

MARKET_PATTERNS = {
    # Natural language: "Dime cómo está el BTC", "How is bitcoin?", "Wie steht ETH?"
    "bitcoin_status": re.compile(
        rf"(?:"
        rf"(?:{_STATUS_VERBS})\s*(?:el\s+|the\s+|der\s+|le\s+|il\s+|o\s+)?(?:{_CRYPTO_TICKERS})"
        rf"|(?:{_CRYPTO_TICKERS})\s+(?:hoy|today|now|ahora|aktuell|maintenant|oggi|agora|koers|status|price|precio|preis|prix|prezzo|pre[cç]o|cours)"
        rf"|dime\s+(?:como|cómo)\s+(?:está|esta)\s+(?:el\s+)?(?:{_CRYPTO_TICKERS})"
        rf")",
        re.IGNORECASE,
    ),
    # IMPORTANT: market_snapshot MUST be checked BEFORE market_price.
    # Otherwise "how is the market" matches MA (Mastercard) via market_price.
    "market_snapshot": re.compile(
        r"(?:"
        r"(?:resumen|snapshot|summary|overview|[uü]bersicht|r[eé]sum[eé]|riepilogo|resumo)\s*(?:del?\s+|du\s+|des?\s+|di\s+|do\s+)?(?:mercado|market|markt|march[eé]|mercato)?"
        r"|(?:h[aá]blame|cu[eé]ntame|dime|tell\s+me|talk\s+to\s+me)\s+(?:del?\s+|about\s+the\s+|about\s+)?(?:mercado|market)"
        r"|(?:c[oó]mo\s+(?:est[aá]|va)|how\s+(?:is|are)\s*)\s*(?:el\s+|the\s+|los\s+)?(?:mercado|market|mercados|markets)"
        r"|(?:mercado|market)\s+(?:hoy|today|ahora|now|general)"
        r"|(?:qu[eé]\s+(?:pasa|hay|tal))\s+(?:en\s+|con\s+)?(?:el\s+)?(?:mercado|market)"
        r")",
        re.IGNORECASE,
    ),
    # Price queries: "precio de bitcoin", "price of ETH", "BTC price"
    "market_price": re.compile(
        rf"(?:"
        rf"(?:{_STATUS_VERBS})\s*(?:el\s+|the\s+|der\s+|le\s+|il\s+|o\s+)?(?:{_ALL_TICKERS})"
        rf"|(?:{_ALL_TICKERS})\s+(?:precio|price|preis|prix|prezzo|pre[cç]o|cours|koers)"
        rf")",
        re.IGNORECASE,
    ),
    # Stock queries: "cómo está NVDA", "how is Apple"
    "stock_status": re.compile(
        rf"(?:"
        rf"(?:{_STATUS_VERBS})\s*(?:el\s+|the\s+|der\s+|la\s+)?(?:{_STOCK_TICKERS})"
        rf"|(?:{_STOCK_TICKERS})\s+(?:hoy|today|now|ahora|status|price|precio)"
        rf")",
        re.IGNORECASE,
    ),
    "market_trend": re.compile(
        r"(?:tendencia|trend|direcci[oó]n|richtung|tendance|andamento|tend.ncia)\s+(?:de[l]?\s+|of\s+|von\s+|de\s+|di\s+)?(\w+)\s*(\d+[mhd])?",
        re.IGNORECASE,
    ),
    "watchlist_review": re.compile(
        r"(?:revisa|review|watchlist|lista|[üu]bersicht)\s*(?:mi\s+)?(?:watchlist|lista)?",
        re.IGNORECASE,
    ),
}

# Symbol aliases — natural language → Binance/stock symbol
SYMBOL_ALIASES = {
    # Crypto
    "bitcoin": "BTCUSDT",   "btc": "BTCUSDT",
    "ethereum": "ETHUSDT",  "eth": "ETHUSDT",
    "solana": "SOLUSDT",    "sol": "SOLUSDT",
    "bnb": "BNBUSDT",       "binancecoin": "BNBUSDT",
    "cardano": "ADAUSDT",   "ada": "ADAUSDT",
    "ripple": "XRPUSDT",    "xrp": "XRPUSDT",
    "dogecoin": "DOGEUSDT", "doge": "DOGEUSDT",
    "polkadot": "DOTUSDT",  "dot": "DOTUSDT",
    "avalanche": "AVAXUSDT","avax": "AVAXUSDT",
    "polygon": "MATICUSDT", "matic": "MATICUSDT",
    "chainlink": "LINKUSDT","link": "LINKUSDT",
    "litecoin": "LTCUSDT",  "ltc": "LTCUSDT",
    "uniswap": "UNIUSDT",   "uni": "UNIUSDT",
    "shib": "SHIBUSDT",     "shibainu": "SHIBUSDT",
    "near": "NEARUSDT",
    "atom": "ATOMUSDT",     "cosmos": "ATOMUSDT",
    # Stocks
    "apple": "AAPL",        "tesla": "TSLA",
    "nvidia": "NVDA",       "microsoft": "MSFT",
    "amazon": "AMZN",       "google": "GOOGL",
    "meta": "META",         "facebook": "META",
    "netflix": "NFLX",      "disney": "DIS",
    "spy": "SPY",           "qqq": "QQQ",
    "msft": "MSFT",         "amzn": "AMZN",
    "goog": "GOOGL",        "nflx": "NFLX",
    "aapl": "AAPL",         "tsla": "TSLA",
    "nvda": "NVDA",
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
    text_lower = text.lower()

    # Universal symbol scan: always find the best symbol from text
    def _scan_symbol(default: str = "BTCUSDT") -> str:
        for alias, sym in SYMBOL_ALIASES.items():
            if re.search(r'\b' + re.escape(alias) + r'\b', text_lower):
                return sym
        return default

    if intent == "market_price":
        # Try capture groups first, fallback to full text scan
        symbol_raw = None
        try:
            if match.lastindex:
                for i in range(1, match.lastindex + 1):
                    try:
                        g = match.group(i)
                        if g:
                            symbol_raw = g.strip().lower()
                            break
                    except IndexError:
                        pass
        except Exception:
            pass
        if symbol_raw and symbol_raw in SYMBOL_ALIASES:
            params["symbol"] = SYMBOL_ALIASES[symbol_raw]
        else:
            params["symbol"] = _scan_symbol("BTCUSDT")

    elif intent == "market_trend":
        try:
            symbol_raw = match.group(1).lower() if match.lastindex and match.lastindex >= 1 else ""
            params["symbol"] = SYMBOL_ALIASES.get(symbol_raw, symbol_raw.upper() or "BTCUSDT")
        except Exception:
            params["symbol"] = _scan_symbol()
        try:
            tf = match.group(2) if match.lastindex and match.lastindex >= 2 else None
            params["timeframe"] = TIMEFRAME_MAP.get(tf, "1h") if tf else "1h"
        except Exception:
            params["timeframe"] = "1h"

    elif intent in ("bitcoin_status", "stock_status"):
        params["symbol"] = _scan_symbol(
            "BTCUSDT" if intent == "bitcoin_status" else "SPY"
        )

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


def _build_universe_market_view() -> str:
    """Build market response from Vectrax's own gravitational universe.

    This is the INTERNAL perspective — what Vectrax has observed, not
    what a search engine found. Convergences, hit patterns, gravitational
    mass, observation trends. Exclusive to Vectrax.
    """
    lines = []
    try:
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()

        # Market stars from gravity engine
        market_stars = []
        for rec in gi.all_records():
            if rec.domain == "market":
                weight = round(rec.hits * max(rec.cc_score, 0.01) * max(rec.freq, 0.01) * rec.decay_factor, 2)
                market_stars.append({
                    "symbol": rec.intent,
                    "hits": rec.hits,
                    "cc": rec.cc_score,
                    "weight": weight,
                    "tier": rec.tier,
                })
        market_stars.sort(key=lambda s: -s["weight"])

        if not market_stars:
            return ""

        lines.append("🌌 Observación desde mi universo:\n")
        for s in market_stars:
            intensity = "🔵" if s["hits"] > 50 else "🟢" if s["hits"] > 20 else "⚪"
            lines.append(
                f"{intensity} {s['symbol']}: {s['hits']} activaciones, "
                f"peso {s['weight']}, tier {s['tier']}"
            )

        # Convergences
        convs = gi.cross_domain_convergences()
        market_convs = [c for c in convs if "market" in str(c.get("domains", []))]
        if market_convs:
            lines.append("")
            lines.append("🔗 Convergencias activas:")
            for c in market_convs[:5]:
                lines.append(
                    f"  {c.get('intent', '?')} — "
                    f"hits:{c.get('combined_hits', 0)} "
                    f"dominios:{c.get('domains', [])}"
                )

        # Recent observations from ledger
        try:
            from core.self_observation.observation_ledger import get_by_domain
            market_obs = get_by_domain("market", limit=5)
            if market_obs:
                lines.append("")
                lines.append("📝 Últimas observaciones:")
                for o in market_obs[:3]:
                    lines.append(f"  {o['timestamp'][:16]} — {o['summary'][:60]}")
        except Exception:
            pass

        # Pattern status
        try:
            from connectors.etoro.pattern_memory import get_patterns
            patterns = get_patterns()
            market_patterns = [p for p in patterns if p.n_total >= 5]
            if market_patterns:
                lines.append("")
                lines.append("🧠 Patrones con historia:")
                for p in sorted(market_patterns, key=lambda x: -x.win_rate)[:3]:
                    lines.append(
                        f"  {p.symbol} {p.direction}: "
                        f"WR {p.win_rate:.0f}% ({p.n_total} señales, "
                        f"conf {p.confidence})"
                    )
        except Exception:
            pass

    except Exception as exc:
        logger.debug("universe market view failed: %s", exc)
        return ""

    return "\n".join(lines)


def _handle_snapshot(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle: 'resumen del mercado'

    Universe-first: responds from Vectrax's own observations before
    falling back to external market data.
    """
    # 1. Internal universe perspective (exclusive to Vectrax)
    universe_view = _build_universe_market_view()

    # 2. External market data (prices from Binance/etc)
    lines = []
    try:
        snapshot = market_router.get_market_snapshot()
        if snapshot.get("success"):
            lines.append("📊 Precios en tiempo real:\n")
            for c in snapshot.get("crypto", []):
                if "error" not in c:
                    change = c.get("change_pct", 0)
                    d = "↑" if change >= 0 else "↓"
                    lines.append(f"  {c.get('symbol', '?')}: ${c.get('price', 0):,.2f} ({d} {change:+.2f}%)")
            for s in snapshot.get("stocks", []):
                if "error" not in s:
                    change = s.get("change_pct", 0)
                    d = "↑" if change >= 0 else "↓"
                    lines.append(f"  {s.get('symbol', '?')}: ${s.get('price', 0):,.2f} ({d} {change:+.2f}%)")
    except Exception:
        pass

    # Combine: universe first, then prices
    parts = []
    if universe_view:
        parts.append(universe_view)
    if lines:
        parts.append("\n".join(lines))
    if not parts:
        parts.append("Sin datos de mercado disponibles.")

    return {
        "success": True,
        "intent": "market_snapshot",
        "response": "\n\n".join(parts),
        "data": {},
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
