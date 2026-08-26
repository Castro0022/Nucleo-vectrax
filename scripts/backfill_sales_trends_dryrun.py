#!/usr/bin/env python3
"""
scripts/backfill_sales_trends_dryrun.py — Stage 3 dry-run (pre-backfill)
==========================================================================
Combines BOTH Online Retail II sheets ("Year 2009-2010" + "Year 2010-2011",
~2 years) into a single deduplicated dataset, loads it into an ISOLATED,
in-memory bulk GravityIndex (never the production
~/.vectrax/gravity_index.json), and smoke-tests both periodicity detectors
(point-process Rayleigh and detrended Lomb-Scargle) on real 2-year data.

This is explicitly a DRY RUN: it never writes to the production index and
does not activate anything in the ingest pipeline. See the Stage 3 plan
for the full rationale and open decisions (e.g. whether to recalibrate
MAX_ACTIVATION_HISTORY against the combined 2-year distribution).

Sheet overlap and deduplication
--------------------------------
The two official sheets OVERLAP by 8 days (2010-12-01 .. 2010-12-09):
verified that all 22,523 rows in that window are EXACT duplicates between
sheets (same Invoice/StockCode/Quantity/InvoiceDate/Price/CustomerID/
Country, matching even per-key multiplicities for the handful of genuine
within-sheet repeat line-items). ``deduplicate_overlap()`` removes ONLY
rows that are exact multiset duplicates of a row in the other sheet — it
does NOT use a date-range cutoff, so any legitimately repeated
transaction that is not an exact content match (in the overlap window or
anywhere else) is preserved untouched, per the approved Stage 3 condition.

Dependencies (script-only, NOT added to requirements.txt):
    pip install pandas openpyxl

Download the dataset (see scripts/calibrate_sales_trends.py for the same
instructions) and pass --xlsx-path.

Usage:
    python scripts/backfill_sales_trends_dryrun.py --xlsx-path /tmp/online_retail_II.xlsx

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from typing import Any, Dict, List

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.domain_ingester import _conditions_signature, _load_template
from core.learn.temporal_pattern import detect_periodicity, detect_periodicity_detrended
from scripts.calibrate_sales_trends import _BulkGravityIndex, _KNOWN_NON_PRODUCT_CODES

SHEET_1 = "Year 2009-2010"
SHEET_2 = "Year 2010-2011"
DOMAIN = "sales_trends"
EVENT_TYPE = "sale"

_KEY_COLUMNS = ["Invoice", "StockCode", "Quantity", "InvoiceDate", "Price", "CustomerID", "Country"]


def _row_keys(df, key_columns: List[str] = _KEY_COLUMNS):
    """Build an exact composite-content key per row (as a pandas Series of
    strings). Two rows with the same key are, by construction, identical
    across every field that identifies a real transaction line-item."""
    import pandas as pd

    parts = []
    for col in key_columns:
        series = df[col]
        if col == "CustomerID":
            series = series.apply(lambda v: "NA" if pd.isna(v) else str(int(v)))
        elif col == "Price":
            series = series.round(4)
        parts.append(series.astype(str))
    key = parts[0]
    for p in parts[1:]:
        key = key.str.cat(p, sep="|")
    return key


def deduplicate_overlap(df1, df2, key_columns: List[str] = _KEY_COLUMNS):
    """Remove from ``df1`` only rows that are EXACT multiset duplicates of
    a row in ``df2``. ``df2`` is never modified or filtered.

    This is content-based, not date-based: a row is only removed if every
    field in ``key_columns`` matches a row in df2 exactly, and multiplicity
    is respected (if a key appears twice in df1 and twice in df2, both are
    considered accounted for and BOTH are removed from df1 — not more, not
    fewer; if it appears 3x in df1 but only 2x in df2, only 2 are removed,
    preserving the genuinely extra occurrence). Because Invoice numbers
    and minute-precision InvoiceDate are part of the key, a coincidental
    exact match between unrelated transactions is not realistically
    possible — in practice this only fires on genuine sheet-overlap
    duplicates, without needing to assume or compute a date window at all.
    """
    if len(df1) == 0:
        return df1.copy()

    k1 = _row_keys(df1, key_columns)
    k2 = _row_keys(df2, key_columns)

    budget: Dict[str, int] = {}
    for k in k2.values:
        budget[k] = budget.get(k, 0) + 1

    keep_mask: List[bool] = []
    for k in k1.values:
        avail = budget.get(k, 0)
        if avail > 0:
            budget[k] = avail - 1
            keep_mask.append(False)
        else:
            keep_mask.append(True)

    return df1[keep_mask].copy()


def load_combined_sample(xlsx_path: str):
    import pandas as pd

    xl = pd.ExcelFile(xlsx_path)
    y1 = xl.parse(SHEET_1).rename(columns={"Customer ID": "CustomerID"})
    y2 = xl.parse(SHEET_2).rename(columns={"Customer ID": "CustomerID"})

    def _clean(df):
        is_cancellation = df["Invoice"].astype(str).str.startswith("C")
        is_non_positive_qty = df["Quantity"] <= 0
        return df[~(is_cancellation | is_non_positive_qty)].copy()

    y1c, y2c = _clean(y1), _clean(y2)
    print(f"[backfill-dryrun] {SHEET_1}: {len(y1)} raw -> {len(y1c)} clean")
    print(f"[backfill-dryrun] {SHEET_2}: {len(y2)} raw -> {len(y2c)} clean")

    y1_deduped = deduplicate_overlap(y1c, y2c)
    removed = len(y1c) - len(y1_deduped)
    print(f"[backfill-dryrun] exact-duplicate rows removed from {SHEET_1} "
          f"(attributable to sheet overlap): {removed}")

    combined = pd.concat([y1_deduped, y2c], ignore_index=True)
    print(f"[backfill-dryrun] combined deduplicated dataset: {len(combined)} rows")
    print(f"[backfill-dryrun] date range: {combined['InvoiceDate'].min()} .. {combined['InvoiceDate'].max()}")
    print(f"[backfill-dryrun] unique StockCode: {combined['StockCode'].nunique()} | "
          f"unique Country: {combined['Country'].nunique()}")
    return combined


def ingest_sample(df) -> _BulkGravityIndex:
    template = _load_template(DOMAIN)
    tmp_path = os.path.join(tempfile.mkdtemp(prefix="vectrax_backfill_dryrun_"), "gravity_index.json")
    idx = _BulkGravityIndex(path=tmp_path)

    for row in df.itertuples(index=False):
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

    idx.flush()
    print(f"[backfill-dryrun] ingested {len(df)} rows -> {len(idx._cache)} distinct stars "
          f"(isolated index, NOT production)")
    return idx


def report(idx: _BulkGravityIndex, percentile: float) -> None:
    import numpy as np

    records = list(idx._cache.values())
    hits = np.array([r.hits for r in records], dtype=float)

    print("\n=== Hits-per-star distribution (2-year combined, deduplicated) ===")
    for p in (50, 90, 95, 99, 99.9):
        print(f"  p{p:<5} = {np.percentile(hits, p):.1f}")
    print(f"  max    = {hits.max():.0f}")
    print(f"  n_stars = {len(records)}")
    recommended = int(np.ceil(np.percentile(hits, percentile)))
    print(f"  p{percentile} recommendation for MAX_ACTIVATION_HISTORY: {recommended} "
          f"(currently 154, calibrated on 1 year only — see Stage 3 plan open question)")

    top = sorted(records, key=lambda r: r.hits, reverse=True)[:15]
    print("\n=== Top 15 stars by hits (2-year combined) ===")
    for r in top:
        code = r.fingerprint.split(":", 2)[-1]
        flagged = any(nc in code for nc in _KNOWN_NON_PRODUCT_CODES)
        marker = "  [NON-PRODUCT CODE]" if flagged else ""
        print(f"  hits={r.hits:<7} freq={r.freq:<8.2f} span_days={_span_days(r):<7.1f} {code}{marker}")

    print("\n=== Periodicity smoke test on top 8 real product stars (2-year data) ===")
    checked = 0
    for r in top:
        if checked >= 8:
            break
        code = r.fingerprint.split(":", 2)[-1]
        if any(nc in code for nc in _KNOWN_NON_PRODUCT_CODES):
            continue
        checked += 1
        try:
            rayleigh = detect_periodicity(r.activation_history, random_state=0)
            detrended = detect_periodicity_detrended(r.activation_history, random_state=0)
        except Exception as exc:
            print(f"  {code}: RAISED {type(exc).__name__}: {exc}")
            raise
        print(f"  {code} (n_activations={len(r.activation_history)}, span={_span_days(r):.0f}d)")
        print(f"    rayleigh : {rayleigh}")
        print(f"    detrended: {detrended}")


def _span_days(rec) -> float:
    from datetime import datetime
    if len(rec.activation_history) < 2:
        return 0.0
    ts = [datetime.fromisoformat(t) for t in rec.activation_history]
    return (max(ts) - min(ts)).total_seconds() / 86400.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--xlsx-path",
        default=os.environ.get("VECTRAX_ONLINE_RETAIL_XLSX", ""),
        help="Path to a local online_retail_II.xlsx containing BOTH sheets.",
    )
    parser.add_argument("--percentile", type=float, default=95.0)
    args = parser.parse_args()

    if not args.xlsx_path or not os.path.isfile(args.xlsx_path):
        parser.error("--xlsx-path must point to a local online_retail_II.xlsx with both sheets.")

    df = load_combined_sample(args.xlsx_path)
    idx = ingest_sample(df)
    report(idx, percentile=args.percentile)


if __name__ == "__main__":
    main()
