#!/usr/bin/env python3
"""Backfill canonical `convergences` from the legacy `convergence_events`
ledger, WITHOUT touching or rewriting that legacy table.

Dry-run by default (prints a report only). Pass --apply to actually write
`convergences` + `convergence_event_map` rows.

Usage:
    python3 scripts/backfill_canonical_convergences.py            # dry-run
    python3 scripts/backfill_canonical_convergences.py --apply    # write
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.learn.convergence_registry import (  # noqa: E402
    NormalizedEntity, compute_convergence_id, connect, normalize_entity_id,
)

LEGACY_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "convergence_history.db",
)


def _live_fingerprints() -> Dict[str, str]:
    try:
        from core.learn.gravity_engine import get_gravity_index
        return {
            fp: rec.domain
            for fp, rec in get_gravity_index().load_raw().items()
        }
    except Exception as exc:
        print(f"[warn] could not load live gravity index: {exc}")
        return {}


def _read_legacy_events(db_path: str) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(db_path, timeout=3)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM convergence_events ORDER BY timestamp ASC, id ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


class CanonicalGroup:
    __slots__ = (
        "entity_a", "entity_b", "first_seen", "last_seen",
        "confirmation_count", "combined_cc", "combined_hits",
        "relationship_type", "status", "dissolved_at", "event_ids",
    )

    def __init__(self, entity_a: NormalizedEntity, entity_b: NormalizedEntity):
        self.entity_a = entity_a
        self.entity_b = entity_b
        self.first_seen: Optional[float] = None
        self.last_seen: Optional[float] = None
        self.confirmation_count = 0
        self.combined_cc = 0.0
        self.combined_hits = 0
        self.relationship_type = ""
        self.status = "dissolved"
        self.dissolved_at: Optional[float] = None
        self.event_ids: List[int] = []


def backfill(
    legacy_db_path: str = LEGACY_DB,
    live_fingerprints: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, CanonicalGroup], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Aggregate legacy events into canonical groups.

    Returns (groups_by_convergence_id, ambiguous_events, event_map_rows).
    Read-only against the legacy table.
    """
    live_fingerprints = live_fingerprints if live_fingerprints is not None else _live_fingerprints()
    events = _read_legacy_events(legacy_db_path)
    context_ids = {
        str(e.get(key, "")) for e in events for key in ("star_a", "star_b")
    }

    groups: Dict[str, CanonicalGroup] = {}
    ambiguous: List[Dict[str, Any]] = []
    event_map: List[Dict[str, Any]] = []

    for event in events:
        raw_domains = []
        try:
            import json
            raw_domains = json.loads(event.get("domains") or "[]")
        except Exception:
            raw_domains = []
        hint_a = raw_domains[0] if len(raw_domains) > 0 and raw_domains[0] else None
        hint_b = raw_domains[1] if len(raw_domains) > 1 and raw_domains[1] else None

        raw_a = str(event.get("star_a", ""))
        raw_b = str(event.get("star_b", ""))
        entity_a = normalize_entity_id(raw_a, "star", hint_a, live_fingerprints, context_ids)
        entity_b = normalize_entity_id(raw_b, "star", hint_b, live_fingerprints, context_ids)

        if (
            entity_a.resolution == "ambiguous_corrupted_fragment"
            or entity_b.resolution == "ambiguous_corrupted_fragment"
        ):
            ambiguous.append({
                "event_id": event["id"], "star_a": raw_a, "star_b": raw_b,
                "reason": "ambiguous_corrupted_fragment",
                "timestamp": event.get("timestamp"),
            })
            event_map.append({
                "event_id": event["id"], "convergence_id": None, "ambiguous": 1,
            })
            continue

        first, second = sorted((entity_a, entity_b), key=lambda e: e.sort_key)
        convergence_id = compute_convergence_id(first, second)
        group = groups.get(convergence_id)
        if group is None:
            group = CanonicalGroup(first, second)
            groups[convergence_id] = group

        ts = float(event.get("timestamp") or 0)
        group.first_seen = ts if group.first_seen is None else min(group.first_seen, ts)
        group.last_seen = ts if group.last_seen is None else max(group.last_seen, ts)
        group.event_ids.append(event["id"])

        if event.get("event") == "birth":
            group.confirmation_count += 1
            group.combined_cc = float(event.get("combined_cc") or 0)
            group.combined_hits = int(event.get("combined_hits") or 0)
            if event.get("type"):
                group.relationship_type = event["type"]
            group.status = "active"
            group.dissolved_at = None
        elif event.get("event") == "dissolution":
            group.status = "dissolved"
            group.dissolved_at = ts

        event_map.append({
            "event_id": event["id"], "convergence_id": convergence_id, "ambiguous": 0,
        })

    return groups, ambiguous, event_map


def apply_backfill(
    groups: Dict[str, CanonicalGroup],
    event_map: List[Dict[str, Any]],
    db_path: str = LEGACY_DB,
) -> None:
    conn = connect(db_path)
    try:
        for convergence_id, group in groups.items():
            existing = conn.execute(
                "SELECT confirmation_count, first_seen FROM convergences WHERE convergence_id=?",
                (convergence_id,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO convergences
                    (convergence_id, entity_a_id, entity_a_type, entity_b_id,
                     entity_b_type, domain_a, domain_b, relationship_type,
                     first_seen, last_seen, confirmation_count, combined_cc,
                     combined_hits, status, dissolved_at, created_from)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        convergence_id,
                        group.entity_a.canonical_id, group.entity_a.entity_type,
                        group.entity_b.canonical_id, group.entity_b.entity_type,
                        group.entity_a.domain, group.entity_b.domain,
                        group.relationship_type, group.first_seen, group.last_seen,
                        group.confirmation_count, group.combined_cc,
                        group.combined_hits, group.status, group.dissolved_at,
                        "backfill",
                    ),
                )
            else:
                conn.execute(
                    """UPDATE convergences SET
                        first_seen = MIN(first_seen, ?),
                        confirmation_count = confirmation_count + ?
                    WHERE convergence_id=?""",
                    (group.first_seen, group.confirmation_count, convergence_id),
                )
        for row in event_map:
            conn.execute(
                """INSERT OR REPLACE INTO convergence_event_map
                (event_id, convergence_id, ambiguous) VALUES (?,?,?)""",
                (row["event_id"], row["convergence_id"], row["ambiguous"]),
            )
        conn.commit()
    finally:
        conn.close()


def print_report(
    groups: Dict[str, CanonicalGroup],
    ambiguous: List[Dict[str, Any]],
    total_events: int,
) -> None:
    active = sum(1 for g in groups.values() if g.status == "active")
    dissolved = len(groups) - active
    total_confirmations = sum(g.confirmation_count for g in groups.values())
    domain_pairs: Dict[Tuple[str, str], int] = defaultdict(int)
    for g in groups.values():
        pair = tuple(sorted((g.entity_a.domain, g.entity_b.domain)))
        domain_pairs[pair] += 1

    print("=" * 70)
    print("CANONICAL CONVERGENCE BACKFILL REPORT")
    print("=" * 70)
    print(f"Legacy events read (unchanged, read-only): {total_events}")
    print(f"Canonical convergences produced:            {len(groups)}")
    print(f"  active:                                   {active}")
    print(f"  dissolved:                                {dissolved}")
    print(f"Total confirmations (sum across all):        {total_confirmations}")
    print(f"Ambiguous events (NOT merged):                {len(ambiguous)}")
    print()
    print("Domain pairs covered:")
    for pair, count in sorted(domain_pairs.items(), key=lambda kv: -kv[1]):
        print(f"  {pair[0]!r} <-> {pair[1]!r}: {count}")
    print()
    if ambiguous:
        print("AMBIGUOUS — NOT MERGED (examples, up to 15):")
        for a in ambiguous[:15]:
            print(
                f"  event_id={a['event_id']} star_a={a['star_a']!r} "
                f"star_b={a['star_b']!r} reason={a['reason']}"
            )
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write results (default: dry-run)")
    parser.add_argument("--db-path", default=LEGACY_DB, help="Path to convergence_history.db")
    args = parser.parse_args()

    groups, ambiguous, event_map = backfill(args.db_path)
    total_events = len(_read_legacy_events(args.db_path))
    print_report(groups, ambiguous, total_events)

    if args.apply:
        print("\nApplying backfill (writing convergences + convergence_event_map)...")
        apply_backfill(groups, event_map, args.db_path)
        print("Done. Legacy convergence_events table was not modified.")
    else:
        print("\nDry-run only — no writes performed. Re-run with --apply to write.")


if __name__ == "__main__":
    main()
