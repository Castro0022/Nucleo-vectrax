"""
Vectrax Temporal Pattern Extension — Generic Periodicity Detector
====================================================================
Detects periodic structure in a star's activation history WITHOUT any
semantic knowledge of seasons, holidays, or expected cycles. This module
belongs to Gravity, not to any specific domain (e.g. sales_trends) — any
domain whose activations carry timestamps can use it.

Design constraints (deliberate, do not relax):
  1. No predefined candidate periods. The period grid is derived purely
     from the observed span and spacing of the input timestamps. There is
     no special-casing of 7 / 30 / 90 / 365 days, months, or holidays.
  2. Significance is never "whichever period has the highest power". It is
     computed via a permutation test against a null hypothesis of no
     temporal structure (events uniformly distributed over the observed
     span). Only a False Alarm Probability (FAP) at or below the caller's
     threshold makes a result significant; otherwise this returns None.
  3. Pure and synchronous. No I/O, no Gravity coupling, no side effects —
     directly callable from tests and from a future calibration script.
     Any fire-and-forget integration with Gravity at runtime is a separate,
     later concern and does not live in this module.

Algorithm:
  For irregularly-spaced *event occurrences* (not a measured signal), the
  appropriate classical tool is a Rayleigh/Schuster power spectrum: fold
  the timestamps at a trial period, treat the folded phases as points on
  the unit circle, and measure how strongly they cluster. This is exactly
  the point-process analogue of the Lomb-Scargle periodogram used for
  irregularly-sampled measurements, and it degrades gracefully to "no
  power anywhere" for a non-periodic point process.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence, Union

import numpy as np

TimestampLike = Union[str, int, float]


@dataclass
class PeriodicityResult:
    """An inspectable, significant periodicity finding."""
    period_days: float
    power: float
    fap: float
    n_points: int
    n_permutations: int


# ---------------------------------------------------------------------------
# Input normalization
# ---------------------------------------------------------------------------

def _normalize_timestamps(timestamps: Sequence[TimestampLike]) -> np.ndarray:
    """Convert ISO-8601 strings or raw numeric offsets into sorted day-offsets.

    Numeric inputs are assumed to already be in days and are shifted so the
    earliest value is 0. ISO-8601 strings are parsed and converted to days
    elapsed since the earliest timestamp.
    """
    if len(timestamps) == 0:
        return np.array([], dtype=float)

    first = timestamps[0]
    if isinstance(first, str):
        parsed = []
        for ts in timestamps:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            parsed.append(dt)
        t0 = min(parsed)
        days = np.array([(dt - t0).total_seconds() / 86400.0 for dt in parsed], dtype=float)
    else:
        days = np.array([float(x) for x in timestamps], dtype=float)
        days = days - days.min()

    return np.sort(days)


# ---------------------------------------------------------------------------
# Rayleigh / Schuster power spectrum
# ---------------------------------------------------------------------------

_GRID_RESOLUTION_CAP = 5000  # numerical resolution budget, not a domain assumption


def _period_grid(t_days: np.ndarray, n_periods: int, oversample: float) -> np.ndarray:
    """Build a period grid derived only from the data's own span.

    No candidate period is ever hardcoded. The lower bound comes purely
    from the grid density budget (NOT the smallest gap between consecutive
    real events: for a point process, consecutive gaps are themselves
    already close to the true period, so using them as a Nyquist-style
    floor would exclude the very period being searched for).

    The upper bound is HALF the observed span, not the full span. A period
    equal to the full span would let a single monotonic trend/hump across
    the only observation window masquerade as "periodicity" — with one
    cycle of data you cannot distinguish a real recurring pattern from a
    one-off trend (e.g. a growing business's sales rising toward its one
    and only December in the dataset). Requiring at least two full cycles
    to fit within the span is the standard guard against this: see
    docs/SALES_TRENDS_CALIBRATION_2026_08_25.md for the Online Retail II
    smoke test where several unrelated real product stars all reported a
    near-identical ~373-day "period" (matching the dataset's ~373-day span
    almost exactly) at the FAP floor — the signature of this exact
    artifact, not genuine per-product seasonality.
    """
    span = float(t_days.max() - t_days.min())
    if span <= 0:
        return np.array([], dtype=float)

    n = int(min(max(n_periods * max(oversample, 1.0), 2), _GRID_RESOLUTION_CAP))
    min_period = max(span / _GRID_RESOLUTION_CAP, 1e-6)
    max_period = span / 2.0

    if max_period <= min_period:
        return np.array([], dtype=float)

    return np.geomspace(min_period, max_period, n)


def _powers_for_grid(t_days: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Vectorized Rayleigh power for every period in ``grid``.

    power(P) = |sum_i exp(2*pi*i * (t_i mod P) / P)|^2 / N
    Maximal when all folded phases coincide (strong periodic clustering),
    ~0 when phases are uniformly spread around the circle (no structure).
    """
    n = t_days.shape[0]
    t = t_days[:, None]          # (n, 1)
    periods = grid[None, :]      # (1, m)
    phases = 2.0 * np.pi * np.mod(t, periods) / periods  # (n, m)
    c = np.sum(np.cos(phases), axis=0)
    s = np.sum(np.sin(phases), axis=0)
    return (c ** 2 + s ** 2) / n


def _best_period(t_days: np.ndarray, grid: np.ndarray) -> tuple[float, float]:
    powers = _powers_for_grid(t_days, grid)
    idx = int(np.argmax(powers))
    return float(grid[idx]), float(powers[idx])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_periodicity(
    timestamps: Sequence[TimestampLike],
    fap_threshold: float = 0.01,
    n_permutations: int = 1000,
    min_points: int = 8,
    n_periods: int = 500,
    oversample: float = 3.0,
    random_state: Optional[int] = None,
) -> Optional[PeriodicityResult]:
    """Detect statistically significant periodic structure in activations.

    Args:
        timestamps: ISO-8601 strings, or raw numeric day-offsets (for tests
            and calibration). Order does not matter, duplicates are fine.
        fap_threshold: maximum acceptable False Alarm Probability. A result
            is only returned when the observed peak power could not
            plausibly arise from unstructured (uniformly random) activity
            at this significance level.
        n_permutations: number of null-hypothesis resamples used to
            estimate the FAP. Higher values give finer FAP resolution
            (resolution floor is ~1/(n_permutations + 1)).
        min_points: minimum number of observations required before even
            attempting detection. Below this, returns None outright — a
            handful of points cannot support a periodicity claim.
        n_periods: baseline number of candidate periods scanned (actual
            grid may be denser depending on data span/spacing).
        oversample: grid density factor relative to the finest resolvable
            period.
        random_state: seed for the permutation test's RNG (reproducibility
            in tests/calibration only; does not affect the observed data).

    Returns:
        None if there are too few points, no temporal span, no viable
        period grid, or the FAP exceeds ``fap_threshold``. Otherwise a
        ``PeriodicityResult`` with the detected period, its power, and the
        empirical FAP that supports it.
    """
    t_days = _normalize_timestamps(timestamps)
    n = t_days.shape[0]
    if n < min_points:
        return None

    span = float(t_days.max() - t_days.min())
    if span <= 0:
        return None

    grid = _period_grid(t_days, n_periods=n_periods, oversample=oversample)
    if grid.size == 0:
        return None

    observed_period, observed_power = _best_period(t_days, grid)

    rng = np.random.default_rng(random_state)
    t_min, t_max = float(t_days.min()), float(t_days.max())
    exceed_count = 0
    for _ in range(n_permutations):
        null_t = np.sort(rng.uniform(t_min, t_max, size=n))
        _, null_power = _best_period(null_t, grid)
        if null_power >= observed_power:
            exceed_count += 1

    # Add-one smoothing: a FAP of exactly 0.0 from a finite number of
    # permutations would overstate certainty. This also guarantees FAP is
    # never reported as a hard zero, which would be indistinguishable from
    # "the test wasn't run".
    fap = (exceed_count + 1) / (n_permutations + 1)

    if fap > fap_threshold:
        return None

    return PeriodicityResult(
        period_days=observed_period,
        power=observed_power,
        fap=fap,
        n_points=n,
        n_permutations=n_permutations,
    )
