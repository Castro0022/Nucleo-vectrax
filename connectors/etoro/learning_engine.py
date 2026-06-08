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
# Note: eToro uses short names for crypto (BTC, ETH), not pairs (BTCUSD).
# EURUSD removed — eToro search returns instrument_id but rates fail.
DEFAULT_WATCHLIST = ["BTC", "ETH", "AAPL", "TSLA", "NVDA", "AMZN"]


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

def _feed_gravity(symbols: List[str]) -> int:
    """
    Step 5: Feed market observations into the gravitational universe.
    Each symbol gets a gravity record with domain='market'.
    Patterns with high win rates gain more gravitational mass.
    This makes market observations visible as stars in the universe.
    """
    fed = 0
    try:
        from core.learn.gravity_engine import get_gravity_index
        from connectors.etoro.pattern_memory import get_patterns
        from connectors.etoro.signal_recorder import get_signal_stats, load_signals

        gi = get_gravity_index()
        all_patterns = get_patterns()
        all_signals = load_signals(limit=500)

        for symbol in symbols:
            sym = symbol.upper()
            sym_patterns = [p for p in all_patterns if p.symbol == sym]
            sym_signals = [s for s in all_signals if s.symbol == sym]

            # Coherence score based on best pattern win rate
            best_wr = max((p.win_rate for p in sym_patterns), default=0) / 100
            n_signals = len(sym_signals)
            impact = "high" if best_wr >= 0.6 and n_signals >= 10 else (
                "medium" if n_signals >= 5 else "low"
            )

            # Record as gravity event — fingerprint per symbol
            fingerprint = f"market:{sym}"
            outcome = "observed"
            if sym_patterns:
                best = max(sym_patterns, key=lambda p: p.expectancy)
                outcome = f"WR={best.win_rate:.0f}% E={best.expectancy:+.3f}%"

            summary = (
                f"{sym}: {n_signals} signals, "
                f"{len(sym_patterns)} patterns"
            )

            gi.record_event(
                fingerprint=fingerprint,
                cc_score=best_wr,
                impact=impact,
                domain="market",
                intent=sym,
                outcome=outcome,
                summary=summary,
            )
            fed += 1

    except Exception as e:
        logger.debug("feed_gravity error: %s", e)
    return fed


def _auto_execute_proposals(proposals: List[TradeProposal]) -> int:
    """Auto-execute qualifying proposals if auto-executor is active."""
    if not proposals:
        return 0
    try:
        from connectors.etoro.auto_executor import (
            get_mode, AutoMode, execute_proposal, record_symbol_op,
        )
        from connectors.etoro.entry_validator import validate_entry

        mode = get_mode()
        if mode == AutoMode.OFF:
            return 0

        executed = 0
        for p in proposals:
            # Validate ALL entry conditions
            allowed, reasons = validate_entry(
                p.symbol, p.direction, p, mode=mode.value,
            )
            if not allowed:
                logger.info(
                    "[AUTO] Proposal %s BLOCKED: %s",
                    p.proposal_id, "; ".join(reasons[:3]),
                )
                continue

            # Execute
            result = execute_proposal(p.proposal_id)
            if result.get("success"):
                record_symbol_op(p.symbol)
                executed += 1
                logger.info(
                    "[AUTO] Proposal %s EXECUTED (%s): %s %s $%.0f",
                    p.proposal_id, mode.value.upper(),
                    p.direction.upper(), p.symbol,
                    result.get("amount_usd", 0),
                )
                # Log to observation ledger
                try:
                    from core.self_observation.observation_ledger import record
                    record(
                        domain="market",
                        obs_type="trade_executed",
                        summary=(
                            f"Operación ejecutada ({mode.value}): "
                            f"{p.direction.upper()} {p.symbol} "
                            f"${result.get('amount_usd', 0):.0f}"
                        ),
                        star_id=f"market:{p.symbol}",
                        evidence={
                            "proposal_id": p.proposal_id,
                            "mode": mode.value,
                            "direction": p.direction,
                            "amount": result.get("amount_usd"),
                        },
                    )
                except Exception:
                    pass
            else:
                logger.warning(
                    "[AUTO] Proposal %s execution failed: %s",
                    p.proposal_id, result.get("error", "?"),
                )

        return executed
    except Exception as e:
        logger.debug("auto_execute_proposals error: %s", e)
        return 0


def _check_positions() -> int:
    """Check open positions for exit conditions."""
    try:
        from connectors.etoro.position_manager import check_open_positions
        actions = check_open_positions()
        return len(actions)
    except Exception as e:
        logger.debug("check_positions error: %s", e)
        return 0


def run_learning_cycle(symbols: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Execute one full learning cycle:
      1. Observe & record signals
      2. Resolve outcomes
      3. Update patterns
      4. Generate proposals
      5. Feed observations into gravitational universe

    Returns a summary dict.
    """
    if symbols is None:
        symbols = DEFAULT_WATCHLIST

    t0 = time.time()

    # Determine which symbols have open markets right now
    try:
        from connectors.etoro.market_mode import get_open_symbols, get_active_mode
        open_syms = get_open_symbols(symbols)
        mode = get_active_mode(symbols)
    except Exception:
        open_syms = symbols  # fallback: treat all as open
        mode = "market"

    logger.info("[LEARN] Starting cycle | mode=%s | open=%s/%s %s",
                mode, len(open_syms), len(symbols),
                open_syms if open_syms else "(all closed)")

    # Step 1: Observe only open markets (no noise from closed markets)
    r1 = _observe_and_record(open_syms) if open_syms else {"signals_recorded": 0}

    # Steps 2-3: Always run (resolve outcomes + rebuild patterns from history)
    r2 = _resolve_outcomes()
    r3 = _update_patterns()

    # Step 4: Only generate proposals for open markets
    proposals = _generate_proposals(open_syms) if open_syms else []

    # Step 5: Only feed gravity for open markets
    gravity_fed = _feed_gravity(open_syms) if open_syms else 0

    # Step 6: check for critical convergences and alert
    alerts_sent = 0
    try:
        from core.learn.gravity_engine import send_convergence_alerts
        alerts_sent = send_convergence_alerts()
        if alerts_sent:
            logger.info("[LEARN] %d convergence alerts sent", alerts_sent)
    except Exception as e:
        logger.debug("convergence alerts error: %s", e)

    # Step 7: Auto-execute qualifying proposals (PAPER or LIVE)
    auto_executed = _auto_execute_proposals(proposals)

    # Step 8: Check open positions for exit conditions
    positions_closed = _check_positions()

    elapsed = round(time.time() - t0, 1)

    summary = {
        "mode":            mode,
        "open_symbols":    len(open_syms),
        "elapsed_s":       elapsed,
        "signals_recorded": r1["signals_recorded"],
        "outcomes_resolved": r2.get("resolved", 0),
        "outcomes_wins":    r2.get("wins", 0),
        "outcomes_losses":  r2.get("losses", 0),
        "patterns_total":   r3.get("patterns", 0),
        "patterns_usable":  r3.get("usable", 0),
        "proposals_new":    len(proposals),
        "gravity_fed":     gravity_fed,
        "alerts_sent":     alerts_sent,
        "auto_executed":   auto_executed,
        "positions_closed": positions_closed,
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
