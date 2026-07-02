"""
Vectrax eToro — Outcome Tracker
=================================
Resolves the outcome of pending signals by fetching the current price
and comparing it against the original prediction.

Classification logic:
  WIN    — price moved ≥ MIN_WIN_PCT in the predicted direction
  LOSS   — price moved ≥ MIN_LOSS_PCT against prediction, OR
           current price touched/crossed the invalidation level
  NEUTRAL — price moved less than MIN_WIN_PCT in either direction
  EXPIRED — outcome_window has passed, still within neutral range

The tracker runs on demand (via learning_engine.py or /vx etoro learn run).
It never executes trades — read-only evaluation only.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.etoro.outcome_tracker")

# ── Thresholds ───────────────────────────────────────────────────────
MIN_WIN_PCT   = 0.3    # ≥0.3% move in predicted direction → WIN
MIN_LOSS_PCT  = 0.2    # ≥0.2% move against prediction → LOSS
# (tighter loss threshold catches reversals earlier)


def classify_outcome(
    direction: str,
    entry_price: float,
    current_price: float,
    invalidation_price: Optional[float],
    window_expired: bool,
) -> Tuple[str, float, bool]:
    """
    Classify the outcome of a signal — SINGLE SOURCE OF TRUTH.

    Reused by BOTH real signal resolution (resolve_pending_signals) and
    PAPER-shadow resolution (connectors/etoro/paper_shadow.py). Do NOT create a
    second win/loss/neutral definition anywhere else.

    Returns:
        (status, return_pct, sl_touched)
        status: "win" | "loss" | "neutral" | "expired"
    """
    if entry_price <= 0:
        return "expired", 0.0, False

    # Price movement as % (positive = moved in buy direction)
    move_pct = (current_price - entry_price) / entry_price * 100
    if direction == "sell":
        move_pct = -move_pct  # for sells, price going down is positive

    # Stop-loss check
    sl_touched = False
    if invalidation_price and invalidation_price > 0:
        if direction == "buy" and current_price <= invalidation_price:
            sl_touched = True
        elif direction == "sell" and current_price >= invalidation_price:
            sl_touched = True

    if sl_touched:
        return "loss", move_pct, True

    if move_pct >= MIN_WIN_PCT:
        return "win", move_pct, False

    if move_pct <= -MIN_LOSS_PCT:
        return "loss", move_pct, False

    if window_expired:
        return "expired", move_pct, False

    return "neutral", move_pct, False


# Backward-compat alias — keep the historical private name pointing at the
# single canonical implementation so no caller re-defines the logic.
_classify_outcome = classify_outcome


def resolve_pending_signals(max_resolve: int = 50) -> Dict[str, Any]:
    """
    Check all PENDING signals and resolve those whose outcome window has passed
    or whose price already hit a clear WIN/LOSS condition.

    Returns a summary of resolutions made.
    """
    from connectors.etoro.signal_recorder import (
        get_pending_signals, update_signal, SignalStatus,
    )
    from connectors.etoro.etoro_client import get_instrument_id, get_price

    pending = get_pending_signals()
    if not pending:
        return {"resolved": 0, "wins": 0, "losses": 0, "neutral": 0, "expired": 0, "skipped": 0}

    # Limit to avoid API hammering
    pending = pending[:max_resolve]

    stats = {"resolved": 0, "wins": 0, "losses": 0, "neutral": 0, "expired": 0, "skipped": 0}
    now = time.time()

    # Cache instrument IDs to minimize API calls
    iid_cache: Dict[str, Optional[int]] = {}
    price_cache: Dict[int, float] = {}

    for sig in pending:
        try:
            # Get instrument ID (cached)
            if sig.symbol not in iid_cache:
                iid_cache[sig.symbol] = get_instrument_id(sig.symbol)
            iid = iid_cache[sig.symbol]

            if not iid:
                stats["skipped"] += 1
                continue

            # Get current price (cached per cycle)
            if iid not in price_cache:
                r = get_price(iid)
                if r.get("success"):
                    price_cache[iid] = r["mid"]
                else:
                    stats["skipped"] += 1
                    continue
            current_price = price_cache[iid]

            # Use entry_price if available, else original signal price
            entry_px = sig.entry_price if sig.entry_price else sig.price

            # Check if outcome window has expired
            window_secs  = sig.outcome_window_h * 3600
            window_expired = (now - sig.timestamp) >= window_secs

            # Don't resolve signals that just started (min 30 min wait)
            age_min = (now - sig.timestamp) / 60
            if age_min < 30 and not window_expired:
                stats["skipped"] += 1
                continue

            status, ret_pct, sl_hit = classify_outcome(
                direction=sig.direction,
                entry_price=entry_px,
                current_price=current_price,
                invalidation_price=sig.invalidation_price,
                window_expired=window_expired,
            )

            # Update signal in file
            update_signal(sig.signal_id, {
                "status":             status,
                "outcome_price":      current_price,
                "outcome_timestamp":  now,
                "return_pct":         round(ret_pct, 4),
                "sl_touched":         sl_hit,
            })

            stats["resolved"] += 1
            stats[status] = stats.get(status, 0) + 1

            logger.info(
                "[OUTCOME] %s | %s %s | entry=%.5g → now=%.5g | "
                "ret=%.2f%% | sl=%s | status=%s",
                sig.signal_id, sig.direction.upper(), sig.symbol,
                entry_px, current_price, ret_pct, sl_hit, status.upper(),
            )

        except Exception as e:
            logger.warning("outcome resolve error for %s: %s", sig.signal_id, e)
            stats["skipped"] += 1

    return stats


def get_recent_outcomes(limit: int = 20) -> List[Dict]:
    """Return the most recent resolved signals for display."""
    from connectors.etoro.signal_recorder import load_signals, SignalStatus
    import datetime

    all_signals = load_signals()
    resolved = [
        s for s in all_signals
        if s.status != SignalStatus.PENDING.value
    ]
    resolved_sorted = sorted(resolved, key=lambda s: s.timestamp, reverse=True)

    result = []
    for s in resolved_sorted[:limit]:
        ts_str = datetime.datetime.utcfromtimestamp(s.timestamp).strftime("%m/%d %H:%M")
        result.append({
            "signal_id":  s.signal_id,
            "timestamp":  ts_str,
            "symbol":     s.symbol,
            "direction":  s.direction,
            "status":     s.status,
            "return_pct": s.return_pct,
            "sl_touched": s.sl_touched,
            "scenario":   s.scenario,
        })
    return result


def format_outcomes_panel(limit: int = 10) -> str:
    """Format recent outcomes as a Telegram message."""
    outcomes = get_recent_outcomes(limit)
    if not outcomes:
        return "Sin outcomes resueltos aún. Ejecuta /vx etoro learn run para procesar señales pendientes."

    icons = {"win": "✅", "loss": "❌", "neutral": "⚪", "expired": "⏳"}
    lines = [f"📊 Outcomes recientes ({len(outcomes)}):\n"]
    for o in outcomes:
        icon   = icons.get(o["status"], "?")
        ret    = f"{o['return_pct']:+.2f}%" if o["return_pct"] is not None else "?"
        sl_tag = " 🛑SL" if o["sl_touched"] else ""
        lines.append(
            f"{icon} {o['timestamp']} {o['direction'].upper()} {o['symbol']} "
            f"→ {ret}{sl_tag}"
        )
    return "\n".join(lines)
