"""
Vectrax eToro Market Observer
==============================
Evaluates 4 conditions in real time to detect operable trading scenarios.

Conditions:
  1. PRICE_IN_RANGE   — price is within a defined key zone (support/resistance)
  2. RELATIVE_VOLUME  — current volume exceeds average by a threshold
  3. TEMPORAL_ALIGN   — market session and time-of-day alignment
  4. TREND_DIRECTION  — price direction aligns with the intended trade

States:
  NO_OPERABLE   — less than 3 conditions met → silent (no alert)
  PRE_OPERABLE  — exactly 3/4 conditions met → informational only
  OPERABLE      — all 4/4 conditions met → full alert "ESCENARIO OPERABLE DETECTADO"

Modes (persistent per user session):
  STRICT      — default, requires 4/4 for any action
  EXPLORATION — manual activation, allows entry with 3/4 (max 25% position size)

The observer is SILENT when conditions are not met.
When OPERABLE, it emits an alert with:
  - Activation reason (which conditions triggered)
  - Suggested entry level
  - Invalidation criteria
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from connectors.etoro.etoro_client import get_candles, get_instrument_id, get_price

logger = logging.getLogger("vectrax.etoro.observer")

# ── State persistence ────────────────────────────────────────────────
_STATE_FILE = os.path.join(
    os.path.expanduser("~"), ".vectrax", "etoro_observer_state.json"
)


def _load_state() -> Dict:
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {"mode": "strict", "watchlist": {}}


def _save_state(state: Dict) -> None:
    try:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        with open(_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


# ── Enums ────────────────────────────────────────────────────────────

class ScenarioState(str, Enum):
    NO_OPERABLE  = "NO OPERABLE"
    PRE_OPERABLE = "PRE-OPERABLE"
    OPERABLE     = "OPERABLE"


class ObserverMode(str, Enum):
    STRICT      = "strict"
    EXPLORATION = "exploration"


# ── Condition Result ─────────────────────────────────────────────────

@dataclass
class ConditionResult:
    name:        str
    met:         bool
    value:       float        # measured value
    threshold:   float        # required threshold
    description: str          # human-readable detail


@dataclass
class ScenarioResult:
    state:           ScenarioState
    symbol:          str
    mode:            ObserverMode
    conditions:      List[ConditionResult]
    conditions_met:  int
    price:           float
    suggested_entry: Optional[float]
    invalidation:    Optional[float]
    reasons:         List[str]
    max_size_pct:    float        # 100% STRICT, 25% EXPLORATION partial
    timestamp:       float = field(default_factory=time.time)


# ── Core Calculation Functions ───────────────────────────────────────

def _calc_atr(candles: List[Dict], period: int = 14) -> float:
    """Average True Range — measures volatility."""
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        high  = candles[i]["high"]
        low   = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs[-period:]) / period


def _calc_relative_volume(candles: List[Dict], lookback: int = 20) -> float:
    """
    Ratio of current candle volume vs average of last `lookback` candles.
    >1.5 = elevated volume (interesting); >2.0 = high volume (strong signal).
    """
    if len(candles) < lookback + 1:
        return 1.0
    avg_vol = sum(c["volume"] for c in candles[1: lookback + 1]) / lookback
    if avg_vol <= 0:
        return 1.0
    return candles[0]["volume"] / avg_vol


def _detect_key_zone(candles: List[Dict], price: float, atr: float) -> Tuple[bool, float, float]:
    """
    Check if price is near a key support/resistance zone.
    Returns (in_zone, support_level, resistance_level).
    A zone is defined as ±1 ATR from a swing high/low in the last 50 candles.
    """
    if not candles or atr == 0:
        return False, 0.0, 0.0

    highs = [c["high"]  for c in candles[:50]]
    lows  = [c["low"]   for c in candles[:50]]

    swing_high = max(highs)
    swing_low  = min(lows)

    near_resistance = abs(price - swing_high) <= atr * 1.5
    near_support    = abs(price - swing_low)  <= atr * 1.5

    return (near_resistance or near_support), swing_low, swing_high


def _detect_trend(candles: List[Dict], fast: int = 10, slow: int = 30) -> Tuple[str, float]:
    """
    Simple trend detection using EMA fast/slow crossover.
    Returns ('bullish'|'bearish'|'neutral', trend_strength 0-1).
    """
    if len(candles) < slow:
        return "neutral", 0.0

    closes = [c["close"] for c in reversed(candles)]  # oldest first

    def ema(data: List[float], period: int) -> float:
        k = 2 / (period + 1)
        result = data[0]
        for price in data[1:]:
            result = price * k + result * (1 - k)
        return result

    ema_fast = ema(closes[-fast * 2:], fast) if len(closes) >= fast * 2 else closes[-1]
    ema_slow = ema(closes[-slow * 2:], slow) if len(closes) >= slow * 2 else closes[-1]

    diff_pct = (ema_fast - ema_slow) / ema_slow if ema_slow else 0
    strength = min(abs(diff_pct) * 100, 1.0)   # cap at 1.0

    if diff_pct > 0.001:
        return "bullish", strength
    elif diff_pct < -0.001:
        return "bearish", strength
    return "neutral", strength


def _session_alignment() -> Tuple[bool, str]:
    """
    Check if current UTC time falls within an active major session.
    Sessions: London (07-16 UTC), New York (13-22 UTC), Asia (00-09 UTC).
    Overlap zones (London+NY: 13-16 UTC) are the strongest.
    Returns (aligned, session_name).
    """
    hour = time.gmtime().tm_hour
    sessions = []

    if 7 <= hour < 16:
        sessions.append("London")
    if 13 <= hour < 22:
        sessions.append("New York")
    if 0 <= hour < 9:
        sessions.append("Asia")

    if len(sessions) >= 2:
        return True, f"Overlap {'+'.join(sessions)} (high liquidity)"
    elif sessions:
        return True, f"Sesión {sessions[0]}"
    return False, "Mercado cerrado o liquidez baja"


# ── Main Evaluation ──────────────────────────────────────────────────

def evaluate(
    symbol: str,
    direction: str = "buy",   # "buy" or "sell"
    instrument_id: Optional[int] = None,
) -> ScenarioResult:
    """
    Evaluate all 4 conditions for a symbol.

    Args:
        symbol:        e.g. "BTCUSD", "AAPL", "EURUSD"
        direction:     "buy" or "sell" — affects trend alignment condition
        instrument_id: optional, resolved from symbol if not provided

    Returns:
        ScenarioResult with state, conditions detail, and alert if OPERABLE.
    """
    state_data = _load_state()
    mode = ObserverMode(state_data.get("mode", "strict"))

    # Resolve instrument ID
    iid = instrument_id
    if iid is None:
        iid = get_instrument_id(symbol)
    if iid is None:
        # Can't evaluate without instrument ID
        return ScenarioResult(
            state=ScenarioState.NO_OPERABLE,
            symbol=symbol,
            mode=mode,
            conditions=[],
            conditions_met=0,
            price=0.0,
            suggested_entry=None,
            invalidation=None,
            reasons=[f"No se encontró el instrumento '{symbol}' en eToro"],
            max_size_pct=0.0,
        )

    # Fetch price and candles
    price_data   = get_price(iid)
    candles_data = get_candles(iid, interval="Hour1", count=60, direction="desc")

    if not price_data.get("success"):
        return ScenarioResult(
            state=ScenarioState.NO_OPERABLE,
            symbol=symbol,
            mode=mode,
            conditions=[],
            conditions_met=0,
            price=0.0,
            suggested_entry=None,
            invalidation=None,
            reasons=[f"Error obteniendo precio: {price_data.get('error')}"],
            max_size_pct=0.0,
        )

    price   = price_data["mid"]
    candles = candles_data.get("candles", []) if candles_data.get("success") else []

    # ── Calculate signals ────────────────────────────────────────────
    atr      = _calc_atr(candles)
    rel_vol  = _calc_relative_volume(candles)
    in_zone, support, resistance = _detect_key_zone(candles, price, atr)
    trend, trend_strength = _detect_trend(candles)
    session_ok, session_name = _session_alignment()

    # ── Condition 1: Price in key zone ───────────────────────────────
    c1 = ConditionResult(
        name="PRECIO_EN_ZONA",
        met=in_zone,
        value=price,
        threshold=atr * 1.5,
        description=(
            f"Precio {price:.5g} {'está' if in_zone else 'NO está'} en zona clave "
            f"(soporte {support:.5g} / resistencia {resistance:.5g}, ATR={atr:.5g})"
        ),
    )

    # ── Condition 2: Relative volume ─────────────────────────────────
    vol_threshold = 1.3  # 30% above average
    c2 = ConditionResult(
        name="VOLUMEN_RELATIVO",
        met=rel_vol >= vol_threshold,
        value=rel_vol,
        threshold=vol_threshold,
        description=(
            f"Volumen relativo {rel_vol:.2f}x "
            f"({'✓' if rel_vol >= vol_threshold else '✗'} umbral {vol_threshold}x)"
        ),
    )

    # ── Condition 3: Temporal alignment (session) ────────────────────
    c3 = ConditionResult(
        name="ALINEACION_TEMPORAL",
        met=session_ok,
        value=1.0 if session_ok else 0.0,
        threshold=1.0,
        description=f"{session_name}",
    )

    # ── Condition 4: Trend direction alignment ───────────────────────
    trend_aligned = (
        (direction == "buy"  and trend == "bullish") or
        (direction == "sell" and trend == "bearish") or
        trend_strength > 0.5  # strong trend regardless of direction
    )
    c4 = ConditionResult(
        name="DIRECCION_TENDENCIA",
        met=trend_aligned,
        value=trend_strength,
        threshold=0.3,
        description=(
            f"Tendencia {trend} (fuerza {trend_strength:.2f}) "
            f"— dirección solicitada: {direction.upper()}"
        ),
    )

    conditions     = [c1, c2, c3, c4]
    conditions_met = sum(1 for c in conditions if c.met)

    # ── Determine state ──────────────────────────────────────────────
    if conditions_met == 4:
        state = ScenarioState.OPERABLE
    elif conditions_met >= 3:
        state = ScenarioState.PRE_OPERABLE
    else:
        state = ScenarioState.NO_OPERABLE

    # In STRICT mode, PRE_OPERABLE does not allow entry
    max_size_pct = 0.0
    if state == ScenarioState.OPERABLE:
        max_size_pct = 100.0
    elif state == ScenarioState.PRE_OPERABLE and mode == ObserverMode.EXPLORATION:
        max_size_pct = 25.0   # exploration: max 25% entry

    # ── Build alert content ──────────────────────────────────────────
    reasons = [c.description for c in conditions if c.met]
    suggested_entry  = None
    invalidation_lvl = None

    if state in (ScenarioState.OPERABLE, ScenarioState.PRE_OPERABLE):
        if direction == "buy":
            suggested_entry  = round(price * 1.0002, 5)   # slight slippage buffer
            invalidation_lvl = round(support - atr, 5) if support else round(price * 0.99, 5)
        else:
            suggested_entry  = round(price * 0.9998, 5)
            invalidation_lvl = round(resistance + atr, 5) if resistance else round(price * 1.01, 5)

    return ScenarioResult(
        state=state,
        symbol=symbol.upper(),
        mode=mode,
        conditions=conditions,
        conditions_met=conditions_met,
        price=price,
        suggested_entry=suggested_entry,
        invalidation=invalidation_lvl,
        reasons=reasons,
        max_size_pct=max_size_pct,
    )


# ── Mode Management ──────────────────────────────────────────────────

def set_mode(mode: str) -> ObserverMode:
    """Set observer mode ('strict' or 'exploration'). Returns new mode."""
    m = ObserverMode.EXPLORATION if mode.lower() in ("explore", "exploration") else ObserverMode.STRICT
    state = _load_state()
    state["mode"] = m.value
    _save_state(state)
    logger.info("[ETORO] Observer mode set to %s", m.value)
    return m


def get_mode() -> ObserverMode:
    state = _load_state()
    return ObserverMode(state.get("mode", "strict"))


# ── Format Output ────────────────────────────────────────────────────

def format_scenario(result: ScenarioResult) -> str:
    """
    Format ScenarioResult as a Telegram message.
    Silent (empty string) when NO_OPERABLE and not in exploration mode.
    """
    if result.state == ScenarioState.NO_OPERABLE:
        # Completely silent — only show summary if explicitly requested
        cond_summary = "\n".join(
            f"  {'✓' if c.met else '✗'} {c.name}"
            for c in result.conditions
        )
        return (
            f"🔴 NO OPERABLE — {result.symbol}\n"
            f"Condiciones: {result.conditions_met}/4\n"
            f"{cond_summary}"
        )

    lines = []

    if result.state == ScenarioState.OPERABLE:
        lines.append(f"⚡ ESCENARIO OPERABLE DETECTADO — {result.symbol}")
        lines.append("")
    else:
        # PRE_OPERABLE
        if result.mode == ObserverMode.EXPLORATION:
            lines.append(f"🟡 PRE-OPERABLE — {result.symbol} (modo exploración, max 25%)")
        else:
            lines.append(f"🟡 PRE-OPERABLE — {result.symbol} (3/4 condiciones, modo STRICT: no operar)")
        lines.append("")

    # Conditions grid
    for c in result.conditions:
        icon = "✅" if c.met else "❌"
        lines.append(f"{icon} {c.name}: {c.description}")

    lines.append("")
    lines.append(f"💲 Precio actual: {result.price:.6g}")

    if result.suggested_entry:
        lines.append(f"📍 Entrada sugerida: {result.suggested_entry:.6g}")
    if result.invalidation:
        lines.append(f"🛑 Invalidación: {result.invalidation:.6g}")

    if result.max_size_pct > 0:
        lines.append(f"📊 Tamaño máximo: {result.max_size_pct:.0f}%")

    lines.append(f"🔧 Modo: {result.mode.value.upper()}")

    return "\n".join(lines)
