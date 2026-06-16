#!/usr/bin/env python3
"""
Vectrax Market Pattern Analyzer
==================================
Parses etoro_signals.jsonl and calculates win rate + expectancy
for each unique pattern (symbol + direction + conditions).

Usage:
  python scripts/analyze_patterns.py                  # full report
  python scripts/analyze_patterns.py --symbol TSLA    # filter by symbol
  python scripts/analyze_patterns.py --positive       # only positive patterns
  python scripts/analyze_patterns.py --negative       # only negative patterns
  python scripts/analyze_patterns.py --json           # output as JSON

Can run locally or inside Docker:
  docker exec vectrax-core python scripts/analyze_patterns.py

Creador: Mario Bravo Castro
Fecha: 2026-06-16
"""
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional

# Signal file path
SIGNALS_PATH = os.path.join(os.path.expanduser("~"), ".vectrax", "etoro_signals.jsonl")


@dataclass
class PatternStats:
    symbol: str
    direction: str
    conditions: str
    wins: int = 0
    losses: int = 0
    neutral: int = 0
    total: int = 0
    sum_pnl: float = 0.0

    @property
    def win_rate(self) -> float:
        resolved = self.wins + self.losses
        return (self.wins / resolved * 100) if resolved > 0 else 0.0

    @property
    def expectancy(self) -> float:
        resolved = self.wins + self.losses
        return (self.sum_pnl / resolved) if resolved > 0 else 0.0

    @property
    def confidence(self) -> str:
        if self.total >= 15 and self.win_rate >= 65 and self.expectancy > 0.3:
            return "HIGH"
        if self.total >= 15 and self.win_rate >= 55 and self.expectancy > 0:
            return "MEDIUM"
        return "LOW"

    @property
    def is_usable(self) -> bool:
        return self.total >= 15 and self.win_rate >= 55 and self.expectancy > 0

    @property
    def classification(self) -> str:
        if self.is_usable:
            return "USABLE"
        if self.total >= 15 and (self.win_rate < 55 or self.expectancy <= 0):
            return "NEGATIVE"
        if self.win_rate >= 55 and self.expectancy > 0:
            return "positive"
        if self.expectancy < 0 and (self.wins + self.losses) > 0:
            return "negative"
        return "building"


def load_signals(path: str = SIGNALS_PATH) -> List[Dict]:
    signals = []
    if not os.path.exists(path):
        print(f"File not found: {path}", file=sys.stderr)
        return signals
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    signals.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return signals


def build_patterns(signals: List[Dict]) -> Dict[str, PatternStats]:
    patterns: Dict[str, PatternStats] = {}

    for s in signals:
        symbol = s.get("symbol", "?").upper()
        direction = s.get("direction", "?")
        conditions = ",".join(sorted(s.get("conditions_names", s.get("conditions", []))))
        status = s.get("status", "pending")
        pnl = s.get("pnl_pct", 0.0) or 0.0

        key = f"{direction}_{symbol}_{conditions}"

        if key not in patterns:
            patterns[key] = PatternStats(
                symbol=symbol,
                direction=direction,
                conditions=conditions,
            )

        p = patterns[key]
        p.total += 1

        if status == "win":
            p.wins += 1
            p.sum_pnl += abs(pnl) if pnl else 0
        elif status == "loss":
            p.losses += 1
            p.sum_pnl -= abs(pnl) if pnl else 0
        elif status == "neutral":
            p.neutral += 1

    return patterns


def print_report(
    patterns: Dict[str, PatternStats],
    symbol_filter: str = "",
    show_only: str = "",
):
    print("=" * 75)
    print("  VECTRAX PATTERN ANALYSIS")
    print("  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    if symbol_filter:
        print("  Filter: %s" % symbol_filter)
    print("=" * 75)
    print()

    ranked = sorted(
        patterns.values(),
        key=lambda p: (p.is_usable, p.win_rate, p.expectancy),
        reverse=True,
    )

    # Apply filters
    if symbol_filter:
        ranked = [p for p in ranked if p.symbol == symbol_filter.upper()]
    if show_only == "positive":
        ranked = [p for p in ranked if p.classification in ("USABLE", "positive")]
    elif show_only == "negative":
        ranked = [p for p in ranked if p.classification in ("NEGATIVE", "negative")]

    if not ranked:
        print("  No patterns match the filter.")
        return

    print("%-6s %-6s | %5s | %8s | %3s | %3s | %3s | %3s | %10s | %s" % (
        "DIR", "SYM", "WR", "EXPECT", "N", "W", "L", "NTR", "CONFID.", "STATUS"))
    print("-" * 75)

    counts = {"USABLE": 0, "positive": 0, "negative": 0, "NEGATIVE": 0, "building": 0}

    for p in ranked:
        if p.total < 1:
            continue
        cls = p.classification
        counts[cls] = counts.get(cls, 0) + 1

        print("%-6s %-6s | %4.0f%% | %+7.3f%% | %3d | %3d | %3d | %3d | %10s | %s" % (
            p.direction.upper(), p.symbol, p.win_rate, p.expectancy,
            p.total, p.wins, p.losses, p.neutral, p.confidence, cls))

    print()
    print("SUMMARY: %d patterns" % len(ranked))
    print("  USABLE:   %d" % counts.get("USABLE", 0))
    print("  Positive: %d" % counts.get("positive", 0))
    print("  Building: %d" % counts.get("building", 0))
    print("  Negative: %d" % (counts.get("negative", 0) + counts.get("NEGATIVE", 0)))


def print_json(patterns: Dict[str, PatternStats]):
    output = []
    for key, p in sorted(patterns.items(), key=lambda x: -x[1].win_rate):
        output.append({
            "key": key,
            "symbol": p.symbol,
            "direction": p.direction,
            "conditions": p.conditions,
            "total": p.total,
            "wins": p.wins,
            "losses": p.losses,
            "neutral": p.neutral,
            "win_rate": round(p.win_rate, 1),
            "expectancy": round(p.expectancy, 3),
            "confidence": p.confidence,
            "classification": p.classification,
            "is_usable": p.is_usable,
        })
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    args = sys.argv[1:]

    symbol_filter = ""
    show_only = ""
    as_json = False

    i = 0
    while i < len(args):
        if args[i] == "--symbol" and i + 1 < len(args):
            symbol_filter = args[i + 1]
            i += 2
        elif args[i] == "--positive":
            show_only = "positive"
            i += 1
        elif args[i] == "--negative":
            show_only = "negative"
            i += 1
        elif args[i] == "--json":
            as_json = True
            i += 1
        else:
            i += 1

    signals = load_signals()
    if not signals:
        print("No signals found at %s" % SIGNALS_PATH)
        sys.exit(1)

    patterns = build_patterns(signals)

    if as_json:
        print_json(patterns)
    else:
        print_report(patterns, symbol_filter=symbol_filter, show_only=show_only)
