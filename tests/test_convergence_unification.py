"""
Tests — Unificación de rutas de convergencia (#107 → consumidores).

Verifica que Censo (universe_census), Self-Aware (self_context), Self-Knowledge
(self_observation.self_knowledge) y Reportes (system_report) obtienen la MISMA
convergencia/estado desde la única autoridad canónica
(core.learn.convergence_registry), sin ninguno de ellos leyendo ya
core.learn.convergence_history o candidatos crudos de
core.learn.gravity_engine.cross_domain_convergences().

Run:  python -m pytest tests/test_convergence_unification.py -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.learn.convergence_registry import (  # noqa: E402
    record_convergence_snapshot,
)


class _TempCanonicalRegistryMixin:
    """Seeds ONE canonical convergence into a temp registry DB and points
    core.learn.convergence_registry.DB_PATH at it for the duration of the
    test, so every consumer that calls the module-level functions without an
    explicit db_path reads the same seeded state."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="vectrax_test_conv_unify_")
        self.db_path = os.path.join(self.tmpdir, "convergence_history.db")

        self.live = {"market:AAPL": "market", "freight_logistics:lane_x": "freight_logistics"}
        record_convergence_snapshot(
            [{
                "type": "intent_overlap",
                "star_a": "market:AAPL", "star_b": "freight_logistics:lane_x",
                "combined_cc": 0.62, "combined_hits": 9,
                "domains": ["market", "freight_logistics"],
            }],
            self.live, self.db_path,
        )

        import core.learn.convergence_registry as registry_mod
        self._orig_db_path = registry_mod.DB_PATH
        registry_mod.DB_PATH = self.db_path

        # universe_census TTL-caches at module scope — reset so a previous
        # test's cached result never leaks into these assertions.
        import core.universe_census as census_mod
        self._orig_census_cache = dict(census_mod._cache)
        census_mod._cache["census"] = None
        census_mod._cache["ts"] = 0.0

    def tearDown(self):
        import core.learn.convergence_registry as registry_mod
        registry_mod.DB_PATH = self._orig_db_path
        import core.universe_census as census_mod
        census_mod._cache.update(self._orig_census_cache)
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestCensusSelfAwareConsistency(_TempCanonicalRegistryMixin, unittest.TestCase):
    """Censo y Self-Aware deben reportar el mismo total canónico (1)."""

    def test_census_reports_the_seeded_convergence(self):
        import core.universe_census as census_mod
        c = census_mod._build_census()
        self.assertEqual(c.convergences, 1)
        self.assertEqual(c.convergences_active, 1)
        self.assertEqual(c.convergence_confirmations_total, 1)

    def test_self_context_convergence_block_reflects_same_registry(self):
        """vectrax.self_context's convergence block now reads
        convergence_registry.build_context() — never convergence_history."""
        from core.learn.convergence_registry import build_context

        text = build_context(limit=5)
        self.assertIn("market:AAPL", text)
        self.assertIn("1 nacimientos", text)

    def test_universe_observer_convergences_list_uses_canonical_source(self):
        from core.self_observation.universe_observer import UniverseSnapshot, _collect_convergences

        snap = UniverseSnapshot()
        with patch("vectrax.graph.get_graph", side_effect=RuntimeError("no graph in test")):
            _collect_convergences(snap)

        canonical_entries = [c for c in snap.convergences if c.get("source") == "canonical"]
        self.assertEqual(len(canonical_entries), 1)
        # record_convergence_snapshot() sorts entities by (type, domain, id)
        # before persisting, so entity_a/entity_b order is deterministic but
        # not necessarily the caller's original star_a/star_b order.
        pair = {canonical_entries[0]["star_a"], canonical_entries[0]["star_b"]}
        self.assertEqual(pair, {"market:AAPL", "freight_logistics:lane_x"})
        # No legacy-tagged entries must ever appear post-unification.
        self.assertFalse(any(c.get("source") == "history" for c in snap.convergences))


class TestSelfKnowledgeConsistency(_TempCanonicalRegistryMixin, unittest.TestCase):
    """Self-Knowledge (trace_provenance) debe citar la misma convergencia
    canónica, no la ruta legacy."""

    def test_trace_provenance_cites_canonical_convergence(self):
        from core.self_observation import self_knowledge as SK

        SK.invalidate_cache()
        with patch("core.learn.gravity_engine.get_gravity_index") as mock_gi:
            mock_gi.return_value.top_stars.return_value = []
            prov = SK.trace_provenance(domain="market", topic_tokens=["AAPL"])

        convs = prov["evidence"].get("convergences") or []
        self.assertTrue(any(c.get("intent") == "intent_overlap" for c in convs))


class TestSystemReportConsistency(_TempCanonicalRegistryMixin, unittest.TestCase):
    """system_report.get_global_state()['convergences']['history'] debe
    derivarse de convergence_lifecycle_events (misma autoridad canónica),
    conservado por compatibilidad tal como pide el punto 5."""

    def test_global_state_history_matches_lifecycle_events(self):
        from core import system_report as SR

        with patch("core.universe_census.get_census") as mock_census:
            mock_census.return_value.total = 1
            mock_census.return_value.gravitational = 0
            mock_census.return_value.knowledge = 0
            mock_census.return_value.users = 0
            mock_census.return_value.domains = {}
            mock_census.return_value.convergences = 1
            mock_census.return_value.patterns = 0
            mock_census.return_value.constellations = 0
            mock_census.return_value.mass_total = 0.0
            mock_census.return_value.word_gravity_count = 0
            mock_census.return_value.users_total = 0
            mock_census.return_value.interactions = 0
            mock_census.return_value.user_facts = 0
            mock_census.return_value.teams = 0

            state = SR.get_global_state()

        self.assertEqual(state["convergences"]["history"]["births"], 1)
        self.assertEqual(state["convergences"]["history"]["dissolutions"], 0)
        self.assertEqual(state["convergences"]["history"]["active"], 1)


class TestNoLegacyOrRawCandidateReadsRemain(unittest.TestCase):
    """Comprobación estática: los cuatro lectores migrados ya no importan
    core.learn.convergence_history ni llaman a
    gravity_engine.cross_domain_convergences() para servir datos a
    consumidores."""

    def test_migrated_modules_do_not_reference_convergence_history(self):
        import core.trend_reader as trend_reader
        import core.system_report as system_report
        import core.self_observation.self_knowledge as self_knowledge
        import core.self_observation.universe_observer as universe_observer
        import vectrax.self_context as self_context

        for mod in (
            trend_reader, system_report, self_knowledge,
            universe_observer, self_context,
        ):
            source = open(mod.__file__, encoding="utf-8").read()
            self.assertNotIn(
                "from core.learn.convergence_history import", source,
                f"{mod.__name__} still imports from the legacy convergence_history module",
            )

    def test_universe_observer_to_api_dict_never_mixes_raw_gravity_candidates(self):
        import core.self_observation.universe_observer as universe_observer

        source = open(universe_observer.__file__, encoding="utf-8").read()
        self.assertNotIn("self.gravity_convergences + self.convergences", source)


if __name__ == "__main__":
    unittest.main()
