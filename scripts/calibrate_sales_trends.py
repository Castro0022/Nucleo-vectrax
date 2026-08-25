#!/usr/bin/env python3
"""
scripts/calibrate_sales_trends.py — Stage 2 calibration (pre-backfill)
========================================================================
Measures the REAL distribution of Gravity activation counts (``hits``)
per sales_trends star, using a genuine sample of Online Retail II (UCI
dataset id=502, "Year 2009-2010" sheet — ~525k rows, ~1 year), so that
``MAX_ACTIVATION_HISTORY`` (core/learn/gravity_engine.py) can be set from
evidence instead of a number that "sounds reasonable".

What this script does:
  1. Loads a LOCAL copy of the official UCI .xlsx (never downloaded
     automatically — see download instructions below — to avoid a hidden
     network dependency in an otherwise offline-friendly codebase).
  2. Excludes cancellations (Invoice starting with 'C') and non-positive
     quantities. This is a data-integrity decision (a cancelled order is
     not a real sale activation), not a narrative-shaping one — known
     non-product StockCodes (POST, MANUAL, DOT, ...) are deliberately
     kept in and reported separately; whether a production loader should
     filter them is a decision for a later stage, informed by this report.
  3. Maps rows to the sales_trends "sale" event contract (product=StockCode,
     region=Country — see config/domain_templates/sales_trends.json) and
     feeds them into an ISOLATED, in-memory bulk GravityIndex. This NEVER
     touches the real ~/.vectrax/gravity_index.json.
  4. Prints a calibration report: hits-per-star percentiles, the top
     stars by hits (to eyeball real products vs. non-product codes), a
     MAX_ACTIVATION_HISTORY recommendation based on the p95 percentile
     (creator's choice: conservative on memory, accepts decimating
     slightly more stars than p99 would), and a detect_periodicity()
     SMOKE TEST on the top real stars — this validates the pipeline does
     not crash on real, messy, minute-level timestamps. It is explicitly
     NOT a claim that Vectrax "found retail seasonality"; that framing is
     out of scope for this stage.

This script is standalone: it is not part of the app, not imported by
production code, and not run in CI (network + a ~45MB dataset file are
required). Only the bulk-loading mechanism (_BulkGravityIndex) is unit
tested — see tests/test_calibration_bulk_index.py.

Dependencies (script-only, NOT added to requirements.txt):
    pip install pandas openpyxl

Download the dataset (one-time, manual, ~43MB zip / ~45MB xlsx):
    curl -sSL -o /tmp/online_retail_ii.zip \\
        "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
    unzip -o /tmp/online_retail_ii.zip -d /tmp
    # -> /tmp/online_retail_II.xlsx

Usage:
    python scripts/calibrate_sales_trends.py --xlsx-path /tmp/online_retail_II.xlsx
    python scripts/calibrate_sales_trends.py --xlsx-path /tmp/online_retail_II.xlsx --percentile 95

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from typing import Any, Dict

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.domain_ingester import _conditions_signature, _load_template
from core.learn.gravity_engine import GravityIndex
from core.learn.schemas import GravityRecord
from core.learn.temporal_pattern import detect_periodicity

SHEET_NAME = "Year 2009-2010"
DOMAIN = "sales_trends"
EVENT_TYPE = "sale"

# Known non-product StockCodes in Online Retail II (postage, manual
# adjustments, bank charges, etc.) — kept IN the sample deliberately (see
# module docstring), flagged here only so the report can call them out.
_KNOWN_NON_PRODUCT_CODES = {
    "POST", "M", "MANUAL", "DOT", "BANK CHARGES", "AMAZONFEE",
    "CRUK", "C2", "D", "S", "PADS", "ADJUST", "ADJUST2",
}


class _BulkGravityIndex(GravityIndex):
    """In-memory-only GravityIndex variant for bulk historical loads.

    ``record_event()`` normally re-reads/rewrites the ENTIRE JSON index on
    every call — correct for one-event-at-a-time live traffic, but O(N)
    disk I/O per call makes it unusable for a few hundred thousand
    historical rows (O(N^2) total: see
    connectors/cybersecurity/verification_cycle.py::_accumulate_mass for
    the same problem solved the same way elsewhere in this codebase).

    This subclass caches the records dict in memory and only persists on
    an explicit ``flush()`` call, while reusing 100% of the real
    ``record_event()`` logic (effective-clock replay, first_seen/last_seen
    bracketing, Déjà Vu promotion, activation_history decimation)
    completely unchanged — it overrides only the storage backend.
    """

    def __init__(self, path: str):
        super().__init__(path=path)
        self._cache: Dict[str, GravityRecord] = {}

    def _load(self) -> Dict[str, GravityRecord]:
        return self._cache

    def _save(self, records: Dict[str, GravityRecord]) -> None:
        self._cache = records

    def flush(self) -> None:
        """Persist the in-memory cache to disk exactly once."""
        GravityIndex._save(self, self._cache)


def load_sample(xlsx_path: str):
    """Load and minimally clean the 2009-2010 sheet. Returns a DataFrame."""
    import pandas as pd

    df = pd.read_excel(xlsx_path, sheet_name=SHEET_NAME)
    df = df.rename(columns={"Customer ID": "CustomerID"})
    total = len(df)

    is_cancellation = df["Invoice"].astype(str).str.startswith("C")
    is_non_positive_qty = df["Quantity"] <= 0
    excluded_mask = is_cancellation | is_non_positive_qty
    excluded = int(excluded_mask.sum())
    df = df[~excluded_mask].copy()

    print(f"[calibrate] loaded {total} rows from '{SHEET_NAME}'")
    print(f"[calibrate] excluded {excluded} rows (cancellations / non-positive quantity)")
    print(f"[calibrate] {len(df)} rows remain for calibration")
    print(f"[calibrate] date range: {df['InvoiceDate'].min()} .. {df['InvoiceDate'].max()}")
    print(f"[calibrate] unique StockCode: {df['StockCode'].nunique()} | unique Country: {df['Country'].nunique()}")
    return df


def ingest_sample(df) -> _BulkGravityIndex:
    """Feed every row into an isolated, in-memory bulk GravityIndex using
    the REAL sales_trends fingerprint logic (never the production index)."""
    template = _load_template(DOMAIN)
    tmp_path = os.path.join(
        tempfile.mkdtemp(prefix="vectrax_calibration_"), "gravity_index.json",
    )
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
    print(f"[calibrate] ingested {len(df)} rows -> {len(idx._cache)} distinct stars")
    print(f"[calibrate] bulk index flushed to {tmp_path} (temp, not production)")
    return idx


def report(idx: _BulkGravityIndex, percentile: float) -> None:
    import numpy as np

    records = list(idx._cache.values())
    hits = np.array([r.hits for r in records], dtype=float)

    print("\n=== Hits-per-star distribution (sales_trends, 2009-2010 sample) ===")
    for p in (50, 90, 95, 99, 99.9):
        print(f"  p{p:<5} = {np.percentile(hits, p):.1f}")
    print(f"  max    = {hits.max():.0f}")
    print(f"  n_stars = {len(records)}")

    print("\n=== Top 15 stars by hits ===")
    top = sorted(records, key=lambda r: r.hits, reverse=True)[:15]
    for r in top:
        code = r.fingerprint.split(":", 2)[-1]
        flagged = any(nc in code for nc in _KNOWN_NON_PRODUCT_CODES)
        marker = "  [NON-PRODUCT CODE — see module docstring]" if flagged else ""
        print(f"  hits={r.hits:<7} freq={r.freq:<8.2f} {code}{marker}")

    recommended = int(np.ceil(np.percentile(hits, percentile)))
    print("\n=== MAX_ACTIVATION_HISTORY recommendation ===")
    print(f"  p{percentile} of hits = {recommended}")
    print(
        f"  -> {(hits <= recommended).mean() * 100:.1f}% of stars in this sample "
        f"would NEVER need decimation at MAX_ACTIVATION_HISTORY={recommended}."
    )

    print("\n=== detect_periodicity() smoke test (top 5 real stars by hits) ===")
    checked = 0
    for r in top:
        if checked >= 5:
            break
        code = r.fingerprint.split(":", 2)[-1]
        if any(nc in code for nc in _KNOWN_NON_PRODUCT_CODES):
            continue  # skip known non-product codes for the smoke test
        checked += 1
        try:
            result = detect_periodicity(r.activation_history, random_state=0)
            print(f"  {code}: n_activations={len(r.activation_history)} -> {result}")
        except Exception as exc:  # the whole point of the smoke test
            print(f"  {code}: RAISED {type(exc).__name__}: {exc}")
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--xlsx-path",
        default=os.environ.get("VECTRAX_ONLINE_RETAIL_XLSX", ""),
        help="Path to a local online_retail_II.xlsx (see module docstring for download instructions).",
    )
    parser.add_argument(
        "--percentile", type=float, default=95.0,
        help="Percentile of hits-per-star used for the MAX_ACTIVATION_HISTORY recommendation (default: 95).",
    )
    args = parser.parse_args()

    if not args.xlsx_path or not os.path.isfile(args.xlsx_path):
        parser.error(
            "--xlsx-path must point to a local online_retail_II.xlsx. "
            "See the module docstring for download instructions."
        )

    df = load_sample(args.xlsx_path)
    idx = ingest_sample(df)
    report(idx, percentile=args.percentile)


if __name__ == "__main__":
    main()
