# sales_trends Stage 3 — Combined 2-Year Backfill Dry-Run + Trend/Periodicity Separation (2026-08-26)

Stage 3 of the Gravity temporal pattern extension (Stage 1: PR #102, Stage 2: PR #103). Goal: design — and dry-run, in an isolated index only — the full two-sheet Online Retail II backfill, and separate trend from periodicity before evaluating recurrences, per the approved plan's explicit condition.

## Sheet overlap: exact-duplicate verification and deduplication

The two official sheets overlap 8 days (2010-12-01 .. 2010-12-09). Verified with a full composite-key comparison (`Invoice`, `StockCode`, `Quantity`, `InvoiceDate`, `Price`, `CustomerID`, `Country`) that **every row** in that window is an exact multiset duplicate between sheets (22,202 distinct keys, per-key multiplicities identical in both sheets, zero non-matching rows found).

Per the approved condition, deduplication (`scripts/backfill_sales_trends_dryrun.py::deduplicate_overlap`) is **content-based, not date-based**: it removes from the 2009-2010 sheet only rows with an exact multiset match in the 2010-2011 sheet, respecting per-key multiplicity (a key appearing 3x in one sheet and 2x in the other only removes 2, preserving the genuinely extra occurrence). This has no explicit "overlap window" concept at all — `InvoiceDate` is part of the key, so a coincidental match outside the real overlap is not realistically possible. Unit-tested on synthetic data (`tests/test_backfill_dedup.py`) for: exact duplicates removed, non-duplicates in the same date window preserved, multiplicity-aware matching (not naive set dedup), and rows outside any overlap preserved untouched.

Real result: 22,130 rows removed (525,461+541,910 raw → 513,134+531,286 clean → **1,022,290** combined deduplicated rows, span 2009-12-01 to 2011-12-09, ≈738 days, 28,434 distinct `sales_trends` stars).

## Why `span/2` alone doesn't separate trend from periodicity

A simple "require ≥2 cycles" grid bound (Stage 2's `max_period = span/2` fix) only guards against the most degenerate case (period ≈ full span). It does not distinguish a genuine repeating cycle from a slow trend that happens to fit ~2 "cycles" within the span. Confirmed mathematically that a circular time-shift null (an intuitive alternative) cannot work either: Rayleigh power at a fixed candidate period is invariant to shifting every timestamp by a constant, since it depends only on relative phase clustering — so a shift-based null cannot discriminate genuine periodicity from trend-induced clustering for ANY period.

## Detrending mechanism implemented (`core/learn/temporal_pattern.py`)

`detect_periodicity_detrended()`: bins the point process into a count series (bin width auto-derived: `span / n_points`, capped by the same resolution budget as the existing grid — deliberately count-based rather than a percentile of raw gaps, since gap percentiles blow up on ordinary random data purely from order statistics of the minimum spacing, which reintroduced false positives during validation), removes a slow trend via a centered moving average (window = 60% of span, deliberately > 50% so it always exceeds `_period_grid`'s own `max_period = span/2`), and tests the residual with the classical Lomb-Scargle periodogram (amplitude-based, unlike Rayleigh's phase-folding). Significance via permutation: shuffling residual **values** across fixed bin times — a valid null for a real-valued signal (unlike time-shifting).

Documented trade-off found during validation: binning inherently needs several activations per cycle to resolve a period at all (a Nyquist-safe floor of `4 × bin_width` is enforced) — sparse ~1-point-per-cycle data, which the point-process Rayleigh detector handles fine, falls below this floor and correctly returns `None` rather than fabricating a result. This is intentional and documented, not a bug: two complementary detectors now exist for two different density regimes.

### Synthetic validation (mandatory before touching real data)

`tests/test_temporal_pattern.py::TestDetrendedDetector` (all passing):
- Pure trend, no periodicity → `None` (multiple seeds) — regression guard replicating the exact Stage 2 real-data artifact.
- Pure periodicity, sufficient density → detects the true period (artificial, 47 days) with no calendar assumption.
- Sparse periodicity below bin resolution → `None` (not a crash), with `detect_periodicity()` (Rayleigh) still succeeding on the same data — documents the density trade-off directly.
- **Trend + periodicity mixed → detects the true 47-day period despite the trend** (the central Stage 3 claim), while never landing on the trend/span-boundary artifact.
- Pure noise (negative validation, multiple seeds) → `None`, preserved.

## Real 2-year smoke test: an honest, inconclusive result — not a positive finding

Running both detectors on the top 8 real stars of the combined 2-year dataset:

- Every single star (unrelated products) reports a period in the **330–369 day range** at the FAP floor, for both methods.
- **Rayleigh's results are suspiciously uniform**: 368.9–369.07 days across all 8 completely different products — matching `span/2 ≈ 369.05` to within a few hours. This is the signature of the detector still locking onto the grid boundary itself, not genuine per-product structure.
- **The detrended method's results vary more by product** (311–369 days) — evidence it is capturing something closer to real per-product structure rather than uniformly hitting the same boundary, though this cannot yet be confirmed as genuine annual seasonality.

**Why this is inconclusive, not a discovery**: with exactly 2 years of data, a real annual cycle (≈365 days) and the span/2 boundary artifact (≈369 days) are numerically almost indistinguishable — 2 years is barely enough to observe 2 cycles of an annual pattern, with no comfortable margin. This was explicitly predicted in the approved Stage 3 plan ("span/2≈369 días apenas supera un año — justo el mínimo de 2 ciclos, sin margen") and the real data confirms it. Confirming genuine annual seasonality (as opposed to a residual boundary effect) would require a 3rd+ year of out-of-sample data to see whether the same ~365-day pattern continues — which this dataset does not have.

**This stage does not claim retail seasonality was found.** It confirms: (1) the mechanism runs correctly end-to-end on ~1M rows of real, messy data without crashing, (2) detrending measurably changes the result relative to Rayleigh in a direction consistent with capturing more real structure, and (3) 2 years of Online Retail II is insufficient to resolve the trend/annual-seasonality ambiguity at this specific timescale.

## MAX_ACTIVATION_HISTORY: 2-year distribution (informational, not applied)

p50=2, p90=59, p95=**192**, p99=722, p99.9=1857, max=5367 (vs Stage 2's 1-year p95=154). Roughly linear scaling with the doubled observation window, as expected. Left as an open decision, per the approved plan — not changed in this stage.

## Explicitly out of scope (unchanged)

- No write to the production Gravity index — the dry-run used an isolated, in-memory bulk index, discarded after the report.
- No activation of `detect_periodicity()`/`detect_periodicity_detrended()` in the ingest pipeline.
- No decision on recalibrating `MAX_ACTIVATION_HISTORY` for the 2-year distribution.
- No claim of detected retail seasonality.
