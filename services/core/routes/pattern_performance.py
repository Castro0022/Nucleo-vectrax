"""
/v1/market/patterns — Trading Pattern Performance Analytics
=============================================================
Dedicated endpoint for the Pattern Performance Dashboard.

Returns:
  1. KPI summary (total patterns, usable, global win rate, expectancy)
  2. Pattern leaderboard (all patterns ranked by expectancy)
  3. Per-symbol breakdown (aggregated stats per symbol)
  4. Signal timeline (recent signals with outcomes for dot-chart)
  5. Equity curve (cumulative P&L from resolved signals)
  6. Executor status
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/market", tags=["pattern-performance"])
logger = logging.getLogger("vectrax.routes.pattern_performance")

_HTML_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "ui" / "static" / "pattern_performance.html"
)


@router.get("/patterns/view", response_class=HTMLResponse)
async def pattern_performance_view():
    if _HTML_PATH.exists():
        return HTMLResponse(_HTML_PATH.read_text())
    return HTMLResponse("<h1>pattern_performance.html not found</h1>", status_code=404)


# Cache to avoid heavy recomputation on rapid refreshes
_cache: Dict[str, Any] = {"data": None, "ts": 0}
_CACHE_TTL = 20  # seconds


@router.get("/patterns")
async def pattern_performance() -> Dict[str, Any]:
    """Full pattern performance analytics for the dashboard."""
    now = time.time()
    if _cache["data"] and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["data"]

    result: Dict[str, Any] = {"ts": now}

    # === 1. ALL PATTERNS ===
    patterns_list: List[Dict] = []
    try:
        from connectors.etoro.pattern_memory import get_patterns
        all_patterns = get_patterns()
        for p in all_patterns:
            patterns_list.append({
                "key": p.pattern_key,
                "symbol": p.symbol,
                "direction": p.direction,
                "session": p.session,
                "conditions": p.conditions_key,
                "n_total": p.n_total,
                "n_wins": p.n_wins,
                "n_losses": p.n_losses,
                "n_neutral": p.n_neutral,
                "n_expired": p.n_expired,
                "win_rate": round(p.win_rate, 1),
                "avg_win_pct": round(p.avg_win_pct, 3),
                "avg_loss_pct": round(p.avg_loss_pct, 3),
                "expectancy": round(p.expectancy, 3),
                "confidence": p.confidence,
                "usable": p.is_usable,
                "progress": min(round(p.n_total / 15 * 100), 100),
                "first_seen": p.first_seen,
                "last_updated": p.last_updated,
            })
    except Exception as exc:
        result["patterns_error"] = str(exc)
    result["patterns"] = sorted(patterns_list, key=lambda x: x["expectancy"], reverse=True)

    # === 2. KPI SUMMARY ===
    total = len(patterns_list)
    usable = sum(1 for p in patterns_list if p["usable"])
    decisive = sum(p["n_wins"] + p["n_losses"] for p in patterns_list)
    total_wins = sum(p["n_wins"] for p in patterns_list)
    total_losses = sum(p["n_losses"] for p in patterns_list)
    global_wr = round(total_wins / max(decisive, 1) * 100, 1)
    avg_exp = round(
        sum(p["expectancy"] for p in patterns_list) / max(total, 1), 3
    )
    best_pattern = max(patterns_list, key=lambda p: p["expectancy"]) if patterns_list else None
    worst_pattern = min(patterns_list, key=lambda p: p["expectancy"]) if patterns_list else None

    result["kpi"] = {
        "total_patterns": total,
        "usable_patterns": usable,
        "building_patterns": sum(1 for p in patterns_list if not p["usable"] and p["n_total"] < 15),
        "negative_patterns": sum(
            1 for p in patterns_list
            if p["n_total"] >= 15 and (p["win_rate"] < 55 or p["expectancy"] <= 0)
        ),
        "global_win_rate": global_wr,
        "avg_expectancy": avg_exp,
        "total_signals_resolved": decisive,
        "total_wins": total_wins,
        "total_losses": total_losses,
        "best_pattern": {
            "symbol": best_pattern["symbol"],
            "direction": best_pattern["direction"],
            "expectancy": best_pattern["expectancy"],
            "win_rate": best_pattern["win_rate"],
        } if best_pattern else None,
        "worst_pattern": {
            "symbol": worst_pattern["symbol"],
            "direction": worst_pattern["direction"],
            "expectancy": worst_pattern["expectancy"],
            "win_rate": worst_pattern["win_rate"],
        } if worst_pattern else None,
    }

    # === 3. PER-SYMBOL BREAKDOWN ===
    symbol_map: Dict[str, Dict] = {}
    for p in patterns_list:
        sym = p["symbol"]
        if sym not in symbol_map:
            symbol_map[sym] = {
                "symbol": sym, "patterns": 0, "usable": 0,
                "signals": 0, "wins": 0, "losses": 0,
                "best_exp": -999, "worst_exp": 999,
                "avg_wr": [],
            }
        s = symbol_map[sym]
        s["patterns"] += 1
        if p["usable"]:
            s["usable"] += 1
        s["signals"] += p["n_total"]
        s["wins"] += p["n_wins"]
        s["losses"] += p["n_losses"]
        s["best_exp"] = max(s["best_exp"], p["expectancy"])
        s["worst_exp"] = min(s["worst_exp"], p["expectancy"])
        if p["n_wins"] + p["n_losses"] > 0:
            s["avg_wr"].append(p["win_rate"])

    symbols_breakdown = []
    for sym, s in sorted(symbol_map.items(), key=lambda x: x[1]["wins"], reverse=True):
        wr_list = s.pop("avg_wr")
        s["win_rate"] = round(
            s["wins"] / max(s["wins"] + s["losses"], 1) * 100, 1
        )
        s["best_exp"] = round(s["best_exp"], 3) if s["best_exp"] != -999 else 0
        s["worst_exp"] = round(s["worst_exp"], 3) if s["worst_exp"] != 999 else 0
        symbols_breakdown.append(s)
    result["symbols"] = symbols_breakdown

    # === 4. SIGNAL TIMELINE (last 60 resolved signals) ===
    timeline: List[Dict] = []
    try:
        from connectors.etoro.signal_recorder import load_signals
        all_signals = load_signals()
        resolved = [
            s for s in all_signals
            if s.status in ("win", "loss", "neutral", "expired")
        ]
        for s in resolved[-60:]:
            timeline.append({
                "id": s.signal_id,
                "ts": s.timestamp,
                "symbol": s.symbol,
                "direction": s.direction,
                "status": s.status,
                "conditions": s.conditions_met,
                "price": s.price,
                "return_pct": round(s.return_pct, 3) if s.return_pct else 0,
            })
    except Exception as exc:
        result["timeline_error"] = str(exc)
    result["timeline"] = timeline

    # === 5. EQUITY CURVE (cumulative P&L from resolved signals) ===
    equity: List[Dict] = []
    cumulative = 0.0
    try:
        for s in timeline:
            cumulative += s["return_pct"]
            equity.append({
                "ts": s["ts"],
                "cumulative_pct": round(cumulative, 3),
                "status": s["status"],
                "symbol": s["symbol"],
            })
    except Exception:
        pass
    result["equity_curve"] = equity

    # === 6. EXECUTOR STATUS ===
    try:
        from connectors.etoro.auto_executor import get_config
        cfg = get_config()
        result["executor"] = {
            "mode": cfg.get("mode", "off"),
            "paper_trades": cfg.get("paper_trades_total", 0),
            "paper_wins": cfg.get("paper_trades_wins", 0),
            "paper_wr": round(
                cfg.get("paper_trades_wins", 0)
                / max(cfg.get("paper_trades_total", 0), 1) * 100, 1
            ),
            "consecutive_losses": cfg.get("consecutive_losses", 0),
            "daily_loss_usd": cfg.get("daily_loss_usd", 0),
            "halt": cfg.get("halt", False),
            "max_position_usd": cfg.get("max_position_usd", 50),
            "min_paper_signals": cfg.get("min_paper_signals", 30),
            "min_paper_win_rate": cfg.get("min_paper_win_rate", 60),
        }
    except Exception:
        result["executor"] = {"mode": "unknown"}

    # === 7. PENDING SIGNALS (not yet resolved) ===
    try:
        from connectors.etoro.signal_recorder import load_signals
        pending = [s for s in load_signals() if s.status == "pending"]
        result["pending_signals"] = len(pending)
    except Exception:
        result["pending_signals"] = 0

    # === 8. PAPER-SHADOW (observational — SEPARATE from the real executor) ===
    # Does NOT advance LIVE progress and is NOT counted as real paper_trades.
    try:
        from connectors.etoro.paper_shadow import get_shadow_stats
        result["shadow"] = get_shadow_stats()
    except Exception as exc:
        result["shadow"] = {"enabled": False, "error": str(exc)}

    _cache["data"] = result
    _cache["ts"] = now
    return result
