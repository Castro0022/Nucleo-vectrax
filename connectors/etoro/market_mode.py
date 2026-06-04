"""
connectors/etoro/market_mode.py — Modo Mercado vs Modo Memoria.

Determina el modo activo del sistema basándose en el estado de los mercados:

  MARKET — Al menos un mercado está abierto. Observar precios, volumen,
           señales, spreads. Registrar cambios de mercado en el ledger.

  MEMORY — Todos los mercados están cerrados. Reflexionar sobre lo
           observado: convergencias, patrones, masa gravitacional,
           confianza acumulada. Sin ruido de mercado.

API pública:
    get_active_mode() -> "market" | "memory"
    is_market_open(symbol) -> bool
    get_open_symbols(watchlist) -> list[str]
    get_closed_symbols(watchlist) -> list[str]
"""

from __future__ import annotations

import datetime
import logging
from typing import List

logger = logging.getLogger("vectrax.etoro.market_mode")


def is_market_open(symbol: str) -> bool:
    """Check if the market for this symbol is currently open."""
    now = datetime.datetime.utcnow()
    hour = now.hour
    weekday = now.weekday()  # 0=Monday, 6=Sunday
    sym = symbol.upper()

    # Crypto: 24/7
    if sym.endswith("USD") and sym[:3] in ("BTC", "ETH"):
        return True

    # Forex: Sunday 22:00 to Friday 22:00 UTC
    if any(sym.startswith(c) for c in ("EUR", "GBP", "JPY", "AUD", "CHF")):
        if weekday == 5:  # Saturday
            return False
        if weekday == 6 and hour < 22:
            return False
        if weekday == 4 and hour >= 22:
            return False
        return True

    # Stocks (US): Monday-Friday, 14:30-21:00 UTC
    if weekday >= 5:
        return False
    return 14 <= hour < 21


def get_open_symbols(watchlist: List[str]) -> List[str]:
    """Return symbols from watchlist that have an open market right now."""
    return [s for s in watchlist if is_market_open(s)]


def get_closed_symbols(watchlist: List[str]) -> List[str]:
    """Return symbols from watchlist that have a closed market right now."""
    return [s for s in watchlist if not is_market_open(s)]


def get_active_mode(watchlist: List[str] | None = None) -> str:
    """Return 'market' if any watched market is open, 'memory' otherwise."""
    if watchlist is None:
        from connectors.etoro.learning_engine import DEFAULT_WATCHLIST
        watchlist = DEFAULT_WATCHLIST
    return "market" if any(is_market_open(s) for s in watchlist) else "memory"
