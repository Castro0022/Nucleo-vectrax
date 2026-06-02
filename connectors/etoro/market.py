"""
connectors/etoro/market.py — eToro Market Data
================================================
Search instruments, get live rates, historical candles.
All methods are fail-safe (return None/empty on error).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from connectors.etoro.client import get_etoro_client

logger = logging.getLogger("vectrax.etoro.market")


def search_instrument(symbol: str) -> Optional[Dict[str, Any]]:
    """Search for an instrument by symbol. Returns first exact match."""
    c = get_etoro_client()
    data = c.get("market-data/search", params={"internalSymbolFull": symbol.upper()})
    if not data or not data.get("items"):
        return None
    # Exact match on symbol
    for item in data["items"]:
        if item.get("internalSymbolFull", "").upper() == symbol.upper():
            return item
    return data["items"][0] if data["items"] else None


def resolve_instrument_id(symbol: str) -> Optional[int]:
    """Resolve a ticker symbol to its eToro instrument ID."""
    item = search_instrument(symbol)
    # eToro uses lowercase 'd' in search response: instrumentId
    return item.get("instrumentId") if item else None


def get_rates(instrument_ids: List[int]) -> Optional[List[Dict]]:
    """Get live rates for one or more instruments."""
    if not instrument_ids:
        return None
    c = get_etoro_client()
    ids_str = ",".join(str(i) for i in instrument_ids)
    return c.get("market-data/instruments/rates", params={"instrumentIds": ids_str})


def get_price(symbol: str) -> Optional[Dict[str, Any]]:
    """Get current price for a symbol. Returns {symbol, bid, ask, last}."""
    iid = resolve_instrument_id(symbol)
    if iid is None:
        return None
    rates = get_rates([iid])
    if not rates:
        return None
    # rates can be a list or dict depending on endpoint version
    rate = rates[0] if isinstance(rates, list) else rates
    return {
        "symbol": symbol.upper(),
        "instrumentId": iid,
        "bid": rate.get("bid") or rate.get("Bid"),
        "ask": rate.get("ask") or rate.get("Ask"),
        "last": rate.get("lastPrice") or rate.get("LastPrice"),
    }


def get_instrument_info(instrument_id: int) -> Optional[Dict]:
    """Get detailed instrument metadata."""
    c = get_etoro_client()
    return c.get("market-data/instruments", params={"instrumentIds": str(instrument_id)})


def get_candles(
    instrument_id: int,
    interval: str = "OneDay",
    count: int = 30,
) -> Optional[List[Dict]]:
    """Get historical OHLC candles.

    Intervals: OneMinute, FiveMinutes, FifteenMinutes, ThirtyMinutes,
               OneHour, FourHours, OneDay, OneWeek
    """
    c = get_etoro_client()
    return c.get(
        f"market-data/instruments/{instrument_id}/candles",
        params={"interval": interval, "count": count},
    )
