"""
Tests for the engine orchestration layer (core/orchestration).

Verifies the durable activation contract:
  - Default registry is well-formed (tiers, unique names, market gated).
  - activate_all is DEFENSIVE (a failing engine never aborts the run) and
    IDEMPOTENT (running twice is safe).
  - EXTERNAL engines are NEVER activated by the bootstrap (only health-checked).
  - gated_by engines are skipped unless their flag is truthy.
  - dry_run never calls any activate() hook.
  - get_engine_status is read-only.
"""
from __future__ import annotations

import pytest

from core.orchestration import engine_registry as reg
from core.orchestration import bootstrap as bs
from core.orchestration.engine_registry import EngineSpec, Tier


@pytest.fixture
def fake_registry(monkeypatch):
    """Replace the global registry with a controlled fake; restore after."""
    reg.clear()
    yield reg
    reg.clear()
    reg._register_defaults()


def _spec(name, tier, *, activate=None, health=None, gated_by=None, deps=()):
    return EngineSpec(name=name, group="test", tier=tier, deps=deps,
                      gated_by=gated_by, activate=activate, health=health)


# ---------------------------------------------------------------------------
# Default registry
# ---------------------------------------------------------------------------

class TestDefaultRegistry:
    def test_registry_populated(self):
        specs = reg.all_specs()
        assert len(specs) >= 30
        names = [s.name for s in specs]
        assert len(names) == len(set(names)), "nombres duplicados en el registro"

    def test_all_tiers_present(self):
        tiers = {s.tier for s in reg.all_specs()}
        assert {Tier.CORE, Tier.OBSERVE, Tier.GATED_INTERNAL, Tier.EXTERNAL} <= tiers

    def test_auto_executor_is_never_auto(self):
        spec = reg.get("auto_executor")
        assert spec is not None
        assert spec.tier == Tier.EXTERNAL
        assert spec.gated_by == "__never__"

    def test_market_group_is_external(self):
        for s in reg.by_group("mercado"):
            assert s.tier == Tier.EXTERNAL


# ---------------------------------------------------------------------------
# activate_all behaviour (fake registry)
# ---------------------------------------------------------------------------

class TestActivateAll:
    def test_defensive_one_failure_does_not_abort(self, fake_registry):
        calls = {"ok": 0}
        def ok_activate():
            calls["ok"] += 1
        def boom_activate():
            raise RuntimeError("boom")
        reg.register(_spec("good", Tier.CORE, activate=ok_activate, health=lambda: True))
        reg.register(_spec("bad", Tier.OBSERVE, activate=boom_activate, health=lambda: True))
        reg.register(_spec("plain", Tier.CORE, health=lambda: True))

        report = bs.activate_all("safe")

        statuses = {e["name"]: e["status"] for e in report["engines"]}
        assert statuses["good"] == bs.ACTIVATED
        assert statuses["bad"] == bs.FAILED       # failed, but did not abort
        assert statuses["plain"] == bs.HEALTHY    # processed after the failure
        assert calls["ok"] == 1

    def test_idempotent(self, fake_registry):
        reg.register(_spec("good", Tier.CORE, activate=lambda: None, health=lambda: True))
        r1 = bs.activate_all("safe")
        r2 = bs.activate_all("safe")
        assert r1["by_status"] == r2["by_status"]

    def test_external_never_activated(self, fake_registry):
        def must_not_call():
            raise AssertionError("EXTERNAL no debe activarse nunca desde el bootstrap")
        reg.register(_spec("ext", Tier.EXTERNAL, activate=must_not_call, health=lambda: True))
        report = bs.activate_all("full")  # incluso en 'full'
        st = {e["name"]: e["status"] for e in report["engines"]}
        assert st["ext"] == bs.HEALTHY

    def test_gated_skipped_when_flag_off(self, fake_registry, monkeypatch):
        monkeypatch.delenv("VX_TEST_FLAG", raising=False)
        called = {"n": 0}
        reg.register(_spec("g", Tier.GATED_INTERNAL,
                           activate=lambda: called.__setitem__("n", called["n"] + 1),
                           health=lambda: True, gated_by="VX_TEST_FLAG"))
        report = bs.activate_all("safe")
        st = {e["name"]: e["status"] for e in report["engines"]}
        assert st["g"] == bs.SKIPPED_GATED
        assert called["n"] == 0

    def test_gated_activated_when_flag_on(self, fake_registry, monkeypatch):
        monkeypatch.setenv("VX_TEST_FLAG", "1")
        called = {"n": 0}
        reg.register(_spec("g", Tier.GATED_INTERNAL,
                           activate=lambda: called.__setitem__("n", called["n"] + 1),
                           health=lambda: True, gated_by="VX_TEST_FLAG"))
        report = bs.activate_all("safe")
        st = {e["name"]: e["status"] for e in report["engines"]}
        assert st["g"] == bs.ACTIVATED
        assert called["n"] == 1

    def test_unavailable_when_health_false(self, fake_registry):
        reg.register(_spec("down", Tier.CORE,
                           activate=lambda: (_ for _ in ()).throw(AssertionError("no")),
                           health=lambda: False))
        report = bs.activate_all("safe")
        st = {e["name"]: e["status"] for e in report["engines"]}
        assert st["down"] == bs.UNAVAILABLE

    def test_health_exception_is_unavailable_not_crash(self, fake_registry):
        def bad_health():
            raise ValueError("nope")
        reg.register(_spec("h", Tier.CORE, activate=lambda: None, health=bad_health))
        report = bs.activate_all("safe")
        st = {e["name"]: e["status"] for e in report["engines"]}
        assert st["h"] == bs.UNAVAILABLE

    def test_dry_run_calls_no_activate(self, fake_registry):
        called = {"n": 0}
        reg.register(_spec("g", Tier.CORE,
                           activate=lambda: called.__setitem__("n", called["n"] + 1),
                           health=lambda: True))
        report = bs.activate_all("safe", dry_run=True)
        st = {e["name"]: e["status"] for e in report["engines"]}
        assert st["g"] == bs.READY
        assert called["n"] == 0
        assert report["dry_run"] is True

    def test_dependency_order(self, fake_registry):
        order = []
        reg.register(_spec("b", Tier.CORE, activate=lambda: order.append("b"),
                           health=lambda: True, deps=("a",)))
        reg.register(_spec("a", Tier.CORE, activate=lambda: order.append("a"),
                           health=lambda: True))
        bs.activate_all("safe")
        assert order.index("a") < order.index("b")


# ---------------------------------------------------------------------------
# Read-only status
# ---------------------------------------------------------------------------

class TestEngineStatus:
    def test_get_engine_status_is_readonly(self, fake_registry):
        def must_not_call():
            raise AssertionError("get_engine_status no debe activar")
        reg.register(_spec("x", Tier.CORE, activate=must_not_call, health=lambda: True))
        status = bs.get_engine_status()
        assert status["total"] == 1
        assert status["engines"][0]["name"] == "x"
        assert status["engines"][0]["available"] is True

    def test_default_status_runs(self):
        """Smoke: read-only status over the REAL registry must not raise."""
        status = bs.get_engine_status()
        assert status["total"] >= 30
        assert "engines" in status


class TestUniverseIntegration:
    def test_universe_snapshot_exposes_engines(self):
        """/v1/universe (to_api_dict) must include the additive 'engines' block."""
        from core.self_observation.universe_observer import UniverseSnapshot
        d = UniverseSnapshot().to_api_dict()
        assert "engines" in d
        assert d["engines"]["total"] >= 30
        assert "by_tier" in d["engines"]
