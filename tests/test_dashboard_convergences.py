"""Tests for services/core/routes/dashboard.py.

Regression guard: dashboard_operator() must report the canonical
convergence total from Census (single source of truth), never
len(snapshot.convergences) - a raw sample of in-memory graph edges +
up to 500 legacy ledger rows, unrelated to the deduplicated canonical
count. This is the exact anti-pattern found and fixed alongside the
2026-08-26 global-scan incident (see PR #108).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


def _run_async(coro):
    """Run a coroutine without leaving the process-wide asyncio event loop
    policy in a state that breaks other tests.

    ``asyncio.run()`` explicitly calls ``asyncio.set_event_loop(None)`` as
    part of its cleanup. In this test suite that broke an unrelated,
    pre-existing test (tests/test_providers.py::TestAnthropicProvider::
    test_list_models_static) that relies on ``asyncio.get_event_loop()``
    lazily auto-creating a loop - once a prior test has explicitly set the
    loop to None, Python's auto-create-on-main-thread fallback no longer
    applies and it raises "There is no current event loop". This helper
    creates and closes its own loop but always leaves a fresh, open loop
    set as current afterward, preserving the ambient default-loop
    behaviour the rest of the suite depends on.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.learn.convergence_registry as registry_mod  # noqa: E402
import core.universe_census as census_mod  # noqa: E402
from core.learn.convergence_registry import record_convergence_snapshot  # noqa: E402
from services.core.routes.dashboard import (  # noqa: E402
    dashboard_observatory,
    dashboard_operator,
)


class _IsolatedCanonicalRegistryMixin:
    """Point the canonical registry at a temp DB and reset Census's TTL
    cache, so the dashboard reads a known, controlled canonical total
    instead of the real production state or a stale cached value."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="vectrax_test_dashboard_")
        self.db_path = os.path.join(self.tmpdir, "convergence_history.db")
        self._orig_db_path = registry_mod.DB_PATH
        registry_mod.DB_PATH = self.db_path
        self._orig_cache = dict(census_mod._cache)
        census_mod._cache["census"] = None
        census_mod._cache["ts"] = 0.0

    def tearDown(self):
        registry_mod.DB_PATH = self._orig_db_path
        census_mod._cache.clear()
        census_mod._cache.update(self._orig_cache)
        shutil.rmtree(self.tmpdir, ignore_errors=True)


def _seed_canonical_convergences(db_path: str, count: int) -> None:
    live = {f"unknown:entity_{i}": "unknown" for i in range(count + 1)}
    candidates = [
        {
            "type": "intent_overlap",
            "star_a": f"unknown:entity_{i}",
            "star_b": f"unknown:entity_{i + 1}",
            "combined_cc": 0.5,
            "combined_hits": 1,
            "domains": ["unknown", "unknown"],
        }
        for i in range(count)
    ]
    record_convergence_snapshot(candidates, live, db_path)


class TestDashboardOperatorConvergenceCount(_IsolatedCanonicalRegistryMixin, unittest.TestCase):
    def test_reports_canonical_census_total(self):
        _seed_canonical_convergences(self.db_path, count=3)

        result = _run_async(dashboard_operator())

        self.assertIn("universe", result)
        self.assertNotIn("error", result["universe"])
        self.assertEqual(result["universe"]["convergences"], 3)

    def test_matches_census_exactly_as_data_changes(self):
        """The endpoint must track Census's canonical total, not some
        independently-computed or stale number."""
        _seed_canonical_convergences(self.db_path, count=1)
        first = _run_async(dashboard_operator())
        self.assertEqual(first["universe"]["convergences"], 1)

        # Force a fresh census read (bypass the 10s TTL cache) after adding
        # more canonical convergences, and confirm the dashboard reflects it.
        census_mod._cache["census"] = None
        census_mod._cache["ts"] = 0.0
        _seed_canonical_convergences(self.db_path, count=5)
        second = _run_async(dashboard_operator())
        self.assertEqual(second["universe"]["convergences"], 5)


class TestDashboardObservatoryConvergenceCount(_IsolatedCanonicalRegistryMixin, unittest.TestCase):
    def test_cross_domain_total_matches_canonical_census(self):
        _seed_canonical_convergences(self.db_path, count=4)

        result = _run_async(dashboard_observatory())

        self.assertEqual(result["gravity"]["convergences_total"], 4)
        self.assertEqual(result["convergences"]["cross_domain"], 4)


if __name__ == "__main__":
    unittest.main()
