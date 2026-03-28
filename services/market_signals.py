"""
Vectrax Market Data — Market Signals Service.

Technical analysis primitives:
  detect_trend(symbol, timeframe)     — SMA-based trend direction
  detect_momentum(symbol, timeframe)  — rate of change momentum
  detect_breakout(symbol, timeframe)  — price vs recent high/low channels
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from services.market_router import get_ohlcv, get_crypto_ticker, is_crypto

logger = logging.getLogger("vectrax.market.signals")


def _extract_closes(candles: List[Dict[str, Any]]) -> List[float]:
    """Extract close prices from candle list."""
    return [c["close"] for c in candles if "close" in c]


def _sma(values: List[float], period: int) -> Optional[float]:
    """Simple Moving Average."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _ema(values: List[float], period: int) -> Optional[float]:
    """Exponential Moving Average."""
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    ema_val = sum(values[:period]) / period
    for price in values[period:]:
        ema_val = (price - ema_val) * multiplier + ema_val
    return ema_val


def _roc(values: List[float], period: int = 14) -> Optional[float]:
    """Rate of Change (momentum indicator)."""
    if len(values) <= period:
        return None
    current = values[-1]
    previous = values[-(period + 1)]
    if previous == 0:
        return None
    return ((current - previous) / previous) * 100


# ── Public Functions ────────────────────────────────────────────────

def detect_trend(symbol: str, timeframe: str = "1h") -> Dict[str, Any]:
    """
    Detect trend using SMA crossover (SMA-7 vs SMA-25).

    Returns:
      trend: "bullish" | "bearish" | "neutral"
      strength: float 0-1
      sma_fast / sma_slow: actual values
    """
    ohlcv = get_ohlcv(symbol, timeframe, limit=50)
    if not ohlcv.get("success"):
        return {"success": False, "error": ohlcv.get("error", "OHLCV unavailable")}

    candles = ohlcv.get("candles", [])
    closes = _extract_closes(candles)
    if len(closes) < 25:
        return {"success": False, "error": f"Not enough data ({len(closes)} candles)"}

    sma_fast = _sma(closes, 7)
    sma_slow = _sma(closes, 25)
    current = closes[-1]

    if sma_fast is None or sma_slow is None:
        return {"success": False, "error": "SMA calculation failed"}

    # Trend direction
    if sma_fast > sma_slow:
        trend = "bullish"
    elif sma_fast < sma_slow:
        trend = "bearish"
    else:
        trend = "neutral"

    # Strength: how far apart the SMAs are relative to price
    spread = abs(sma_fast - sma_slow) / current if current > 0 else 0
    strength = min(1.0, spread * 100)  # Normalize to 0-1

    return {
        "success": True,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "trend": trend,
        "strength": round(strength, 3),
        "price": current,
        "sma_fast": round(sma_fast, 2),
        "sma_slow": round(sma_slow, 2),
        "candles_analyzed": len(closes),
    }


def detect_momentum(symbol: str, timeframe: str = "1h") -> Dict[str, Any]:
    """
    Detect momentum using Rate of Change (ROC).

    Returns:
      momentum: "strong_bullish" | "bullish" | "neutral" | "bearish" | "strong_bearish"
      roc: percentage change over period
    """
    ohlcv = get_ohlcv(symbol, timeframe, limit=30)
    if not ohlcv.get("success"):
        return {"success": False, "error": ohlcv.get("error", "OHLCV unavailable")}

    closes = _extract_closes(ohlcv.get("candles", []))
    if len(closes) < 15:
        return {"success": False, "error": f"Not enough data ({len(closes)} candles)"}

    roc_val = _roc(closes, 14)
    if roc_val is None:
        return {"success": False, "error": "ROC calculation failed"}

    # Classify momentum
    if roc_val > 5:
        momentum = "strong_bullish"
    elif roc_val > 1:
        momentum = "bullish"
    elif roc_val > -1:
        momentum = "neutral"
    elif roc_val > -5:
        momentum = "bearish"
    else:
        momentum = "strong_bearish"

    return {
        "success": True,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "momentum": momentum,
        "roc": round(roc_val, 3),
        "price": closes[-1],
        "candles_analyzed": len(closes),
    }


def detect_breakout(symbol: str, timeframe: str = "1h", lookback: int = 20) -> Dict[str, Any]:
    """
    Detect price breakout above/below recent channel.

    Uses highest high and lowest low over lookback period.
    Returns:
      breakout: "up" | "down" | "none"
      channel_high / channel_low
    """
    ohlcv = get_ohlcv(symbol, timeframe, limit=lookback + 5)
    if not ohlcv.get("success"):
        return {"success": False, "error": ohlcv.get("error", "OHLCV unavailable")}

    candles = ohlcv.get("candles", [])
    if len(candles) < lookback:
        return {"success": False, "error": f"Not enough data ({len(candles)} candles)"}

    # Channel from lookback candles (excluding last)
    channel_candles = candles[-(lookback + 1):-1]
    highs = [c["high"] for c in channel_candles]
    lows = [c["low"] for c in channel_candles]

    channel_high = max(highs)
    channel_low = min(lows)
    current_close = candles[-1]["close"]
    current_high = candles[-1]["high"]
    current_low = candles[-1]["low"]

    # Breakout detection
    if current_close > channel_high:
        breakout = "up"
    elif current_close < channel_low:
        breakout = "down"
    else:
        breakout = "none"

    # Distance from channel boundaries
    range_size = channel_high - channel_low if channel_high != channel_low else 1
    position = (current_close - channel_low) / range_size

    return {
        "success": True,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "breakout": breakout,
        "price": current_close,
        "channel_high": round(channel_high, 2),
        "channel_low": round(channel_low, 2),
        "position_in_channel": round(position, 3),
        "lookback": lookback,
    }


def full_analysis(symbol: str, timeframe: str = "1h") -> Dict[str, Any]:
    """Run all signal detectors for a symbol."""
    trend = detect_trend(symbol, timeframe)
    momentum = detect_momentum(symbol, timeframe)
    breakout = detect_breakout(symbol, timeframe)

    # Get current ticker data for context
    if is_crypto(symbol):
        ticker = get_crypto_ticker(symbol)
    else:
        from services.market_router import get_stock_quote
        ticker = get_stock_quote(symbol)

    return {
        "success": True,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "price": ticker.get("price", 0) if ticker.get("success") else 0,
        "change_pct": ticker.get("change_pct", 0) if ticker.get("success") else 0,
        "trend": trend if trend.get("success") else {"error": trend.get("error")},
        "momentum": momentum if momentum.get("success") else {"error": momentum.get("error")},
        "breakout": breakout if breakout.get("success") else {"error": breakout.get("error")},
    }
