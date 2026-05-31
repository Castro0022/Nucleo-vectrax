"""
Vectrax eToro — Learning Engine
=================================
Closes the market learning loop:

  1. OBSERVE  — evaluate market via observer, record any PRE/OPERABLE scenario
  2. RESOLVE  — update outcomes of pending signals
  3. LEARN    — rebuild pattern statistics from resolved signals
  4. PROPOSE  — generate proposals based on usable patterns + current conditions
  5. STORE    — save proposals (never auto-executes without explicit authorization)

This module is the central orchestrator. It does NOT execute trades.
All proposals must be explicitly reviewed and approved by the creator.

Persistence:
  ~/.vectrax/etoro_signals.jsonl    — raw signals
  ~/.vectrax/etoro_patterns.json    — statistical patterns
  ~/.vectrax/etoro_proposals.jsonl  — generated proposals
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.etoro.learning_engine")

_PROPOSALS_FILE = os.path.join(
    os.path.expanduser("~"), ".vectrax", "etoro_proposals.jsonl"
)

# Symbols to observe during a learn cycle
DEFAULT_WATCHLIST = ["BTCUSD", "ETHUSD", "EURUSD", "AAPL", "TSLA"]


# ── Proposal dataclass ────────────────────────────────────────────────

@dataclass
class TradeProposal:
    proposal_id:     str
    timestamp:       float
    symbol:          str
    direction:       str
    entry_price:     float
    stop_loss:       float
    take_profit:     float
    confidence:      str       # LOW / MEDIUM / HIGH
    win_rate:        float
    expectancy:      float
    pattern_n:       int
    scenario_state:  str
    conditions_met:  int
    reasoning:       str
    status:          str = "pending"   # pending / approved / rejected / executed / expired

    def to_dict(self) -> Dict:
        return asdict(self)


# ── Persistence ───────────────────────────────────────────────────────

def _save_proposal(p: TradeProposal) -> None:
    os.makedirs(os.path.dirname(_PROPOSALS_FILE), exist_ok=True)
    with open(_PROPOSALS_FILE, "a") as f:
        f.write(json.dumps(p.to_dict(), ensure_ascii=False) + "\n")


def load_proposals(limit: int = 20, status_filter: Optional[str] = None) -> List[TradeProposal]:
    if not os.path.exists(_PROPOSALS_FILE):
        return []
    proposals = []
    try:
        with open(_PROPOSALS_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if status_filter and d.get("status") != status_filter:
                        continue
                    proposals.append(TradeProposal(**{
                        k: v for k, v in d.items()
                        if k in TradeProposal.__dataclass_fields__
                    }))
                except Exception:
                    pass
    except Exception as e:
        logger.warning("load_proposals error: %s", e)
    if limit:
        return proposals[-limit:]
    return proposals


def update_proposal_status(proposal_id: str, status: str) -> bool:
    if not os.path.exists(_PROPOSALS_FILE):
        return False
    lines = []
    found = False
    try:
        with open(_PROPOSALS_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if d.get("proposal_id") == proposal_id:
                        d["status"] = status
                        found = True
                    lines.append(json.dumps(d, ensure_ascii=False))
                except Exception:
                    lines.append(line)
        with open(_PROPOSALS_FILE, "w") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        logger.error("update_proposal_status error: %s", e)
    return found


# ── Core cycle functions ──────────────────────────────────────────────

def _observe_and_record(symbols: List[str]) -> Dict[str, Any]:
    """
    Step 1: evaluate market conditions for each symbol and record signals.
    """
    from connectors.etoro.market_observer import evaluate, ScenarioState
    from connectors.etoro.signal_recorder import record_from_scenario

    recorded = 0
    for symbol in symbols:
        for direction in ("buy", "sell"):
            try:
                result = evaluate(symbol=symbol, direction=direction)

                # Only record PRE_OPERABLE or OPERABLE (not NO_OPERABLE)
                if result.state == ScenarioState.NO_OPERABLE:
                    continue

                # Extract condition info from result
                cond_names = [c.name for c in result.conditions if c.met]

                # Don't duplicate: skip if a signal for this symbol+direction
                # was already recorded in the last 2 hours
                from connectors.etoro.signal_recorder import load_signals
                recent = load_signals()
                cutoff = time.time() - 7200  # 2h
                duplicate = any(
                    s.symbol == symbol.upper()
                    and s.direction == direction
                    and s.timestamp > cutoff
                    for s in recent
                )
                if duplicate:
                    logger.debug("OBSERVE skip duplicate %s %s", direction, symbol)
                    continue

                # Extract ATR and rel_volume from observer conditions
                atr = 0.0
                rel_vol = 1.0
                for c in result.conditions:
                    if c.name == "PRECIO_EN_ZONA":
                        atr = c.threshold / 1.5  # reverse: threshold = atr * 1.5
                    elif c.name == "VOLUMEN_RELATIVO":
                        rel_vol = c.value

                record_from_scenario(
                    symbol=symbol,
                    direction=direction,
                    price=result.price,
                    scenario_state=result.state.value,
                    conditions_met=result.conditions_met,
                    conditions_names=cond_names,
                    atr=atr,
                    rel_volume=rel_vol,
                    session=next(
                        (c.description for c in result.conditions
                         if c.name == "ALINEACION_TEMPORAL"), ""
                    ),
                    entry_price=result.suggested_entry,
                    invalidation_price=result.invalidation,
                )
                recorded += 1

            except Exception as e:
                logger.warning("observe_record error %s %s: %s", direction, symbol, e)

    return {"signals_recorded": recorded}


def _resolve_outcomes() -> Dict[str, Any]:
    """Step 2: resolve outcomes of pending signals."""
    from connectors.etoro.outcome_tracker import resolve_pending_signals
    return resolve_pending_signals()


def _update_patterns() -> Dict[str, Any]:
    """Step 3: rebuild pattern memory from resolved signals."""
    from connectors.etoro.pattern_memory import update_patterns_from_signals
    return update_patterns_from_signals()


def _generate_proposals(symbols: List[str]) -> List[TradeProposal]:
    """
    Step 4: for each symbol with a usable pattern matching current conditions,
    generate a proposal. Does NOT execute anything.
    """
    from connectors.etoro.market_observer import evaluate, ScenarioState
    from connectors.etoro.pattern_memory import get_patterns, _make_key

    usable_patterns = {p.pattern_key: p for p in get_patterns(usable_only=True)}
    if not usable_patterns:
        return []

    proposals: List[TradeProposal] = []
    ts = time.time()

    # Don't re-propose if a pending proposal for same symbol exists in last 4h
    recent_proposals = load_proposals(limit=50, status_filter="pending")
    recent_cutoff = ts - 14400  # 4h
    recently_proposed = {
        (p.symbol, p.direction) for p in recent_proposals
        if p.timestamp > recent_cutoff
    }

    for symbol in symbols:
        for direction in ("buy", "sell"):
            if (symbol.upper(), direction) in recently_proposed:
                continue
            try:
                result = evaluate(symbol=symbol, direction=direction)

                if result.state == ScenarioState.NO_OPERABLE:
                    continue

                # Check if there's a matching usable pattern
                cond_names = [c.name for c in result.conditions if c.met]
                session = next(
                    (c.description for c in result.conditions
                     if c.name == "ALINEACION_TEMPORAL"), ""
                )
                key = _make_key(symbol.upper(), direction, session, cond_names)

                pattern = usable_patterns.get(key)
                if not pattern:
                    # Try fallback: match without session specificity
                    for k, p in usable_patterns.items():
                        if p.symbol == symbol.upper() and p.direction == direction:
                            pattern = p
                            break

                if not pattern:
                    continue

                # Build stop-loss and take-profit from pattern stats
                sl_dist = result.price * (1.5 / 100)  # 1.5% SL
                tp_dist = result.price * (pattern.avg_win_pct / 100)

                if direction == "buy":
                    stop_loss   = round(result.price - sl_dist, 5)
                    take_profit = round(result.price + max(tp_dist, sl_dist * 1.5), 5)
                else:
                    stop_loss   = round(result.price + sl_dist, 5)
                    take_profit = round(result.price - max(tp_dist, sl_dist * 1.5), 5)

                pid = f"PROP-{symbol[:4].upper()}{direction[0].upper()}-{int(ts)%10000:04d}"
                reasoning = (
                    f"Patrón {pattern.confidence} ({pattern.n_total} señales): "
                    f"WR={pattern.win_rate:.0f}%, E={pattern.expectancy:+.3f}%. "
                    f"Condiciones activas: {', '.join(cond_names)}."
                )

                p = TradeProposal(
                    proposal_id=pid,
                    timestamp=ts,
                    symbol=symbol.upper(),
                    direction=direction,
                    entry_price=result.suggested_entry or result.price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    confidence=pattern.confidence,
                    win_rate=pattern.win_rate,
                    expectancy=pattern.expectancy,
                    pattern_n=pattern.n_total,
                    scenario_state=result.state.value,
                    conditions_met=result.conditions_met,
                    reasoning=reasoning,
                )
                _save_proposal(p)
                proposals.append(p)
                logger.info(
                    "[PROPOSAL] %s | %s %s @ %.5g | SL=%.5g TP=%.5g | "
                    "WR=%.0f%% E=%+.3f%%",
                    pid, direction.upper(), symbol.upper(),
                    p.entry_price, stop_loss, take_profit,
                    pattern.win_rate, pattern.expectancy,
                )

            except Exception as e:
                logger.warning("generate_proposal error %s %s: %s", direction, symbol, e)

    return proposals


# ── Main public functions ─────────────────────────────────────────────

def run_learning_cycle(symbols: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Execute one full learning cycle:
      1. Observe & record signals
      2. Resolve outcomes
      3. Update patterns
      4. Generate proposals

    Returns a summary dict.
    """
    if symbols is None:
        symbols = DEFAULT_WATCHLIST

    t0 = time.time()
    logger.info("[LEARN] Starting cycle for symbols: %s", symbols)

    r1 = _observe_and_record(symbols)
    r2 = _resolve_outcomes()
    r3 = _update_patterns()
    proposals = _generate_proposals(symbols)

    elapsed = round(time.time() - t0, 1)

    summary = {
        "elapsed_s":       elapsed,
        "signals_recorded": r1["signals_recorded"],
        "outcomes_resolved": r2.get("resolved", 0),
        "outcomes_wins":    r2.get("wins", 0),
        "outcomes_losses":  r2.get("losses", 0),
        "patterns_total":   r3.get("patterns", 0),
        "patterns_usable":  r3.get("usable", 0),
        "proposals_new":    len(proposals),
    }
    logger.info("[LEARN] Cycle complete in %.1fs: %s", elapsed, summary)
    return summary


def get_learning_status() -> str:
    """Return a formatted status panel for Telegram."""
    from connectors.etoro.signal_recorder import get_signal_stats
    from connectors.etoro.pattern_memory   import get_patterns

    sig_stats = get_signal_stats()
    patterns  = get_patterns()
    usable    = [p for p in patterns if p.is_usable]
    pending_props = load_proposals(limit=100, status_filter="pending")

    lines = [
        "🤖 Motor de Aprendizaje — Estado\n",
        f"📡 Señales totales:   {sig_stats['total']}",
        f"⏳ Pendientes:        {sig_stats['pending']}",
        f"✅ Wins:              {sig_stats['wins']}",
        f"❌ Losses:            {sig_stats['losses']}",
        f"⚪ Neutral/Expiradas: {sig_stats['neutral'] + sig_stats['expired']}",
        f"📈 Win rate global:   {sig_stats['win_rate']}%",
        "",
        f"🧠 Patrones totales: {len(patterns)}",
        f"⚡ Usables (≥{15} N, ≥55% WR): {len(usable)}",
        "",
        f"📋 Propuestas pendientes: {len(pending_props)}",
    ]

    if usable:
        lines.append("\nMejores patrones:")
        for p in sorted(usable, key=lambda x: x.expectancy, reverse=True)[:3]:
            lines.append(
                f"  {p.symbol} {p.direction.upper()} | "
                f"WR={p.win_rate:.0f}% E={p.expectancy:+.3f}% ({p.confidence})"
            )

    return "\n".join(lines)


def format_proposals_panel(limit: int = 5) -> str:
    """Format pending proposals for Telegram."""
    proposals = load_proposals(limit=limit, status_filter="pending")
    if not proposals:
        return "Sin propuestas pendientes. Ejecuta /vx etoro learn run para generar nuevas."

    lines = [f"📋 Propuestas activas ({len(proposals)}):\n"]
    for p in sorted(proposals, key=lambda x: x.timestamp, reverse=True):
        import datetime
        ts_str = datetime.datetime.utcfromtimestamp(p.timestamp).strftime("%m/%d %H:%M")
        lines.append(
            f"🔖 {p.proposal_id} [{ts_str}]\n"
            f"   {p.direction.upper()} {p.symbol} @ {p.entry_price:.5g}\n"
            f"   SL={p.stop_loss:.5g} | TP={p.take_profit:.5g}\n"
            f"   WR={p.win_rate:.0f}% | E={p.expectancy:+.3f}% | {p.confidence}\n"
            f"   {p.reasoning[:80]}..."
        )
    return "\n".join(lines)
