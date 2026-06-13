"""
/v1/market/live — Real-time market cycle data for visualization.

Returns the complete cycle in one response:
  1. Symbols being observed (with live prices)
  2. Recent signals detected
  3. Learned patterns
  4. Active proposals
  5. Open positions (PAPER)
  6. Recent learning events (closes + pattern updates)

Auto-executor status included.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/market", tags=["market-live"])
logger = logging.getLogger("vectrax.routes.market_live")

_HTML_PATH = Path(__file__).resolve().parent.parent.parent / "ui" / "static" / "market_live.html"


@router.get("/view", response_class=HTMLResponse)
async def market_view():
    if _HTML_PATH.exists():
        return HTMLResponse(_HTML_PATH.read_text())
    return HTMLResponse("<h1>market_live.html not found</h1>", status_code=404)


@router.get("/live")
async def market_live() -> Dict[str, Any]:
    """Complete market cycle snapshot for real-time UI."""
    result: Dict[str, Any] = {"ts": time.time()}

    # === 1. SYMBOLS BEING OBSERVED (with live prices) ===
    symbols = []
    try:
        from connectors.etoro.learning_engine import DEFAULT_WATCHLIST
        from connectors.etoro.etoro_client import get_instrument_id, get_price

        for sym in DEFAULT_WATCHLIST:
            entry = {"symbol": sym.upper(), "status": "observando", "price": None, "bid": None, "ask": None}
            try:
                iid = get_instrument_id(sym)
                if iid:
                    p = get_price(iid)
                    if p.get("success"):
                        entry["price"] = p["mid"]
                        entry["bid"] = p["bid"]
                        entry["ask"] = p["ask"]
                        entry["status"] = "activo"
                    else:
                        entry["status"] = "sin precio"
                else:
                    entry["status"] = "no resuelto"
            except Exception:
                entry["status"] = "error"
            symbols.append(entry)
    except Exception as exc:
        result["symbols_error"] = str(exc)
    result["symbols"] = symbols

    # === 2. RECENT SIGNALS ===
    signals = []
    try:
        from connectors.etoro.signal_recorder import load_signals
        raw = load_signals(limit=20)
        for s in reversed(raw[-10:]):  # latest 10
            signals.append({
                "id": s.signal_id,
                "symbol": s.symbol,
                "direction": s.direction,
                "scenario": s.scenario,
                "conditions_met": s.conditions_met,
                "price": s.price,
                "status": s.status,
                "ts": s.timestamp,
                "age_min": round((time.time() - s.timestamp) / 60, 1),
            })
    except Exception as exc:
        result["signals_error"] = str(exc)
    result["signals"] = signals

    # === 3. LEARNED PATTERNS ===
    patterns = []
    try:
        from connectors.etoro.pattern_memory import get_patterns
        all_p = get_patterns()
        for p in sorted(all_p, key=lambda x: x.n_total, reverse=True)[:10]:
            patterns.append({
                "key": p.pattern_key[:40] if hasattr(p, 'pattern_key') else f"{p.symbol}-{p.direction}",
                "symbol": p.symbol,
                "direction": p.direction,
                "n_total": p.n_total,
                "win_rate": round(p.win_rate, 1),
                "expectancy": round(p.expectancy, 3),
                "confidence": p.confidence,
                "usable": p.is_usable,
            })
    except Exception as exc:
        result["patterns_error"] = str(exc)
    result["patterns"] = patterns

    # === 4. ACTIVE PROPOSALS ===
    proposals = []
    try:
        from connectors.etoro.learning_engine import load_proposals
        pending = load_proposals(limit=10, status_filter="pending")
        for p in pending:
            proposals.append({
                "id": p.proposal_id,
                "symbol": p.symbol,
                "direction": p.direction,
                "entry_price": p.entry_price,
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit,
                "confidence": p.confidence,
                "win_rate": round(p.win_rate, 1),
                "reasoning": p.reasoning[:100],
                "ts": p.timestamp,
            })
    except Exception as exc:
        result["proposals_error"] = str(exc)
    result["proposals"] = proposals

    # === 5. OPEN POSITIONS (PAPER) ===
    positions = []
    try:
        from connectors.etoro.auto_executor import get_paper_trades
        open_trades = [t for t in get_paper_trades(limit=50) if t.status == "open"]
        for t in open_trades:
            current = None
            try:
                from connectors.etoro.position_manager import _get_current_price
                current = _get_current_price(t.symbol)
            except Exception:
                pass
            pnl_pct = 0
            if current and t.entry_price:
                if t.direction == "buy":
                    pnl_pct = (current - t.entry_price) / t.entry_price * 100
                else:
                    pnl_pct = (t.entry_price - current) / t.entry_price * 100
            positions.append({
                "id": t.trade_id,
                "symbol": t.symbol,
                "direction": t.direction,
                "entry_price": t.entry_price,
                "current_price": current,
                "pnl_pct": round(pnl_pct, 3),
                "pnl_usd": round(t.amount_usd * pnl_pct / 100, 2) if pnl_pct else 0,
                "amount_usd": t.amount_usd,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
                "hours_open": round((time.time() - t.timestamp) / 3600, 1),
            })
    except Exception as exc:
        result["positions_error"] = str(exc)
    result["positions"] = positions

    # === 6. RECENT LEARNING EVENTS (closed trades + pattern updates) ===
    learning = []
    try:
        from connectors.etoro.auto_executor import get_paper_trades
        closed = [t for t in get_paper_trades(limit=50) if t.status.startswith("closed")]
        for t in sorted(closed, key=lambda x: x.timestamp, reverse=True)[:5]:
            learning.append({
                "type": "close",
                "symbol": t.symbol,
                "direction": t.direction,
                "result": "ganadora" if t.pnl_usd and t.pnl_usd > 0 else "perdedora" if t.pnl_usd and t.pnl_usd < 0 else "neutral",
                "pnl_usd": t.pnl_usd,
                "pnl_pct": t.pnl_pct,
            })
    except Exception:
        pass
    result["learning"] = learning

    # === 7. AUTO-EXECUTOR STATUS ===
    try:
        from connectors.etoro.auto_executor import get_config, get_mode
        cfg = get_config()
        result["executor"] = {
            "mode": cfg.get("mode", "off"),
            "paper_trades": cfg.get("paper_trades_total", 0),
            "paper_wins": cfg.get("paper_trades_wins", 0),
            "consecutive_losses": cfg.get("consecutive_losses", 0),
            "daily_loss_usd": cfg.get("daily_loss_usd", 0),
            "halt": cfg.get("halt", False),
            "max_position_usd": cfg.get("max_position_usd", 50),
            "min_paper_signals": cfg.get("min_paper_signals", 30),
            "min_paper_win_rate": cfg.get("min_paper_win_rate", 60),
        }
    except Exception:
        result["executor"] = {"mode": "unknown"}

    # === 8. ALL PATTERNS WITH PROGRESS ===
    all_patterns = []
    try:
        from connectors.etoro.pattern_memory import get_patterns
        for p in sorted(get_patterns(), key=lambda x: x.n_total, reverse=True):
            if p.n_total < 2:
                continue
            all_patterns.append({
                "symbol": p.symbol,
                "direction": p.direction,
                "n_total": p.n_total,
                "n_wins": p.n_wins,
                "n_losses": p.n_losses,
                "win_rate": round(p.win_rate, 1),
                "expectancy": round(p.expectancy, 3),
                "confidence": p.confidence,
                "usable": p.is_usable,
                "progress": min(round(p.n_total / 15 * 100), 100),
            })
    except Exception:
        pass
    result["all_patterns"] = all_patterns

    # === 9. NOTIFICATION HISTORY ===
    notifications = []
    try:
        from core.learn.gravity_engine import get_alert_history
        for a in get_alert_history(limit=10):
            notifications.append({
                "type": "convergence",
                "timestamp": a.get("timestamp", ""),
                "symbol": a.get("intent", "?"),
                "detail": ", ".join(a.get("reasons", [])) or f"cc={a.get('combined_cc', '?')}",
            })
    except Exception:
        pass
    try:
        import json as _json
        _alerted_file = os.path.join(os.path.expanduser("~"), ".vectrax", "alerted_usable_patterns.json")
        if os.path.exists(_alerted_file):
            alerted = _json.load(open(_alerted_file))
            for key in alerted:
                notifications.append({
                    "type": "pattern_usable",
                    "timestamp": "",
                    "symbol": key.split("_")[0] if "_" in key else key,
                    "detail": f"Patr\u00f3n usable: {key}",
                })
    except Exception:
        pass
    result["notifications"] = notifications

    return result
