# sales_trends Calibration — Online Retail II Sample (2026-08-25)

Stage 2 of the Gravity temporal pattern extension (see Stage 1 PR #102). Goal: replace the placeholder `MAX_ACTIVATION_HISTORY = 256` with a value derived from real evidence, and smoke-test `detect_periodicity()` against real, messy data before any production backfill.

## Sample

- Source: UCI Online Retail II (dataset id 502), sheet "Year 2009-2010".
- 525,461 raw rows; 12,327 excluded (cancellations — `Invoice` starting with `C` — and non-positive `Quantity`); 513,134 rows used.
- Date range: 2009-12-01 07:45 to 2010-12-09 20:01 (~373 days).
- 4,317 unique `StockCode`, 40 unique `Country`.
- Loaded into an **isolated, in-memory** `GravityIndex` (never `~/.vectrax/gravity_index.json`) using the real `sales_trends` fingerprint (`product=StockCode`, `region=Country`), via `scripts/calibrate_sales_trends.py`.

## Hits-per-star distribution (18,370 distinct stars)

p50=2, p90=61, **p95=154**, p99=483, p99.9=1071.3, max=3309 (top star: `85123A` / United Kingdom — the well-known "WHITE HANGING HEART T-LIGHT HOLDER" bestseller).

## MAX_ACTIVATION_HISTORY recommendation

Per the creator's explicit choice (p95, conservative on memory over p99): **154**, replacing the placeholder 256. At this value, 95% of stars in the sample never need decimation; only the top ~5% (mostly genuine bestsellers, plus a few non-product codes — see below) trigger `decimate_history`'s span-preserving thinning.

Known non-product `StockCode`s (`POST`, `MANUAL`, `DOT`, `BANK CHARGES`, etc.) were deliberately **kept in** the sample rather than filtered, per the Stage 2 plan — whether a production loader should exclude them is left for a later stage, informed by this report. None of them appeared in the top 15 by hits in this sample.

## detect_periodicity() smoke test — artifact found and fixed

The first run of the smoke test (top 5 real stars by hits) reported an almost identical **~373-day period** across several *different, unrelated* products, each at the FAP floor (0.000999, the minimum possible with 1000 permutations). That number is suspiciously close to the sample's own span (~373 days).

Inspecting `85123A`'s monthly activation counts confirmed the cause — a single monotonic trend across the year, not a repeating cycle:

```
Jan 270  Feb 213  Mar 285  Apr 244  May 244  Jun 259
Jul 246  Aug 258  Sep 240  Oct 297  Nov 410  Dec 434
```

`_period_grid()`'s upper bound was the full observed span, which lets a period equal to the span fold the *entire single trend* into what looks like phase clustering — with only one cycle of data, "this repeats every ~373 days" is statistically indistinguishable from "this happened once, trending upward." **Fix**: `core/learn/temporal_pattern.py::_period_grid` now caps `max_period` at `span / 2` — the standard requirement of observing at least two full cycles before a period claim is entertained. Regression test: `tests/test_temporal_pattern.py::TestPeriodGridBounds`.

Re-running the smoke test after the fix: the exact ~373-day artifact disappeared. `85123A` still landed at the new grid boundary (~186.7 days, i.e. `span/2` again), and four of the five products landed at ~1.0 day. Neither result should be read as a business finding:

- **~186.7 days (`span/2` boundary again)**: `85123A`'s year-long upward trend still has just enough shape to bias detection toward *any* period near half the span — halving the grid bound caught the most egregious full-span case, but a strong single-year trend can still leak into a nearby harmonic. Fully separating "trend" from "period" (e.g. detrending, or requiring 3-4+ cycles) is a real, known limitation of Rayleigh/Lomb-Scargle-style detection on a single short baseline, **not resolved in this stage**.
- **~1.0 day**: plausibly genuine but mundane — order timestamps clustering by time-of-day (the store's operating hours), not a "sales trend" of any business interest.

**Conclusion for Stage 3 and beyond**: this dataset's single ~1-year window cannot support reliable *annual* seasonality claims regardless of grid tuning — that requires the full two-sheet, ~2-year dataset (deferred to the full backfill stage), and likely a detrended or floating-mean variant of the detector for long periods. Short periods (days/weeks) are not subject to this limitation since many repetitions fit within one year.

## What this stage explicitly does NOT conclude

- No claim that Vectrax "detected retail seasonality" — that framing remains out of scope.
- No full backfill occurred; the isolated bulk index was discarded after the report.
- No decision was made on excluding non-product `StockCode`s from a future production loader.
