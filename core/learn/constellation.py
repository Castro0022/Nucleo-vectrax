"""
Vectrax Constellation Engine
==============================
Law 2 — Compression: when ≥ COMPACTION_THRESHOLD similar events accumulate,
they are folded into a single ConstellationRecord.

Persistence: ``vault/constellations/<id>.json``
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

from core.learn import VAULT_DIR
from core.learn.schemas import ConstellationRecord, GravityRecord

CONSTELLATIONS_DIR = os.path.join(VAULT_DIR, "constellations")
COMPACTION_THRESHOLD = 10_000  # minimum events to form a constellation


def _ensure_dir() -> None:
    os.makedirs(CONSTELLATIONS_DIR, exist_ok=True)


def _constellation_id(domain: str, intent: str) -> str:
    """Stable ID for a constellation key."""
    raw = f"{domain}:{intent}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _save_constellation(c: ConstellationRecord) -> None:
    _ensure_dir()
    path = os.path.join(CONSTELLATIONS_DIR, f"{c.constellation_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(c.to_dict(), f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_constellation(cid: str) -> Optional[ConstellationRecord]:
    path = os.path.join(CONSTELLATIONS_DIR, f"{cid}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return ConstellationRecord.from_dict(json.load(f))
    except (json.JSONDecodeError, OSError):
        return None


def list_constellations() -> List[ConstellationRecord]:
    _ensure_dir()
    results: List[ConstellationRecord] = []
    for fname in os.listdir(CONSTELLATIONS_DIR):
        if fname.endswith(".json"):
            cid = fname[:-5]
            c = load_constellation(cid)
            if c:
                results.append(c)
    return results


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------

def compact(index) -> List[ConstellationRecord]:
    """
    Scan the gravity index for groups of ≥ COMPACTION_THRESHOLD events
    sharing the same (domain, intent).  For each qualifying group:

    1. Create / update a ConstellationRecord with aggregate stats.
    2. Tag each member GravityRecord with the constellation_id.
    3. Persist the constellation to vault/constellations/.

    Parameters
    ----------
    index : GravityIndex

    Returns
    -------
    List of new / updated ConstellationRecords.
    """
    records = index.load_raw()

    # Group by (domain, intent)
    groups: Dict[str, List[GravityRecord]] = defaultdict(list)
    for rec in records.values():
        key = f"{rec.domain}:{rec.intent}"
        groups[key].append(rec)

    created: List[ConstellationRecord] = []

    for key, members in groups.items():
        if len(members) < COMPACTION_THRESHOLD:
            continue

        domain, intent = key.split(":", 1)
        cid = _constellation_id(domain, intent)

        total_hits = sum(m.hits for m in members)
        cc_vals = [m.cc_score for m in members]
        first_dates = [m.first_seen for m in members]
        last_dates = [m.last_seen for m in members]

        elapsed_days = max(
            (datetime.now(timezone.utc)
             - datetime.fromisoformat(min(first_dates))).total_seconds() / 86400,
            0.01,
        ) if first_dates else 1.0

        constellation = ConstellationRecord(
            constellation_id=cid,
            pattern_fingerprints=[m.fingerprint for m in members],
            total_events=len(members),
            frequency=round(total_hits / elapsed_days, 4),
            variance=round(statistics.variance(cc_vals), 4) if len(cc_vals) > 1 else 0.0,
            domain=domain,
            intent=intent,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        _save_constellation(constellation)

        # Tag members
        for m in members:
            m.constellation_id = cid

        created.append(constellation)

    # Persist updated records (constellation_id tags)
    if created:
        index.update_records(records)

    return created
