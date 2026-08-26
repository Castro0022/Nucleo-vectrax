"""
Tests for core/learn/temporal_pattern.py — generic periodicity detector.

Deliberately uses ARTIFICIAL periods (11, 37, 83 days) instead of calendar
periods (7/30/90/365) so a pass cannot be explained by hardcoded knowledge
of human cycles. The detector must find structure purely from the data.

Two mandatory properties are validated:
  1. Positive: finds real periodic structure (with phase jitter + missing
     points) and reports a low FAP.
  2. Negative: rejects a purely random point process (no structure) and
     returns None instead of forcing the best-fitting period.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pytest

from core.learn.temporal_pattern import (
    detect_periodicity,
    detect_periodicity_detrended,
    PeriodicityResult,
    _period_grid,
)


def _synthetic_periodic(period: float, n_cycles: int, jitter_frac: float,
                         drop_frac: float, seed: int) -> list[float]:
    """Build day-offsets for a noisy periodic point process.

    Base occurrences at k*period for k in [0, n_cycles), each perturbed by
    gaussian phase jitter, then a random fraction of points is dropped to
    simulate missing observations.
    """
    rng = np.random.default_rng(seed)
    k = np.arange(n_cycles)
    base = k * period
    jitter = rng.normal(0.0, jitter_frac * period, size=n_cycles)
    t = base + jitter
    keep = rng.random(n_cycles) > drop_frac
    t = t[keep]
    t = t[t >= 0]
    return sorted(t.tolist())


def _synthetic_random(n_points: int, span: float, seed: int) -> list[float]:
    """Purely random arrival times over [0, span) — no structure at all."""
    rng = np.random.default_rng(seed)
    return sorted(rng.uniform(0.0, span, size=n_points).tolist())


def _synthetic_trend(n_points: int, span: float, seed: int, skew: float = 0.3) -> list[float]:
    """A single monotonic trend (density increasing toward the end of the
    only observation window) with NO repeating cycle — replicates the real
    artifact found on Online Retail II (see
    docs/SALES_TRENDS_CALIBRATION_2026_08_25.md)."""
    rng = np.random.default_rng(seed)
    t = np.sort(span * (rng.random(n_points) ** skew))
    return t.tolist()


def _synthetic_mixed(period: float, span: float, n_periodic_per_cycle: int,
                      n_trend: int, seed: int, trend_skew: float = 0.3) -> list[float]:
    """A monotonic trend (no periodicity) with a genuine periodic component
    superimposed — the scenario a trend-blind detector cannot handle."""
    rng = np.random.default_rng(seed)
    n_cycles = int(span / period)
    k = np.repeat(np.arange(n_cycles), n_periodic_per_cycle)
    jitter = rng.normal(0.0, 0.05 * period, size=len(k))
    periodic = k * period + jitter
    periodic = periodic[(periodic >= 0) & (periodic < span)]
    trend = span * (rng.random(n_trend) ** trend_skew)
    return np.sort(np.concatenate([periodic, trend])).tolist()


# ===================================================================
# Positive validation: recovers artificial periods
# ===================================================================

class TestPositiveDetection:
    @pytest.mark.parametrize("period,seed", [(11.0, 1), (37.0, 2), (83.0, 3)])
    def test_recovers_artificial_period_with_noise(self, period, seed):
        timestamps = _synthetic_periodic(
            period, n_cycles=30, jitter_frac=0.05, drop_frac=0.15, seed=seed,
        )
        result = detect_periodicity(
            timestamps,
            fap_threshold=0.02,
            n_permutations=300,
            n_periods=400,
            random_state=seed,
        )
        assert result is not None, f"expected to detect period={period}d but got None"
        assert isinstance(result, PeriodicityResult)
        relative_error = abs(result.period_days - period) / period
        assert relative_error < 0.15, (
            f"detected {result.period_days:.2f}d vs true {period}d "
            f"(rel err {relative_error:.2%})"
        )
        assert result.fap <= 0.02
        assert result.n_points >= 8

    def test_result_is_inspectable(self):
        timestamps = _synthetic_periodic(37.0, 30, 0.05, 0.1, seed=42)
        result = detect_periodicity(timestamps, n_permutations=300, random_state=42)
        assert result is not None
        assert hasattr(result, "period_days")
        assert hasattr(result, "power")
        assert hasattr(result, "fap")
        assert hasattr(result, "n_points")
        assert hasattr(result, "n_permutations")


# ===================================================================
# Negative validation: rejects pure noise
# ===================================================================

class TestNegativeDetection:
    @pytest.mark.parametrize("seed", [10, 11, 12])
    def test_rejects_random_arrivals(self, seed):
        timestamps = _synthetic_random(n_points=30, span=900.0, seed=seed)
        result = detect_periodicity(
            timestamps,
            fap_threshold=0.02,
            n_permutations=300,
            n_periods=400,
            random_state=seed,
        )
        assert result is None, (
            f"expected no significant periodicity on pure noise, got {result}"
        )

    def test_does_not_force_best_fit_on_noise(self):
        """Regression guard: a naive 'return the peak' implementation would
        always return *something*. The real detector must be able to say
        'no significant periodicity' instead."""
        outcomes = [
            detect_periodicity(
                _synthetic_random(25, 700.0, seed=100 + i),
                fap_threshold=0.02,
                n_permutations=200,
                random_state=100 + i,
            )
            for i in range(5)
        ]
        assert all(o is None for o in outcomes)


# ===================================================================
# Period grid bounds (max_period = span / 2 regression guard)
# ===================================================================

class TestPeriodGridBounds:
    """Regression guard: the period grid must require at least two full
    cycles to fit within the observed span (max_period = span / 2, not
    span). A period equal to the FULL span would let a single monotonic
    trend/hump across the only observation window masquerade as
    periodicity — discovered via the Online Retail II calibration smoke
    test, see docs/SALES_TRENDS_CALIBRATION_2026_08_25.md."""

    def test_max_period_is_half_the_span(self):
        t = np.linspace(0.0, 373.0, 200)
        grid = _period_grid(t, n_periods=500, oversample=3.0)
        assert grid.max() <= 373.0 / 2.0 + 1e-9

    def test_single_trend_never_reports_full_span_period(self):
        """A skewed, non-repeating single trend (no real cycle at all)
        must never be reported with a period near the full span — the
        exact shape of the artifact found on real Online Retail II data."""
        rng = np.random.default_rng(5)
        span = 373.0
        t = np.sort(span * (rng.random(200) ** 0.3))  # skewed toward the end, no periodicity
        result = detect_periodicity(t.tolist(), n_permutations=300, random_state=5)
        if result is not None:
            assert result.period_days <= span / 2.0 + 1e-6


# ===================================================================
# Detrended Lomb-Scargle detector (Stage 3): separates trend from period
# ===================================================================

class TestDetrendedDetector:
    """Mandatory Stage 3 validation (per the approved plan): the detrended
    detector must (1) reject a pure trend with no real cycle — replicating
    the exact artifact found on real Online Retail II data — (2) keep
    detecting genuine periodicity with no regression vs. the existing
    Rayleigh detector, (3) detect a real period DESPITE a superimposed
    trend, and (4) preserve negative validation on pure noise. All periods
    are artificial (not 7/30/365) to avoid confirmation bias."""

    def test_pure_trend_returns_none(self):
        """Regression guard replicating the real Stage 2 artifact: a single
        monotonic trend (no repeating cycle) must not be reported as
        periodic."""
        timestamps = _synthetic_trend(n_points=300, span=373.0, seed=1)
        result = detect_periodicity_detrended(timestamps, n_permutations=300, random_state=1)
        assert result is None, f"expected no significant periodicity on a pure trend, got {result}"

    @pytest.mark.parametrize("seed", [21, 22, 23])
    def test_pure_trend_returns_none_multiple_seeds(self, seed):
        timestamps = _synthetic_trend(n_points=300, span=373.0, seed=seed)
        result = detect_periodicity_detrended(timestamps, n_permutations=300, random_state=seed)
        assert result is None

    def test_pure_periodicity_detected_with_sufficient_density(self):
        """No trend at all: the detrended detector must still find a real
        period, GIVEN enough points to resolve it after binning.

        Binning inherently trades short-period/sparse-data sensitivity for
        the ability to separate trend from periodicity: a binned signal
        cannot resolve a period below ~4 bin widths (see
        detect_periodicity_detrended's min_period_floor), whereas the
        point-process Rayleigh detector works fine with ~1 point/cycle.
        This is why the calibration-derived synthetic here uses several
        points per cycle instead of detect_periodicity()'s sparser fixture
        — an honest, documented trade-off, not a bug.
        """
        timestamps = _synthetic_mixed(
            period=47.0, span=1400.0, n_periodic_per_cycle=6, n_trend=0, seed=2,
        )
        result = detect_periodicity_detrended(timestamps, n_permutations=300, random_state=2)
        assert result is not None
        assert result.method == "lomb_scargle_detrended"
        relative_error = abs(result.period_days - 47.0) / 47.0
        assert relative_error < 0.15, f"detected {result.period_days:.2f}d vs true 47d"

    def test_sparse_periodicity_below_bin_resolution_returns_none_not_crash(self):
        """Documents the trade-off directly: at Rayleigh-sparse density
        (~1 point/cycle), the period falls below the binned method's own
        Nyquist-safe floor and it correctly declines to guess, rather than
        crashing or fabricating a result. detect_periodicity() (Rayleigh)
        remains the right tool for this density regime."""
        timestamps = _synthetic_periodic(47.0, n_cycles=30, jitter_frac=0.05, drop_frac=0.1, seed=2)
        result = detect_periodicity_detrended(timestamps, n_permutations=300, random_state=2)
        assert result is None
        assert detect_periodicity(timestamps, n_permutations=300, random_state=2) is not None

    def test_detects_period_despite_superimposed_trend(self):
        """The central Stage 3 claim: a real periodic signal buried under a
        monotonic trend must still be detected, unlike a trend-blind
        detector which either finds nothing or reports the trend's span
        boundary as a spurious "period" (see docs/SALES_TRENDS_CALIBRATION_2026_08_25.md).
        """
        timestamps = _synthetic_mixed(
            period=47.0, span=1400.0, n_periodic_per_cycle=6,
            n_trend=150, seed=7,
        )
        result = detect_periodicity_detrended(timestamps, n_permutations=300, random_state=3)
        assert result is not None, "expected to detect the 47d cycle despite the trend"
        relative_error = abs(result.period_days - 47.0) / 47.0
        assert relative_error < 0.15, (
            f"detected {result.period_days:.2f}d vs true 47d (rel err {relative_error:.2%})"
        )
        # Must NOT be the trend/span-boundary artifact.
        assert result.period_days < 1400.0 / 4.0

    @pytest.mark.parametrize("seed", [30, 31, 32])
    def test_rejects_random_arrivals(self, seed):
        """Negative validation preserved: pure noise must still return None."""
        timestamps = _synthetic_random(n_points=300, span=1400.0, seed=seed)
        result = detect_periodicity_detrended(
            timestamps, fap_threshold=0.02, n_permutations=300, random_state=seed,
        )
        assert result is None, f"expected no significant periodicity on pure noise, got {result}"

    def test_min_period_floor_is_nyquist_safe_not_calendar_based(self):
        """Regression guard: the detrended grid's lower bound must come from
        the method's own bin resolution (data-derived), not the fine
        point-process floor used by detect_periodicity() — using the wrong
        floor made short candidate periods numerically unresolvable from
        binned counts (discovered while validating this very detector)."""
        t = np.linspace(0.0, 1400.0, 320)
        n_bins = min(max(len(t) // 2, 50), 2000)
        bin_width = 1400.0 / n_bins
        grid = _period_grid(t, n_periods=500, oversample=3.0, min_period_floor=4.0 * bin_width)
        assert grid.min() >= 4.0 * bin_width - 1e-9


# ===================================================================
# Edge cases
# ===================================================================

class TestEdgeCases:
    def test_too_few_points_returns_none(self):
        assert detect_periodicity([1.0, 2.0, 3.0], min_points=8) is None

    def test_empty_returns_none(self):
        assert detect_periodicity([]) is None

    def test_degenerate_identical_timestamps_returns_none(self):
        assert detect_periodicity([5.0] * 20) is None

    def test_default_min_points_boundary(self):
        # Exactly at the boundary: min_points itself does not raise, but with
        # no real structure it should not fabricate a period either.
        timestamps = _synthetic_random(8, 200.0, seed=7)
        result = detect_periodicity(timestamps, min_points=8, n_permutations=100, random_state=7)
        # Not asserting None strictly (small-N noise could rarely pass by
        # chance under a permissive threshold) — assert it never raises and
        # respects the contract (None or a well-formed result).
        assert result is None or isinstance(result, PeriodicityResult)

    def test_iso_string_timestamps_supported(self):
        # Same periodic structure as the numeric test, expressed as ISO-8601.
        from datetime import datetime, timedelta, timezone
        t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
        day_offsets = _synthetic_periodic(37.0, 30, 0.05, 0.1, seed=42)
        iso_timestamps = [
            (t0 + timedelta(days=d)).isoformat() for d in day_offsets
        ]
        result = detect_periodicity(iso_timestamps, n_permutations=300, random_state=42)
        assert result is not None
        assert abs(result.period_days - 37.0) / 37.0 < 0.15
