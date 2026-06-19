"""
Hermetic tests for the opt-in self-observation pytest plugin.

The plugin is exercised WITHOUT a nested pytest session: we drive the
``LedgerReporter`` hook methods directly with lightweight fake report/call/
session objects. All writes are pinned to a temp vault, so nothing touches the
real ledger.
"""
from __future__ import annotations

import types

import pytest

import core.self_observation.observation_ledger as ledger
from tests.plugins import ledger_plugin as lp


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

def _report(nodeid, when, outcome, duration=0.01):
    return types.SimpleNamespace(
        nodeid=nodeid,
        when=when,
        duration=duration,
        outcome=outcome,
        passed=(outcome == "passed"),
        failed=(outcome == "failed"),
        skipped=(outcome == "skipped"),
    )


def _excall(typename):
    return types.SimpleNamespace(
        excinfo=types.SimpleNamespace(typename=typename, type=None)
    )


def _read(reporter, domain="test", limit=200):
    with reporter._pinned_ledger():
        return ledger.get_by_domain(domain, limit=limit)


# ---------------------------------------------------------------------------
# enable gate
# ---------------------------------------------------------------------------

class TestEnableGate:
    @pytest.mark.parametrize("val,expected", [
        ("1", True), ("true", True), ("YES", True), ("on", True),
        ("0", False), ("false", False), ("", False),
    ])
    def test_enabled(self, monkeypatch, val, expected):
        monkeypatch.setenv(lp.ENABLE_ENV, val)
        assert lp._enabled() is expected

    def test_enabled_unset(self, monkeypatch):
        monkeypatch.delenv(lp.ENABLE_ENV, raising=False)
        assert lp._enabled() is False


# ---------------------------------------------------------------------------
# event emission over one run
# ---------------------------------------------------------------------------

class TestReporterRun:
    def _drive_run(self, tmp_path, run_id="runA"):
        r = lp.LedgerReporter(vault_dir=str(tmp_path), run_id=run_id)
        r.pytest_sessionstart()
        # failing test: makereport runs before logreport and captures the cause
        fail_id = "tests/test_x.py::test_fail"
        r.pytest_runtest_makereport(
            item=types.SimpleNamespace(nodeid=fail_id),
            call=_excall("ValueError"),
        )
        r.pytest_runtest_logreport(_report(fail_id, "call", "failed", 0.1))
        # passing test
        r.pytest_runtest_logreport(_report("tests/test_x.py::test_ok", "call", "passed"))
        # skipped test (reported at setup)
        r.pytest_runtest_logreport(_report("tests/test_x.py::test_skip", "setup", "skipped"))
        r.pytest_collection_finish(types.SimpleNamespace(items=[1, 2, 3]))
        r.pytest_sessionfinish(exitstatus=1)
        return r

    def test_emits_suite_start_and_end(self, tmp_path):
        r = self._drive_run(tmp_path)
        rows = _read(r)
        kinds = {row["obs_type"] for row in rows}
        assert "suite_start" in kinds
        assert "suite_end" in kinds

    def test_suite_end_totals(self, tmp_path):
        r = self._drive_run(tmp_path)
        end = [x for x in _read(r) if x["obs_type"] == "suite_end"][0]
        ev = end["evidence"]
        assert ev["passed"] == 1
        assert ev["failed"] == 1
        assert ev["skipped"] == 1
        assert ev["total"] == 3          # from collection_finish
        assert ev["exit_status"] == 1
        assert ev["success"] is False
        assert end["severity"] == "info"  # summary stays neutral

    def test_failed_test_records_cause_class_only(self, tmp_path):
        r = self._drive_run(tmp_path)
        failed = [x for x in _read(r) if x["obs_type"] == "failed_test"]
        assert len(failed) == 1
        ev = failed[0]["evidence"]
        assert ev["cause_class"] == "ValueError"
        assert ev["file"] == "tests/test_x.py"
        assert ev["phase"] == "call"
        assert failed[0]["severity"] == "warning"

    def test_exception_interact_fallback_records_cause(self, tmp_path):
        r = lp.LedgerReporter(vault_dir=str(tmp_path))
        r.pytest_exception_interact(
            node=types.SimpleNamespace(nodeid="t/x.py::c"),
            call=_excall("KeyError"),
        )
        assert r._cause["t/x.py::c"] == "KeyError"

    def test_no_recovery_without_prior_run(self, tmp_path):
        r = self._drive_run(tmp_path)
        assert [x for x in _read(r) if x["obs_type"] == "recovered_test"] == []


# ---------------------------------------------------------------------------
# cross-run recovery memory
# ---------------------------------------------------------------------------

def test_recovery_detected_across_runs(tmp_path):
    node = "tests/test_y.py::test_a"

    # Run 1: the test fails.
    r1 = lp.LedgerReporter(vault_dir=str(tmp_path), run_id="r1")
    r1.pytest_sessionstart()
    r1.pytest_runtest_makereport(
        item=types.SimpleNamespace(nodeid=node),
        call=_excall("AssertionError"),
    )
    r1.pytest_runtest_logreport(_report(node, "call", "failed"))
    r1.pytest_sessionfinish(exitstatus=1)

    # Run 2 (same vault): the same test now passes → recovery.
    r2 = lp.LedgerReporter(vault_dir=str(tmp_path), run_id="r2")
    r2.pytest_sessionstart()
    assert node in r2._prev_failed          # loaded prior-run failure
    assert r2._prev_run_id == "r1"
    r2.pytest_runtest_logreport(_report(node, "call", "passed"))
    r2.pytest_sessionfinish(exitstatus=0)

    recovered = [x for x in _read(r2) if x["obs_type"] == "recovered_test"]
    assert len(recovered) == 1
    ev = recovered[0]["evidence"]
    assert ev["nodeid"] == node
    assert ev["run_id"] == "r2"
    assert ev["prev_run_id"] == "r1"


# ---------------------------------------------------------------------------
# ledger path pinning never leaks
# ---------------------------------------------------------------------------

def test_pinned_ledger_restores_path(tmp_path):
    r = lp.LedgerReporter(vault_dir=str(tmp_path))
    ledger._DB_PATH = "/sentinel/observation_ledger.db"
    with r._pinned_ledger():
        assert ledger._DB_PATH == r._db_path
    assert ledger._DB_PATH == "/sentinel/observation_ledger.db"


# ---------------------------------------------------------------------------
# registration entry points
# ---------------------------------------------------------------------------

class _FakePM:
    def __init__(self):
        self.registered = []

    def register(self, obj, name=None):
        self.registered.append((obj, name))

    def unregister(self, obj):
        self.registered = [(o, n) for (o, n) in self.registered if o is not obj]


class _FakeConfig:
    def __init__(self):
        self.pluginmanager = _FakePM()


def test_configure_noop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv(lp.ENABLE_ENV, raising=False)
    cfg = _FakeConfig()
    lp.pytest_configure(cfg)
    assert cfg.pluginmanager.registered == []
    assert not hasattr(cfg, "_vectrax_ledger_reporter")


def test_configure_registers_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv(lp.ENABLE_ENV, "1")
    cfg = _FakeConfig()
    lp.pytest_configure(cfg)
    assert len(cfg.pluginmanager.registered) == 1
    assert isinstance(cfg._vectrax_ledger_reporter, lp.LedgerReporter)
    # and unconfigure cleans up
    lp.pytest_unconfigure(cfg)
    assert cfg.pluginmanager.registered == []
    assert not hasattr(cfg, "_vectrax_ledger_reporter")
