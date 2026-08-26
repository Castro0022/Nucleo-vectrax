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
    method: str = "rayleigh"  # "rayleigh" or "lomb_scargle_detrended"


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


def _period_grid(
    t_days: np.ndarray,
    n_periods: int,
    oversample: float,
    min_period_floor: Optional[float] = None,
) -> np.ndarray:
    """Build a period grid derived only from the data's own span.

    No candidate period is ever hardcoded. The lower bound, by default,
    comes purely from the grid density budget (NOT the smallest gap
    between consecutive real events: for a point process, consecutive
    gaps are themselves already close to the true period, so using them
    as a Nyquist-style floor would exclude the very period being
    searched for). Callers whose method has its own resolution limit
    (e.g. a binned/detrended approach, which cannot resolve a period
    shorter than a few bin widths) may pass ``min_period_floor`` to raise
    that lower bound — still entirely data-derived (from the caller's own
    bin width), never a calendar constant.

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
    default_floor = span / _GRID_RESOLUTION_CAP
    min_period = max(min_period_floor if min_period_floor is not None else default_floor, 1e-6)
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
        method="rayleigh",
    )


# ---------------------------------------------------------------------------
# Detrended Lomb-Scargle — separates a slow trend from real periodicity
# ---------------------------------------------------------------------------
#
# Stage 3 rationale: the Rayleigh detector above operates directly on raw
# event times. Its power at a trial period P depends only on the RELATIVE
# clustering of folded phases, which is invariant to shifting every
# timestamp by a constant — so a slow, single-cycle trend (e.g. sales
# ramping up across the only observed year, see
# docs/SALES_TRENDS_CALIBRATION_2026_08_25.md) can masquerade as
# "periodicity" at a period close to the observation span, and no
# permutation null built from *shifting* the data could ever catch this
# (shifting doesn't change Rayleigh power for genuine periodic signals
# either, so it cannot discriminate trend from signal).
#
# The fix here is a real trend/residual DECOMPOSITION, not a smarter null:
#   1. Bin the point process into a regular count series (bin width and bin
#      count derived from the data itself: never a calendar constant).
#   2. Remove a slow trend via a centered moving average whose window is a
#      large FRACTION of the span (default 0.6, deliberately > 0.5 so it
#      always exceeds _period_grid's own max_period = span/2 — any genuine
#      candidate-period signal survives the smoothing untouched; only
#      slower variation is removed).
#   3. Test the residual (now a real-valued signal, not a point process)
#      with the classical Lomb-Scargle periodogram (Lomb 1976 / Scargle
#      1982) over the SAME period grid (no predefined target periods).
#   4. Significance via permutation: shuffle the residual VALUES across the
#      fixed bin times. This is a valid null here (unlike shifting) because
#      it fully destroys temporal order/structure while preserving the
#      residual's value distribution — a standard bootstrap FAP technique
#      for periodograms.


def _adaptive_bin_width(t_days: np.ndarray) -> float:
    """Estimate a data-derived bin width for binning a point process into
    a count series: roughly one bin per observed point on average.

    Deliberately count-based (span / n), NOT derived from a percentile of
    consecutive gaps: for random/unstructured data, low-percentile gap
    statistics shrink toward zero simply from having many points
    (order-statistics of the minimum spacing), which would silently blow
    up the bin count and reintroduce noise-driven false positives —
    exactly what the FAP permutation test exists to prevent. Count-based
    binning is predictable and stable regardless of how the points happen
    to be distributed, at the cost of a real, documented trade-off: this
    detector needs several activations per real-world cycle to resolve a
    period at all (see detect_periodicity_detrended's min_period_floor);
    sparse ~1-point-per-cycle data is correctly left to the point-process
    detect_periodicity() instead.
    """
    span = float(t_days.max() - t_days.min())
    n = t_days.shape[0]
    floor = span / _GRID_RESOLUTION_CAP
    return max(span / max(n, 1), floor)


def _bin_series(t_days: np.ndarray, n_bins: int) -> tuple[np.ndarray, np.ndarray]:
    """Bin a point process into a regularly-spaced count series.

    Bin edges span exactly [t_days.min(), t_days.max()]; ``n_bins`` is
    chosen by the caller from the data itself (never a calendar constant).
    Returns (bin_centers, counts).
    """
    t_min, t_max = float(t_days.min()), float(t_days.max())
    edges = np.linspace(t_min, t_max, n_bins + 1)
    counts, _ = np.histogram(t_days, bins=edges)
    centers = (edges[:-1] + edges[1:]) / 2.0
    return centers, counts.astype(float)


def _rolling_mean(x: np.ndarray, window: int) -> np.ndarray:
    """Centered moving average, edge-padded to preserve length."""
    window = max(1, int(window))
    if window <= 1 or x.shape[0] <= 1:
        return x.copy()
    pad_left = window // 2
    pad_right = window - 1 - pad_left
    padded = np.pad(x, (pad_left, pad_right), mode="edge")
    kernel = np.ones(window) / window
    return np.convolve(padded, kernel, mode="valid")


def _lomb_scargle_powers(t: np.ndarray, y: np.ndarray, periods: np.ndarray) -> np.ndarray:
    """Classical Lomb (1976) / Scargle (1982) periodogram power for a
    real-valued signal y(t) across a period grid. Unlike the Rayleigh
    power above, this operates on signal AMPLITUDE, not point-occurrence
    phase — the correct tool once the point process has been converted
    into a (detrended) count series.
    """
    y = y - float(np.mean(y))
    omega = 2.0 * np.pi / periods            # (m,)
    t_col = t[:, None]                       # (n, 1)
    om_row = omega[None, :]                  # (1, m)
    arg = 2.0 * om_row * t_col                # (n, m)
    sum_sin2 = np.sum(np.sin(arg), axis=0)
    sum_cos2 = np.sum(np.cos(arg), axis=0)
    tau = np.arctan2(sum_sin2, sum_cos2) / (2.0 * omega)  # (m,)
    phase = om_row * (t_col - tau[None, :])   # (n, m)
    cos_p = np.cos(phase)
    sin_p = np.sin(phase)
    y_col = y[:, None]
    num_c = np.sum(y_col * cos_p, axis=0) ** 2
    den_c = np.sum(cos_p ** 2, axis=0)
    num_s = np.sum(y_col * sin_p, axis=0) ** 2
    den_s = np.sum(sin_p ** 2, axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        term_c = np.where(den_c > 0, num_c / den_c, 0.0)
        term_s = np.where(den_s > 0, num_s / den_s, 0.0)
    return 0.5 * (term_c + term_s)


def _best_period_ls(t: np.ndarray, y: np.ndarray, grid: np.ndarray) -> tuple[float, float]:
    powers = _lomb_scargle_powers(t, y, grid)
    idx = int(np.argmax(powers))
    return float(grid[idx]), float(powers[idx])


def detect_periodicity_detrended(
    timestamps: Sequence[TimestampLike],
    fap_threshold: float = 0.01,
    n_permutations: int = 1000,
    min_points: int = 8,
    n_periods: int = 500,
    oversample: float = 3.0,
    trend_fraction: float = 0.6,
    random_state: Optional[int] = None,
) -> Optional[PeriodicityResult]:
    """Detect periodicity while explicitly separating it from a slow trend.

    See the module-level rationale above ``_bin_series`` for why this
    exists alongside ``detect_periodicity``. Same design constraints apply
    unchanged: no predefined candidate periods (reuses ``_period_grid``,
    including its span/2 cap), and significance is never "whichever period
    has the highest power" — a permutation test must clear
    ``fap_threshold`` or this returns ``None``.

    Args:
        timestamps: same as ``detect_periodicity``.
        fap_threshold, n_permutations, min_points, n_periods, oversample,
            random_state: same meaning as ``detect_periodicity``.
        trend_fraction: fraction of the observed span used as the trend
            removal window. Must stay > 0.5 so the trend window always
            exceeds ``_period_grid``'s max_period (span/2) — otherwise the
            smoothing could partially remove genuine candidate-period
            signal along with the trend.

    Returns:
        None if there are too few points, no temporal span, no viable
        period grid, a degenerate (constant) residual, or the FAP exceeds
        ``fap_threshold``. Otherwise a ``PeriodicityResult`` with
        ``method="lomb_scargle_detrended"``.
    """
    t_days = _normalize_timestamps(timestamps)
    n = t_days.shape[0]
    if n < min_points:
        return None

    span = float(t_days.max() - t_days.min())
    if span <= 0:
        return None

    # Bin width derived from the data's own fine-grained gap structure
    # (never total point count alone, and never a calendar constant) —
    # see _adaptive_bin_width for why this correctly self-limits to the
    # regime where binning can resolve anything at all.
    bin_width = _adaptive_bin_width(t_days)
    n_bins = int(min(max(round(span / bin_width), 50), _GRID_RESOLUTION_CAP))
    bin_centers, counts = _bin_series(t_days, n_bins)
    bin_width = span / n_bins  # recompute to match the actual (rounded/capped) bin count used

    # A binned signal cannot resolve a period much shorter than a handful
    # of bin widths (Nyquist requires >=2 samples/cycle; 4x bin_width is a
    # comfortable margin above the bare minimum for the periodogram shape
    # to be meaningful). This floor is entirely derived from THIS method's
    # own resolution (n and span), never a calendar constant — it is
    # deliberately coarser than detect_periodicity()'s point-process floor,
    # since binning inherently trades short-period sensitivity for the
    # ability to separate a slow trend from real periodicity.
    grid = _period_grid(
        t_days, n_periods=n_periods, oversample=oversample,
        min_period_floor=4.0 * bin_width,
    )
    if grid.size == 0:
        return None

    trend_window_bins = max(1, int(round((span * trend_fraction) / bin_width)))
    trend = _rolling_mean(counts, trend_window_bins)
    residual = counts - trend

    if not np.any(residual):
        return None  # degenerate: no variance left to test

    observed_period, observed_power = _best_period_ls(bin_centers, residual, grid)

    rng = np.random.default_rng(random_state)
    exceed_count = 0
    for _ in range(n_permutations):
        shuffled = rng.permutation(residual)
        _, null_power = _best_period_ls(bin_centers, shuffled, grid)
        if null_power >= observed_power:
            exceed_count += 1

    fap = (exceed_count + 1) / (n_permutations + 1)
    if fap > fap_threshold:
        return None

    return PeriodicityResult(
        period_days=observed_period,
        power=observed_power,
        fap=fap,
        n_points=n,
        n_permutations=n_permutations,
        method="lomb_scargle_detrended",
    )
