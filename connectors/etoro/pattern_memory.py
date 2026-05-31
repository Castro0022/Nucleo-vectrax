"""
Vectrax eToro — Pattern Memory
================================
Builds statistical memory from resolved signals.

A "pattern" is a combination of:
  - symbol (e.g. BTCUSD)
  - direction (buy / sell)
  - session (London, New York, Asia, Overlap)
  - conditions_key: sorted list of condition names that were met

For each pattern we track:
  - sample size (N)
  - win rate %
  - avg return on win
  - avg return on loss
  - expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)
  - confidence level: LOW (N<10), MEDIUM (N<30), HIGH (N≥30)

Persistence: ~/.vectrax/etoro_patterns.json
Only patterns with N >= MIN_SAMPLE are considered usable for proposals.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.etoro.pattern_memory")

_PATTERNS_FILE = os.path.join(
    os.path.expanduser("~"), ".vectrax", "etoro_patterns.json"
)

MIN_SAMPLE        = 5    # minimum signals per pattern to consider it meaningful
USABLE_SAMPLE     = 15   # minimum for a pattern to generate proposals
USABLE_WIN_RATE   = 55.0 # minimum win rate % for a pattern to be considered actionable


# ── Pattern dataclass ─────────────────────────────────────────────────

@dataclass
class PatternStats:
    pattern_key:     str
    symbol:          str
    direction:       str
    session:         str
    conditions_key:  str   # sorted condition names joined with "|"

    # Statistics
    n_total:         int = 0
    n_wins:          int = 0
    n_losses:        int = 0
    n_neutral:       int = 0
    n_expired:       int = 0

    avg_win_pct:     float = 0.0
    avg_loss_pct:    float = 0.0
    expectancy:      float = 0.0

    # Meta
    last_updated:    float = 0.0
    first_seen:      float = 0.0

    @property
    def win_rate(self) -> float:
        decisive = self.n_wins + self.n_losses
        if decisive == 0:
            return 0.0
        return round(self.n_wins / decisive * 100, 1)

    @property
    def confidence(self) -> str:
        if self.n_total >= 30:
            return "HIGH"
        if self.n_total >= 10:
            return "MEDIUM"
        return "LOW"

    @property
    def is_usable(self) -> bool:
        return (
            self.n_total >= USABLE_SAMPLE
            and self.win_rate >= USABLE_WIN_RATE
            and self.expectancy > 0
        )

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["win_rate"]   = self.win_rate
        d["confidence"] = self.confidence
        d["is_usable"]  = self.is_usable
        return d


# ── Persistence ───────────────────────────────────────────────────────

def _load_patterns() -> Dict[str, PatternStats]:
    if not os.path.exists(_PATTERNS_FILE):
        return {}
    try:
        with open(_PATTERNS_FILE) as f:
            raw = json.load(f)
        return {
            k: PatternStats(**{
                fld: v for fld, v in d.items()
                if fld in PatternStats.__dataclass_fields__
            })
            for k, d in raw.items()
        }
    except Exception as e:
        logger.warning("load_patterns error: %s", e)
        return {}


def _save_patterns(patterns: Dict[str, PatternStats]) -> None:
    try:
        os.makedirs(os.path.dirname(_PATTERNS_FILE), exist_ok=True)
        with open(_PATTERNS_FILE, "w") as f:
            json.dump(
                {k: p.to_dict() for k, p in patterns.items()},
                f, indent=2, ensure_ascii=False,
            )
    except Exception as e:
        logger.error("save_patterns error: %s", e)


def _make_key(symbol: str, direction: str, session: str, conditions_names: List[str]) -> str:
    conds = "|".join(sorted(conditions_names))
    # Normalize session to first word (e.g. "Overlap London+New York" → "Overlap")
    sess = session.split()[0] if session else "Unknown"
    return f"{symbol}:{direction}:{sess}:{conds}"


# ── Main update function ──────────────────────────────────────────────

def update_patterns_from_signals() -> Dict[str, Any]:
    """
    Rebuild pattern statistics from ALL resolved signals.
    Called after outcome_tracker resolves new outcomes.
    Returns summary of patterns updated.
    """
    from connectors.etoro.signal_recorder import load_signals, SignalStatus

    all_signals = load_signals()
    resolved = [
        s for s in all_signals
        if s.status != SignalStatus.PENDING.value
    ]

    if not resolved:
        return {"patterns": 0, "usable": 0}

    # Group signals by pattern key
    groups: Dict[str, List] = {}
    for sig in resolved:
        key = _make_key(sig.symbol, sig.direction, sig.session, sig.conditions_names)
        groups.setdefault(key, []).append(sig)

    patterns: Dict[str, PatternStats] = {}
    now = time.time()

    for key, sigs in groups.items():
        first_sig = sigs[0]
        sess = first_sig.session.split()[0] if first_sig.session else "Unknown"
        conds = "|".join(sorted(first_sig.conditions_names))

        stats = PatternStats(
            pattern_key=key,
            symbol=first_sig.symbol,
            direction=first_sig.direction,
            session=sess,
            conditions_key=conds,
            first_seen=first_sig.timestamp,
            last_updated=now,
        )

        win_returns  = []
        loss_returns = []

        for sig in sigs:
            stats.n_total += 1
            if sig.status == SignalStatus.WIN.value:
                stats.n_wins += 1
                if sig.return_pct is not None:
                    win_returns.append(abs(sig.return_pct))
            elif sig.status == SignalStatus.LOSS.value:
                stats.n_losses += 1
                if sig.return_pct is not None:
                    loss_returns.append(abs(sig.return_pct))
            elif sig.status == "neutral":
                stats.n_neutral += 1
            elif sig.status == "expired":
                stats.n_expired += 1

        stats.avg_win_pct  = round(sum(win_returns)  / max(1, len(win_returns)),  3)
        stats.avg_loss_pct = round(sum(loss_returns) / max(1, len(loss_returns)), 3)

        decisive = stats.n_wins + stats.n_losses
        if decisive > 0:
            wr = stats.n_wins / decisive
            stats.expectancy = round(
                wr * stats.avg_win_pct - (1 - wr) * stats.avg_loss_pct, 3
            )

        patterns[key] = stats

    _save_patterns(patterns)

    usable = sum(1 for p in patterns.values() if p.is_usable)
    logger.info(
        "[PATTERNS] Updated %d patterns (%d usable) from %d signals",
        len(patterns), usable, len(resolved),
    )
    return {"patterns": len(patterns), "usable": usable}


def get_patterns(usable_only: bool = False) -> List[PatternStats]:
    """Return all patterns, optionally filtered to usable ones."""
    patterns = _load_patterns()
    result = list(patterns.values())
    if usable_only:
        result = [p for p in result if p.is_usable]
    return sorted(result, key=lambda p: p.expectancy, reverse=True)


def get_best_patterns(limit: int = 5) -> List[PatternStats]:
    """Return top patterns by expectancy with sufficient sample."""
    patterns = [p for p in _load_patterns().values() if p.n_total >= MIN_SAMPLE]
    return sorted(patterns, key=lambda p: p.expectancy, reverse=True)[:limit]


def format_patterns_panel(limit: int = 8) -> str:
    """Format pattern statistics as a Telegram message."""
    patterns = get_patterns()
    if not patterns:
        return "Sin patrones todavía. Se necesitan señales resueltas para construir memoria."

    usable = [p for p in patterns if p.is_usable]
    lines  = [
        f"🧠 Memoria de patrones — {len(patterns)} total, {len(usable)} usables\n"
    ]

    for p in patterns[:limit]:
        usable_tag = " ⚡" if p.is_usable else ""
        lines.append(
            f"{usable_tag}{'✅' if p.win_rate >= 55 else '⚠️'} "
            f"{p.symbol} {p.direction.upper()} | {p.session}\n"
            f"   N={p.n_total} | WR={p.win_rate:.0f}% | "
            f"E={p.expectancy:+.3f}% | {p.confidence}"
        )
    return "\n".join(lines)
