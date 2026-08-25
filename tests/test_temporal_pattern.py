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

from core.learn.temporal_pattern import detect_periodicity, PeriodicityResult


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
