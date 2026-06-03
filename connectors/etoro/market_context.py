"""
Vectrax eToro — Market Context
================================
Gathers accumulated market insight for a symbol so that Vectrax
can answer opinion questions from its OWN observation, not generic data.

Usage:
    from connectors.etoro.market_context import get_market_insight
    ctx = get_market_insight("NVDA")
    # inject `ctx` into LLM prompt as system context

The returned string contains:
  - Current price, spread, trend
  - Relative volume and session
  - Accumulated pattern stats (win rate, expectancy, sample size)
  - Recent signals and their outcomes
  - Current scenario state (NO_OPERABLE / PRE / OPERABLE)
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger("vectrax.etoro.market_context")

# Symbol aliases: natural names → eToro symbols
SYMBOL_ALIASES: Dict[str, str] = {
    "nvidia": "NVDA", "nvda": "NVDA",
    "apple": "AAPL", "aapl": "AAPL",
    "tesla": "TSLA", "tsla": "TSLA",
    "microsoft": "MSFT", "msft": "MSFT",
    "amazon": "AMZN", "amzn": "AMZN",
    "google": "GOOGL", "googl": "GOOGL", "alphabet": "GOOGL",
    "meta": "META", "facebook": "META",
    "bitcoin": "BTC", "btc": "BTC",
    "ethereum": "ETH", "eth": "ETH",
    "solana": "SOL", "sol": "SOL",
    "gold": "GOLD", "oro": "GOLD",
    "eurusd": "EURUSD", "euro": "EURUSD",
    "btcusd": "BTCUSD", "ethusd": "ETHUSD",
}


def resolve_symbol(text: str) -> Optional[str]:
    """Resolve a natural language name to an eToro symbol."""
    clean = text.strip().lower()
    if clean in SYMBOL_ALIASES:
        return SYMBOL_ALIASES[clean]
    # Try uppercase directly (already a symbol)
    upper = clean.upper()
    if len(upper) <= 6 and upper.isalpha():
        return upper
    return None


def record_market_interest(symbol: str, user_id: str = "") -> None:
    """Record that a user asked about a market symbol.

    Creates a gravity event in the cognitive domain with the symbol as
    intent, enabling cross-domain convergence detection with market stars.
    """
    try:
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
        gi.record_event(
            fingerprint=f"user_market_interest:{symbol.upper()}",
            cc_score=0.5,
            impact="medium",
            domain="user_interest",
            intent=symbol.upper(),
            outcome="queried",
            summary=f"User asked about {symbol.upper()}",
        )
    except Exception:
        pass


def get_market_insight(symbol: str) -> Optional[str]:
    """
    Build a structured market insight block for a symbol.

    Returns a text block suitable for LLM context injection,
    or None if the symbol can't be resolved.
    """
    try:
        from connectors.etoro.etoro_client import (
            get_instrument_id, get_price, get_candles,
        )
        from connectors.etoro.market_observer import (
            evaluate, _calc_atr, _calc_relative_volume, _detect_trend,
            _session_alignment,
        )
        from connectors.etoro.signal_recorder import load_signals
        from connectors.etoro.pattern_memory import get_patterns
    except ImportError as e:
        logger.warning("market_context import error: %s", e)
        return None

    # Resolve instrument
    iid = get_instrument_id(symbol)
    if not iid:
        return None

    # Current price
    price_data = get_price(iid)
    if not price_data.get("success"):
        return None

    price = price_data["mid"]
    ask = price_data["ask"]
    bid = price_data["bid"]
    spread = round(ask - bid, 5)

    # Candles for analysis
    candles_data = get_candles(iid, interval="Hour1", count=60, direction="desc")
    candles = candles_data.get("candles", []) if candles_data.get("success") else []

    # Technical signals
    atr = _calc_atr(candles) if candles else 0
    rel_vol = _calc_relative_volume(candles) if candles else 1.0
    trend, trend_str = _detect_trend(candles) if candles else ("neutral", 0.0)
    session_ok, session_name = _session_alignment()

    # Evaluate scenario
    scenario_buy = evaluate(symbol=symbol, direction="buy", instrument_id=iid)
    scenario_sell = evaluate(symbol=symbol, direction="sell", instrument_id=iid)

    # Accumulated patterns for this symbol
    all_patterns = get_patterns()
    sym_patterns = [p for p in all_patterns if p.symbol == symbol.upper()]

    # Recent signals for this symbol
    all_signals = load_signals(limit=200)
    sym_signals = [s for s in all_signals if s.symbol == symbol.upper()]
    recent_signals = sym_signals[-10:]  # last 10

    # Build context block
    lines = [
        f"[OBSERVACIÓN DE MERCADO — {symbol.upper()}]",
        f"Precio actual: {price:.5g} (ask={ask:.5g}, bid={bid:.5g}, spread={spread:.5g})",
        f"Tendencia: {trend} (fuerza={trend_str:.2f})",
        f"Volumen relativo: {rel_vol:.2f}x {'(elevado)' if rel_vol > 1.3 else '(normal)'}",
        f"ATR (volatilidad): {atr:.5g}",
        f"Sesión: {session_name}",
        "",
        f"Escenario BUY: {scenario_buy.state.value} ({scenario_buy.conditions_met}/4 condiciones)",
        f"Escenario SELL: {scenario_sell.state.value} ({scenario_sell.conditions_met}/4 condiciones)",
    ]

    # Pattern memory
    if sym_patterns:
        lines.append("")
        lines.append(f"Patrones acumulados para {symbol.upper()}: {len(sym_patterns)}")
        for p in sorted(sym_patterns, key=lambda x: x.expectancy, reverse=True)[:3]:
            lines.append(
                f"  {p.direction.upper()} | N={p.n_total} | "
                f"WR={p.win_rate:.0f}% | E={p.expectancy:+.3f}% | {p.confidence}"
            )
    else:
        lines.append(f"\nSin patrones acumulados aún para {symbol.upper()}.")

    # Recent signals
    if recent_signals:
        wins = sum(1 for s in sym_signals if s.status == "win")
        losses = sum(1 for s in sym_signals if s.status == "loss")
        pending = sum(1 for s in sym_signals if s.status == "pending")
        total = len(sym_signals)
        lines.append("")
        lines.append(
            f"Señales totales: {total} (wins={wins}, losses={losses}, pending={pending})"
        )
        last = recent_signals[-1]
        age_h = (time.time() - last.timestamp) / 3600
        lines.append(
            f"Última señal: {last.direction.upper()} hace {age_h:.1f}h "
            f"@ {last.price:.5g} — {last.status}"
        )
    else:
        lines.append(f"\nSin señales registradas para {symbol.upper()} todavía.")

    lines.append("[FIN OBSERVACIÓN DE MERCADO]")
    return "\n".join(lines)


def get_watchlist_summary() -> str:
    """Brief summary of all watched symbols for self-context."""
    try:
        from connectors.etoro.learning_engine import DEFAULT_WATCHLIST
        from connectors.etoro.signal_recorder import get_signal_stats
        from connectors.etoro.pattern_memory import get_patterns

        stats = get_signal_stats()
        patterns = get_patterns()
        usable = [p for p in patterns if p.is_usable]

        return (
            f"Observación de mercado activa. "
            f"Watchlist: {', '.join(DEFAULT_WATCHLIST)}. "
            f"Señales totales: {stats['total']} "
            f"(WR={stats['win_rate']}%). "
            f"Patrones: {len(patterns)} ({len(usable)} usables)."
        )
    except Exception:
        return "Conexión a eToro activa (observación de mercado disponible)."
