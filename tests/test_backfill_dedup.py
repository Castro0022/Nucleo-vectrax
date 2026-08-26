"""
Tests for scripts/backfill_sales_trends_dryrun.py's deduplicate_overlap().

Approved Stage 3 condition: deduplication must be limited to EXACT
duplicates attributable to the sheet overlap, preserving legitimately
repeated transactions outside that intersection. These tests use small
synthetic DataFrames — the real ~1M-row dataset is never touched in CI
(see the script's module docstring for how it was verified on real data).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

# pandas is a script-only dependency (see scripts/backfill_sales_trends_dryrun.py
# module docstring), NOT part of requirements.txt. Skip this whole module
# gracefully wherever it isn't installed instead of failing collection.
pd = pytest.importorskip("pandas")

from scripts.backfill_sales_trends_dryrun import deduplicate_overlap


def _row(invoice, stockcode, qty, date, price, customer, country):
    return {
        "Invoice": invoice, "StockCode": stockcode, "Quantity": qty,
        "InvoiceDate": pd.Timestamp(date), "Price": price,
        "CustomerID": customer, "Country": country,
    }


class TestDeduplicateOverlap:
    def test_exact_duplicate_is_removed(self):
        """A row in df1 with an exact match in df2 must be removed from df1."""
        row = _row("536365", "85123A", 6, "2010-12-01 08:26:00", 2.55, 17850, "United Kingdom")
        df1 = pd.DataFrame([row])
        df2 = pd.DataFrame([dict(row)])
        result = deduplicate_overlap(df1, df2)
        assert len(result) == 0

    def test_non_duplicate_in_same_date_window_is_preserved(self):
        """A row sharing the SAME date window as a real duplicate, but with
        genuinely different content (different Quantity), must be kept."""
        dup = _row("536365", "85123A", 6, "2010-12-01 08:26:00", 2.55, 17850, "United Kingdom")
        distinct = _row("536366", "85123A", 12, "2010-12-01 08:26:00", 2.55, 17850, "United Kingdom")
        df1 = pd.DataFrame([dup, distinct])
        df2 = pd.DataFrame([dict(dup)])
        result = deduplicate_overlap(df1, df2)
        assert len(result) == 1
        assert result.iloc[0]["Invoice"] == "536366"

    def test_similar_row_outside_overlap_window_is_preserved(self):
        """A row that looks like df2's content but on a totally different
        date (i.e. genuinely outside any sheet-overlap situation) must not
        be treated as a duplicate, since InvoiceDate is part of the key."""
        dup_source = _row("536365", "85123A", 6, "2010-12-01 08:26:00", 2.55, 17850, "United Kingdom")
        far_away = _row("999999", "85123A", 6, "2011-06-15 10:00:00", 2.55, 17850, "United Kingdom")
        df1 = pd.DataFrame([far_away])
        df2 = pd.DataFrame([dict(dup_source)])
        result = deduplicate_overlap(df1, df2)
        assert len(result) == 1

    def test_multiplicity_aware_not_naive_set_dedup(self):
        """If a key appears twice in df1 but only once in df2 (a genuine
        repeat purchase plus one real overlap duplicate), only ONE
        occurrence should be removed from df1 — not both."""
        row = _row("536365", "85123A", 6, "2010-12-01 08:26:00", 2.55, 17850, "United Kingdom")
        df1 = pd.DataFrame([dict(row), dict(row)])  # appears twice in df1
        df2 = pd.DataFrame([dict(row)])              # appears once in df2
        result = deduplicate_overlap(df1, df2)
        assert len(result) == 1  # one genuine extra occurrence preserved

    def test_multiplicity_aware_removes_up_to_df2_count(self):
        """If a key appears 3x in df1 and 3x in df2, all 3 are considered
        accounted for and removed — this is the real Online Retail II
        pattern (some invoices have genuine repeat line-items, and the
        overlap duplicates them identically in both sheets)."""
        row = _row("536365", "85123A", 6, "2010-12-01 08:26:00", 2.55, 17850, "United Kingdom")
        df1 = pd.DataFrame([dict(row)] * 3)
        df2 = pd.DataFrame([dict(row)] * 3)
        result = deduplicate_overlap(df1, df2)
        assert len(result) == 0

    def test_df2_is_never_modified(self):
        row = _row("536365", "85123A", 6, "2010-12-01 08:26:00", 2.55, 17850, "United Kingdom")
        df1 = pd.DataFrame([dict(row)])
        df2 = pd.DataFrame([dict(row)])
        df2_copy = df2.copy()
        deduplicate_overlap(df1, df2)
        pd.testing.assert_frame_equal(df2, df2_copy)

    def test_empty_df1_returns_empty(self):
        df1 = pd.DataFrame(columns=["Invoice", "StockCode", "Quantity", "InvoiceDate", "Price", "CustomerID", "Country"])
        df2 = pd.DataFrame([_row("1", "A", 1, "2020-01-01", 1.0, 1, "UK")])
        result = deduplicate_overlap(df1, df2)
        assert len(result) == 0

    def test_no_overlap_at_all_preserves_everything(self):
        df1 = pd.DataFrame([_row(str(i), "A", 1, "2020-01-01", 1.0, 1, "UK") for i in range(5)])
        df2 = pd.DataFrame([_row(str(i), "B", 1, "2021-01-01", 2.0, 2, "France") for i in range(5)])
        result = deduplicate_overlap(df1, df2)
        assert len(result) == 5
