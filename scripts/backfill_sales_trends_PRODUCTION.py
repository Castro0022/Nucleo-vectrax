#!/usr/bin/env python3
"""
scripts/backfill_sales_trends_PRODUCTION.py — REAL production backfill
==========================================================================
Loads the combined, deduplicated 2-year Online Retail II dataset directly
into the REAL production gravity index (``~/.vectrax/gravity_index.json``
by default — the exact path the live server reads/writes via
``core.learn.gravity_engine.GRAVITY_INDEX_PATH``).

This is NOT the Stage 3 dry run (``scripts/backfill_sales_trends_dryrun.py``,
which only ever touches an isolated temp file). Running this script
permanently adds ~28k+ sales_trends stars to production. Per Gravity's
Law 4 (no deletion), they cannot be removed afterward except by manual
edit of the JSON file.

Critical safety property — READ THIS BEFORE MODIFYING
--------------------------------------------------------
``scripts/calibrate_sales_trends.py::_BulkGravityIndex`` (reused by the
Stage 3 dry run) always starts its in-memory cache EMPTY. That is correct
and safe for a throwaway temp path that doesn't exist yet. It would be
CATASTROPHIC here: flushing an empty-seeded cache to the real production
path would silently overwrite/discard every pre-existing star (every
domain, every user, everything Vectrax has ever recorded) with nothing
but the new sales_trends batch.

``_BulkGravityIndexSeeded`` below fixes this: it loads whatever already
exists on disk at ``path`` into the cache ONCE at construction time
(via the real ``GravityIndex._load()``), before any new event is
recorded. All pre-existing fingerprints are preserved; only new/changed
fingerprints are added or updated. This class must NEVER be pointed at a
path where "start empty" is the intended behavior — use the Stage 2/3
``_BulkGravityIndex`` for that.

Safety checklist this script enforces:
  1. Writes a timestamped backup of the current production file before
     touching anything.
  2. Deduplicates the two-sheet overlap via the exact-multiset method
     already validated in Stage 3 (``deduplicate_overlap`` — content-based,
     not date-based; see scripts/backfill_sales_trends_dryrun.py).
  3. Seeds from the real existing file (see above) — nothing pre-existing
     is discarded.
  4. Reports before/after counts, including an explicit check that every
     pre-existing (non-sales_trends) fingerprint is still present after
     the flush, before declaring success.

Run this ONLY while the live service is stopped (avoids a concurrent
writer racing with this script's single end-of-run flush). See the
accompanying runbook in the PR/conversation for the stop/backfill/restart
sequence.

Usage:
    python scripts/backfill_sales_trends_PRODUCTION.py --xlsx-path /path/to/online_retail_II.xlsx --confirm

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime, timezone
from typing import Any, Dict

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.domain_ingester import _conditions_signature, _load_template
from core.learn.gravity_engine import GravityIndex, GRAVITY_INDEX_PATH
from scripts.backfill_sales_trends_dryrun import load_combined_sample

DOMAIN = "sales_trends"
EVENT_TYPE = "sale"


class _BulkGravityIndexSeeded(GravityIndex):
    """Bulk in-memory loader that SEEDS from the real existing file first.

    See the module docstring's "Critical safety property" section. This
    is the only difference from scripts/calibrate_sales_trends.py's
    _BulkGravityIndex (which starts empty, correct only for fresh/temp
    paths).
    """

    def __init__(self, path: str):
        super().__init__(path=path)
        self._cache: Dict[str, Any] = GravityIndex._load(self)  # seed from disk, once

    def _load(self):
        return self._cache

    def _save(self, records):
        self._cache = records

    def flush(self) -> None:
        GravityIndex._save(self, self._cache)


def backup_production_file(path: str) -> str:
    if not os.path.isfile(path):
        print(f"[backfill-PRODUCTION] no existing file at {path} (nothing to back up)")
        return ""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = f"{path}.backup_pre_sales_trends_{ts}"
    shutil.copy2(path, backup_path)
    size_kb = os.path.getsize(backup_path) / 1024
    print(f"[backfill-PRODUCTION] backup written: {backup_path} ({size_kb:.1f} KB)")
    return backup_path


def ingest_into_production(df, idx: _BulkGravityIndexSeeded) -> None:
    template = _load_template(DOMAIN)
    n = len(df)
    for i, row in enumerate(df.itertuples(index=False)):
        data: Dict[str, Any] = {
            "product": str(row.StockCode),
            "region": str(row.Country),
            "quantity": int(row.Quantity),
            "amount": round(float(row.Quantity) * float(row.Price), 2),
            "invoice_id": str(row.Invoice),
        }
        fingerprint = f"{DOMAIN}:{EVENT_TYPE}:{_conditions_signature(EVENT_TYPE, data, template)}"
        field_count = len(data)
        cc_score = min(0.3 + (field_count * 0.1), 1.0)
        impact = "high" if field_count >= 5 else "medium" if field_count >= 3 else "low"
        idx.record_event(
            fingerprint=fingerprint,
            cc_score=cc_score,
            impact=impact,
            domain=DOMAIN,
            intent=EVENT_TYPE,
            event_timestamp=row.InvoiceDate.isoformat(),
        )
        if (i + 1) % 200000 == 0:
            print(f"[backfill-PRODUCTION] ingested {i + 1}/{n} rows...")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--xlsx-path",
        default=os.environ.get("VECTRAX_ONLINE_RETAIL_XLSX", ""),
        help="Path to a local online_retail_II.xlsx containing BOTH sheets.",
    )
    parser.add_argument(
        "--gravity-path", default=GRAVITY_INDEX_PATH,
        help=f"Path to the production gravity index (default: {GRAVITY_INDEX_PATH}).",
    )
    parser.add_argument(
        "--confirm", action="store_true",
        help="Required. Without this flag the script only prints what it WOULD do.",
    )
    args = parser.parse_args()

    if not args.xlsx_path or not os.path.isfile(args.xlsx_path):
        parser.error("--xlsx-path must point to a local online_retail_II.xlsx with both sheets.")

    print(f"[backfill-PRODUCTION] target gravity index: {args.gravity_path}")
    pre_existing = GravityIndex(path=args.gravity_path).load_raw()
    pre_count = len(pre_existing)
    pre_non_sales = {k: v for k, v in pre_existing.items() if v.domain != DOMAIN}
    print(f"[backfill-PRODUCTION] pre-existing fingerprints: {pre_count} "
          f"({len(pre_non_sales)} non-sales_trends, {pre_count - len(pre_non_sales)} sales_trends)")

    if not args.confirm:
        print("[backfill-PRODUCTION] DRY (no --confirm passed) — stopping before any write.")
        df = load_combined_sample(args.xlsx_path)
        print(f"[backfill-PRODUCTION] would ingest {len(df)} rows. Re-run with --confirm to apply.")
        return

    backup_production_file(args.gravity_path)

    df = load_combined_sample(args.xlsx_path)

    idx = _BulkGravityIndexSeeded(path=args.gravity_path)
    assert len(idx._cache) == pre_count, "seeded cache size mismatch before ingest — aborting"

    ingest_into_production(df, idx)
    idx.flush()

    post_existing = GravityIndex(path=args.gravity_path).load_raw()
    post_count = len(post_existing)
    missing = [k for k in pre_non_sales if k not in post_existing]

    print(f"\n[backfill-PRODUCTION] post-flush fingerprints: {post_count}")
    print(f"[backfill-PRODUCTION] net new fingerprints: {post_count - pre_count}")
    if missing:
        print(f"[backfill-PRODUCTION] !!! DATA LOSS DETECTED: {len(missing)} pre-existing "
              f"non-sales_trends fingerprints are MISSING after flush !!!")
        for k in missing[:10]:
            print(f"    MISSING: {k}")
        sys.exit(1)
    print(f"[backfill-PRODUCTION] OK — all {len(pre_non_sales)} pre-existing non-sales_trends "
          f"fingerprints verified present after flush.")

    sales_stars = [v for v in post_existing.values() if v.domain == DOMAIN]
    hits = sorted((r.hits for r in sales_stars), reverse=True)
    print(f"[backfill-PRODUCTION] sales_trends stars in production now: {len(sales_stars)}")
    if hits:
        print(f"[backfill-PRODUCTION] top hits: {hits[:5]}")


if __name__ == "__main__":
    main()
