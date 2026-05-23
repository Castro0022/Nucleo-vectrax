#!/usr/bin/env python3
"""
Vectrax Router Performance Report
====================================
Generates a performance report from the router decision ledger.

Usage:
    # Local (reads data/router_feedback.jsonl directly)
    python3 scripts/router_report.py

    # Last N records only
    python3 scripts/router_report.py --last 200

    # Since a specific timestamp or date
    python3 scripts/router_report.py --since 2026-05-23

    # Compare post-deploy vs pre-deploy
    python3 scripts/router_report.py --since 2026-05-23T00:55:00 --compare

    # JSON output (for piping to other tools)
    python3 scripts/router_report.py --json

Co-Authored-By: Oz <oz-agent@warp.dev>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LEDGER = os.path.join(_PROJECT_ROOT, "data", "router_feedback.jsonl")

# Also check the container path (for running inside Docker)
_CONTAINER_LEDGER = "/app/data/router_feedback.jsonl"


def load_records(path: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return records
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def parse_since(value: str) -> float:
    """Parse --since value as epoch or ISO date."""
    try:
        return float(value)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(value, fmt).timestamp()
        except ValueError:
            continue
    print(f"Error: cannot parse --since '{value}'", file=sys.stderr)
    sys.exit(1)


def analyze(records: List[Dict], label: str = "") -> Dict[str, Any]:
    """Analyze a set of records and return structured metrics."""
    if not records:
        return {"label": label, "total": 0}

    n = len(records)

    # Classification methods
    methods = Counter(r.get("classification_method", "?") for r in records)

    # Intent distribution
    intents = Counter(r.get("intent", "?") for r in records)

    # Success rate
    successes = sum(1 for r in records if r.get("success"))
    fallbacks = sum(1 for r in records if r.get("used_fallback"))

    # Quality scores
    quality = Counter(r.get("quality_score", "?") for r in records)

    # Conflicts: regex_fallback where semantic disagrees
    conflicts = []
    for r in records:
        if (
            r.get("classification_method") == "regex_fallback"
            and r.get("semantic_would_have_been")
        ):
            key = f"{r['semantic_would_have_been']}→{r['intent']}"
            conflicts.append(key)
    conflict_counts = Counter(conflicts)

    # Memory rescues (new classification method)
    rescues = sum(
        1 for r in records
        if r.get("classification_method") == "semantic_memory_rescue"
    )

    # Latency
    latencies = [r.get("routing_latency_ms", 0) for r in records if r.get("routing_latency_ms")]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    p95_latency = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0

    # Semantic confidence distribution
    confs = [r.get("semantic_confidence", 0) for r in records if r.get("semantic_confidence")]
    avg_conf = sum(confs) / len(confs) if confs else 0

    # Time range
    timestamps = [r.get("timestamp", 0) for r in records if r.get("timestamp")]
    ts_min = min(timestamps) if timestamps else 0
    ts_max = max(timestamps) if timestamps else 0

    return {
        "label": label,
        "total": n,
        "time_range": {
            "from": datetime.fromtimestamp(ts_min).isoformat() if ts_min else "?",
            "to": datetime.fromtimestamp(ts_max).isoformat() if ts_max else "?",
        },
        "classification_methods": {k: {"count": v, "pct": round(100 * v / n)} for k, v in methods.most_common()},
        "regex_fallback_pct": round(100 * methods.get("regex_fallback", 0) / n),
        "intents": {k: v for k, v in intents.most_common()},
        "success_rate": round(100 * successes / n),
        "fallback_rate": round(100 * fallbacks / n),
        "quality": {k: v for k, v in quality.most_common()},
        "conflicts": {k: v for k, v in conflict_counts.most_common(10)},
        "memory_rescues": rescues,
        "latency": {
            "avg_ms": round(avg_latency, 1),
            "p95_ms": round(p95_latency, 1),
        },
        "semantic_confidence_avg": round(avg_conf, 3),
    }


def print_report(analysis: Dict[str, Any]) -> None:
    """Print a human-readable report."""
    label = analysis.get("label", "")
    total = analysis["total"]
    if total == 0:
        print(f"  {label}: no records")
        return

    tr = analysis["time_range"]
    print(f"{'═' * 56}")
    print(f"  VECTRAX ROUTER REPORT{f' — {label}' if label else ''}")
    print(f"{'═' * 56}")
    print(f"  Records: {total}  ({tr['from']} → {tr['to']})")
    print()

    # Classification methods
    print("  Classification Methods:")
    for method, data in analysis["classification_methods"].items():
        bar = "█" * (data["pct"] // 3) + "░" * ((100 - data["pct"]) // 3)
        print(f"    {method:28s} {data['count']:4d} ({data['pct']:2d}%) {bar}")

    rfp = analysis["regex_fallback_pct"]
    rescues = analysis["memory_rescues"]
    print(f"\n  regex_fallback: {rfp}%{'  ⚠ HIGH' if rfp > 30 else '  ✓ OK' if rfp < 20 else ''}")
    if rescues:
        print(f"  memory_rescues: {rescues}")

    # Conflicts
    conflicts = analysis.get("conflicts", {})
    if conflicts:
        print(f"\n  Conflicts (semantic vs regex):")
        for conflict, count in conflicts.items():
            print(f"    {conflict}: {count}x")
    else:
        print(f"\n  Conflicts: none ✓")

    # Intents
    print(f"\n  Intent Distribution:")
    for intent, count in analysis["intents"].items():
        pct = 100 * count // total
        print(f"    {intent:12s} {count:4d} ({pct}%)")

    # Quality
    print(f"\n  Quality Scores:")
    for score, count in analysis["quality"].items():
        pct = 100 * count // total
        print(f"    {score:20s} {count:4d} ({pct}%)")

    # Stats
    print(f"\n  Success rate:    {analysis['success_rate']}%")
    print(f"  Fallback rate:   {analysis['fallback_rate']}%")
    print(f"  Avg confidence:  {analysis['semantic_confidence_avg']}")
    lat = analysis["latency"]
    print(f"  Latency:         avg={lat['avg_ms']}ms  p95={lat['p95_ms']}ms")
    print()


def print_comparison(before: Dict, after: Dict) -> None:
    """Print a before/after comparison."""
    print(f"\n{'═' * 56}")
    print(f"  COMPARISON: {before['label']} → {after['label']}")
    print(f"{'═' * 56}")

    if before["total"] == 0 or after["total"] == 0:
        print("  Insufficient data for comparison.")
        return

    metrics = [
        ("regex_fallback", "regex_fallback_pct", "%", True),
        ("success_rate", "success_rate", "%", False),
        ("semantic_confidence", "semantic_confidence_avg", "", False),
        ("memory_rescues", "memory_rescues", "", False),
    ]
    for name, key, unit, lower_better in metrics:
        b = before.get(key, 0)
        a = after.get(key, 0)
        delta = a - b
        direction = "↓" if delta < 0 else "↑" if delta > 0 else "="
        good = (delta < 0 and lower_better) or (delta > 0 and not lower_better)
        marker = "✓" if good and delta != 0 else "✗" if not good and delta != 0 else " "
        print(f"  {marker} {name:25s} {b}{unit} → {a}{unit}  ({direction}{abs(delta)}{unit})")

    # Conflict comparison
    bc = sum(before.get("conflicts", {}).values())
    ac = sum(after.get("conflicts", {}).values())
    delta = ac - bc
    marker = "✓" if delta < 0 else "✗" if delta > 0 else " "
    print(f"  {marker} {'total conflicts':25s} {bc} → {ac}  ({'↓' if delta < 0 else '↑'}{abs(delta)})")
    print()


def main():
    parser = argparse.ArgumentParser(description="Vectrax Router Performance Report")
    parser.add_argument("--last", type=int, default=0, help="Analyze only last N records")
    parser.add_argument("--since", type=str, default="", help="Analyze records since timestamp or date")
    parser.add_argument("--compare", action="store_true", help="Compare post-since vs pre-since")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--ledger", type=str, default="", help="Path to router_feedback.jsonl")
    args = parser.parse_args()

    # Find ledger
    ledger_path = args.ledger or DEFAULT_LEDGER
    if not os.path.exists(ledger_path):
        ledger_path = _CONTAINER_LEDGER
    if not os.path.exists(ledger_path):
        print(f"Ledger not found at {DEFAULT_LEDGER} or {_CONTAINER_LEDGER}", file=sys.stderr)
        sys.exit(1)

    all_records = load_records(ledger_path)
    if not all_records:
        print("No records in ledger.", file=sys.stderr)
        sys.exit(1)

    if args.since:
        cutoff = parse_since(args.since)
        post = [r for r in all_records if r.get("timestamp", 0) >= cutoff]
        pre = [r for r in all_records if r.get("timestamp", 0) < cutoff]

        if args.compare:
            pre_200 = pre[-200:] if len(pre) > 200 else pre
            analysis_pre = analyze(pre_200, label=f"pre ({len(pre_200)} records)")
            analysis_post = analyze(post, label=f"post ({len(post)} records)")

            if args.json:
                print(json.dumps({"pre": analysis_pre, "post": analysis_post}, indent=2))
            else:
                print_report(analysis_pre)
                print_report(analysis_post)
                print_comparison(analysis_pre, analysis_post)
        else:
            analysis = analyze(post, label=f"since {args.since}")
            if args.json:
                print(json.dumps(analysis, indent=2))
            else:
                print_report(analysis)

    elif args.last:
        records = all_records[-args.last:]
        analysis = analyze(records, label=f"last {args.last}")
        if args.json:
            print(json.dumps(analysis, indent=2))
        else:
            print_report(analysis)

    else:
        # Default: last 200
        records = all_records[-200:]
        analysis = analyze(records, label=f"last 200 of {len(all_records)}")
        if args.json:
            print(json.dumps(analysis, indent=2))
        else:
            print_report(analysis)


if __name__ == "__main__":
    main()
