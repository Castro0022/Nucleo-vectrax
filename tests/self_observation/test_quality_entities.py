"""
tests/self_observation/test_quality_entities.py — hermetic tests for the
"quality as universe phenomena" layer (architecture Pillar D).

We record synthetic quality/test/code/runtime observations into the per-test,
conftest-redirected observation ledger and assert they surface as living
universe entities through the three seams:

  - core.self_observation.quality_entities  (the single-source-of-truth mapper)
  - core.universe_census                     (counts: SSOT for totals)
  - core.self_observation.universe_observer  (detailed entities in /v1/universe)

The autouse ``_hermetic_base`` fixture (tests/conftest.py) redirects the ledger
DB to a temp vault, so these tests never read or mutate real machine state /
creator memory.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def seeded_ledger():
    """Init the (hermetic) ledger and seed a representative set of rows.

    Insertion order matters: the ledger returns rows newest-first, and a
    recovery should cool an *earlier* failure for the same module.
    """
    from core.self_observation import observation_ledger as ol
    ol.init_ledger()

    # 1) A critical test failure on a module (oldest row).
    ol.record(
        "test", "test_failure", "auth login test broke",
        star_id="tests/test_auth.py",
        evidence={"module": "auth/login.py", "risk": 0.9, "count": 4},
        severity="critical",
    )
    # 2) ...later recovered (newer row) -> should cool the failure above.
    ol.record(
        "test", "test_recovery", "auth login green again",
        evidence={"module": "auth/login.py"}, severity="info",
    )
    # 3) An active (un-recovered) runtime warning on a different module.
    ol.record(
        "runtime", "runtime_error", "worker timeout",
        evidence={"module": "worker.py"}, severity="warning",
    )
    # 4) A code-change event.
    ol.record(
        "code", "code_change", "refactor pipeline",
        evidence={"module": "core/pipeline.py"}, severity="info",
    )
    # 5) A cross-module root-cause convergence.
    ol.record(
        "quality", "root_cause_convergence", "shared dependency failure",
        evidence={"modules": ["auth/login.py", "core/pipeline.py"]},
        severity="warning",
    )
    # 6) Noise from a NON-quality domain -> must be excluded from phenomena.
    ol.record("market", "market_activity", "BTC moved", severity="info")
    return ol


# ---------------------------------------------------------------------------
# Mapper (quality_entities) — classification, brightness, cooling
# ---------------------------------------------------------------------------

def test_classification_and_counts(seeded_ledger):
    from core.self_observation.quality_entities import get_quality_entities
    c = get_quality_entities()["counts"]
    assert c["failures"] == 2
    assert c["active_failures"] == 1        # only worker.py is still active
    assert c["recoveries"] == 1
    assert c["code_changes"] == 1
    assert c["convergences"] == 1
    assert c["total"] == 5                  # market-domain noise excluded


def test_failure_brightness_tracks_severity(seeded_ledger):
    from core.self_observation.quality_entities import get_quality_entities
    failures = {f["module"]: f for f in get_quality_entities()["failures"]}

    assert failures["login.py"]["severity"] == "critical"
    assert failures["login.py"]["brightness"] == pytest.approx(1.0)
    assert failures["worker.py"]["severity"] == "warning"
    assert failures["worker.py"]["brightness"] == pytest.approx(0.65)
    # Brightness must order by risk/severity.
    assert failures["login.py"]["brightness"] > failures["worker.py"]["brightness"]


def test_recovery_cools_matching_failure(seeded_ledger):
    from core.self_observation.quality_entities import get_quality_entities
    failures = {f["module"]: f for f in get_quality_entities()["failures"]}
    assert failures["login.py"]["cooling"] is True      # has a later recovery
    assert failures["worker.py"]["cooling"] is False     # no recovery -> active


def test_convergence_is_cross_module(seeded_ledger):
    from core.self_observation.quality_entities import get_quality_entities
    convs = get_quality_entities()["convergences"]
    assert len(convs) == 1
    mods = convs[0]["modules"]
    assert "auth/login.py" in mods
    assert "core/pipeline.py" in mods


def test_empty_ledger_is_defensive():
    """No seeding: the hermetic ledger is empty -> well-formed zeros, no raise."""
    from core.self_observation import observation_ledger as ol
    from core.self_observation.quality_entities import (
        get_quality_entities, get_quality_summary,
    )
    ol.init_ledger()
    q = get_quality_entities()
    assert q["failures"] == []
    assert q["recoveries"] == []
    assert q["events"] == []
    assert q["convergences"] == []
    assert get_quality_summary()["total"] == 0


def test_info_note_without_risk_is_not_a_phenomenon():
    """A plain info quality note (no failure/recovery/code/convergence keyword)
    is counted in the total but produces no entity — keeps the universe clean."""
    from core.self_observation import observation_ledger as ol
    from core.self_observation.quality_entities import get_quality_entities
    ol.init_ledger()
    ol.record("quality", "note", "just an informational note", severity="info")
    q = get_quality_entities()
    assert q["counts"]["total"] == 1
    assert q["counts"]["failures"] == 0
    assert q["failures"] == [] and q["convergences"] == []


# ---------------------------------------------------------------------------
# Census — counts are the single source of truth for totals
# ---------------------------------------------------------------------------

def test_census_exposes_quality_counts(seeded_ledger):
    # _build_census bypasses the 10s TTL cache so the assertion is deterministic.
    from core.universe_census import _build_census
    quality = _build_census().to_dict()["quality"]
    assert quality["failures"] == 2
    assert quality["active_failures"] == 1
    assert quality["recoveries"] == 1
    assert quality["code_changes"] == 1
    assert quality["convergences"] == 1


# ---------------------------------------------------------------------------
# /v1/universe — detailed entities for the living canvas
# ---------------------------------------------------------------------------

def test_universe_api_exposes_quality_entities(seeded_ledger):
    from core.self_observation.universe_observer import observe_universe
    api = observe_universe().to_api_dict()

    assert "quality" in api
    qd = api["quality"]
    assert {"failures", "recoveries", "events", "convergences", "counts"} <= set(qd)
    assert qd["counts"]["active_failures"] == 1
    assert len(qd["events"]) == 1                       # the code change
    assert any(c["modules"] for c in qd["convergences"])  # cross-module cluster
    # Every failure entity carries a brightness for the canvas (brightness ~ severity).
    assert all("brightness" in f for f in qd["failures"])


def test_universe_quality_signals_surface_active_risk(seeded_ledger):
    """Active failures / root causes must raise perception-layer signals."""
    from core.self_observation.universe_observer import observe_universe
    snap = observe_universe()
    assert "quality_failures_active" in snap.signals
    assert "quality_root_cause_detected" in snap.signals
