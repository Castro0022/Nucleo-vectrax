"""
tests/plugins/ledger_plugin.py — opt-in pytest self-observation plugin.

Vectrax observes its own engineering health. When enabled, this plugin turns
each pytest RUN into structured observations in the observation ledger (via the
``core.self_observation.quality_observer`` contract):

    suite_start     — a run began (run_id)
    failed_test     — a test failed (nodeid, file, cause CLASS, phase, duration)
    recovered_test  — a test that failed in the PREVIOUS recorded run now passes
    suite_end       — run totals (passed/failed/skipped/errors, duration, exit)

DESIGN / GOVERNANCE
-------------------
* OPT-IN. The plugin only activates when ``VECTRAX_TEST_LEDGER=1`` (also accepts
  ``true``/``yes``/``on``). Otherwise nothing is registered, so the default
  hermetic quality gate is never touched.
* RESPECTS ``VECTRAX_VAULT_DIR``. Writes go only to ``$VECTRAX_VAULT_DIR/
  observation_ledger.db`` (captured at configure time). Because the repo's
  autouse hermetic fixture re-points the ledger to a per-test temp DB during
  each test, every write here is wrapped so the ledger path is pinned to the
  run-level vault and then restored — this never disturbs per-test isolation.
* ABSTRACT ONLY. All payloads flow through quality_observer's sanitizer, so no
  prompts/secrets/personal data or assertion values are ever stored — only the
  exception CLASS name, file paths, counts and durations.

REGISTRATION (the orchestrator wires this; this plugin does NOT self-register
globally and installs no git hooks):
    # pyproject.toml -> [tool.pytest.ini_options]
    #   addopts = "-p no:cacheprovider -p tests.plugins.ledger_plugin"
    # or root conftest.py:  pytest_plugins = ["tests.plugins.ledger_plugin"]
    # then run with:        VECTRAX_TEST_LEDGER=1 VECTRAX_VAULT_DIR=/path pytest
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
import uuid
from typing import Any, Dict, Optional, Set

logger = logging.getLogger("vectrax.tests.ledger_plugin")

ENABLE_ENV = "VECTRAX_TEST_LEDGER"
_TRUE = {"1", "true", "yes", "on"}


def _enabled() -> bool:
    return os.environ.get(ENABLE_ENV, "").strip().lower() in _TRUE


# ---------------------------------------------------------------------------
# The reporter (registered as a plugin object when enabled)
# ---------------------------------------------------------------------------

class LedgerReporter:
    """Collects pytest lifecycle signals and records them as observations.

    Constructed with an explicit ``vault_dir`` (used by tests) or, when omitted,
    resolves the vault from ``$VECTRAX_VAULT_DIR`` (falling back to the ledger's
    own configured path). The instance is drivable directly in unit tests by
    calling its hook methods with lightweight report/call/session objects.
    """

    def __init__(self, vault_dir: Optional[str] = None, run_id: Optional[str] = None):
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self._db_path = self._resolve_db_path(vault_dir)
        # counters
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.errors = 0
        self.collected: Optional[int] = None
        # bookkeeping
        self._cause: Dict[str, str] = {}        # nodeid -> exception class
        self._skip_nodes: Set[str] = set()
        self._error_nodes: Set[str] = set()
        self._prev_failed: Set[str] = set()     # nodeids that failed last run
        self._prev_run_id: Optional[str] = None
        self._recovered_emitted: Set[str] = set()
        self._t0 = time.time()

    # -- ledger path handling ------------------------------------------------

    @staticmethod
    def _resolve_db_path(vault_dir: Optional[str]) -> str:
        from core.self_observation import observation_ledger as _ol

        vault = vault_dir or os.environ.get("VECTRAX_VAULT_DIR")
        if vault:
            return os.path.join(vault, "observation_ledger.db")
        # No explicit vault: use whatever the ledger already targets.
        return getattr(_ol, "_DB_PATH", "")

    @contextlib.contextmanager
    def _pinned_ledger(self):
        """Pin the ledger DB path to the run-level vault for one write, then
        restore it so the per-test hermetic redirection is left intact."""
        from core.self_observation import observation_ledger as _ol

        prev = getattr(_ol, "_DB_PATH", None)
        try:
            if self._db_path:
                _ol._DB_PATH = self._db_path
            yield
        finally:
            _ol._DB_PATH = prev

    def _qo(self):
        from core.self_observation import quality_observer as qo

        return qo

    # -- prev-run recovery memory -------------------------------------------

    def _load_prev_failed(self) -> None:
        """Read the most recent PRIOR run's failed nodeids from the ledger so we
        can flag recoveries this run. Best-effort; never raises."""
        try:
            from core.self_observation import observation_ledger as _ol

            with self._pinned_ledger():
                self._qo()._ensure_init()
                rows = _ol.get_by_domain("test", limit=1000)
            prev_run: Optional[str] = None
            failed: Set[str] = set()
            for row in rows:  # rows are newest-first (id DESC)
                if row.get("obs_type") != "failed_test":
                    continue
                ev = row.get("evidence") or {}
                rid = ev.get("run_id")
                if rid == self.run_id:
                    continue  # ignore current run (shouldn't exist yet)
                if prev_run is None:
                    prev_run = rid
                if rid == prev_run:
                    node = ev.get("nodeid")
                    if node:
                        failed.add(node)
            self._prev_failed = failed
            self._prev_run_id = prev_run
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("load_prev_failed failed: %s", exc)
            self._prev_failed = set()

    # -- pytest hooks --------------------------------------------------------

    def pytest_sessionstart(self, session: Any = None) -> None:
        self._t0 = time.time()
        self._load_prev_failed()
        try:
            with self._pinned_ledger():
                self._qo().record_suite_start(self.run_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("suite_start record failed: %s", exc)

    def pytest_collection_finish(self, session: Any) -> None:
        try:
            self.collected = len(getattr(session, "items", []) or [])
        except Exception:  # pragma: no cover - defensive
            self.collected = None

    def pytest_runtest_makereport(self, item, call):
        """Capture the exception CLASS NAME (only) for a failing phase.

        This hook runs BEFORE ``pytest_runtest_logreport`` (unlike
        ``pytest_exception_interact``, which fires after), so the cause class is
        known by the time we emit ``failed_test``. Returns None so pytest's
        builtin report is the one that's actually used.

        NOTE: hook params must NOT have defaults — pluggy only injects arguments
        for parameters declared without a default value.
        """
        excinfo = getattr(call, "excinfo", None)
        if excinfo is not None:
            self._note_cause_from_excinfo(getattr(item, "nodeid", None), excinfo)
        return None

    def pytest_exception_interact(self, node, call, report=None) -> None:
        """Fallback capture for non-runtest interactions (e.g. collection).

        ``node`` and ``call`` are injected by pluggy (no defaults); ``report``
        is optional since the node already carries the nodeid.
        """
        nodeid = getattr(report, "nodeid", None) or getattr(node, "nodeid", None)
        self._note_cause_from_excinfo(nodeid, getattr(call, "excinfo", None))

    def _note_cause_from_excinfo(self, nodeid: Any, excinfo: Any) -> None:
        """Store ONLY the exception class name (never the message/values)."""
        if not nodeid or excinfo is None:
            return
        name = getattr(excinfo, "typename", None)
        if not name:
            typ = getattr(excinfo, "type", None)
            name = getattr(typ, "__name__", None)
        if name:
            self._cause[str(nodeid)] = str(name)

    def pytest_runtest_logreport(self, report: Any) -> None:
        when = getattr(report, "when", None)
        nodeid = getattr(report, "nodeid", "") or ""
        duration = float(getattr(report, "duration", 0.0) or 0.0)

        # Skips (count once; usually reported at setup).
        if getattr(report, "skipped", False) and when in ("setup", "call"):
            if nodeid not in self._skip_nodes:
                self._skip_nodes.add(nodeid)
                self.skipped += 1
            return

        if when == "call":
            if getattr(report, "passed", False):
                self.passed += 1
                self._maybe_recovered(nodeid)
            elif getattr(report, "failed", False):
                self.failed += 1
                self._emit_failed(nodeid, "call", duration)
        elif when in ("setup", "teardown"):
            if getattr(report, "failed", False):
                if nodeid not in self._error_nodes:
                    self._error_nodes.add(nodeid)
                    self.errors += 1
                self._emit_failed(nodeid, when, duration)

    def _emit_failed(self, nodeid: str, phase: str, duration: float) -> None:
        try:
            with self._pinned_ledger():
                self._qo().record_failed_test(
                    nodeid, self.run_id,
                    cause_class=self._cause.get(nodeid, "unknown"),
                    phase=phase,
                    duration_s=duration,
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("failed_test record failed: %s", exc)

    def _maybe_recovered(self, nodeid: str) -> None:
        if nodeid in self._prev_failed and nodeid not in self._recovered_emitted:
            self._recovered_emitted.add(nodeid)
            try:
                with self._pinned_ledger():
                    self._qo().record_recovered_test(
                        nodeid, self.run_id, prev_run_id=self._prev_run_id,
                    )
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("recovered_test record failed: %s", exc)

    def pytest_sessionfinish(self, exitstatus, session=None) -> None:
        # exitstatus has no default so pluggy injects it; session is unused.
        duration = round(time.time() - self._t0, 3)
        total = (self.collected
                 if self.collected is not None
                 else self.passed + self.failed + self.skipped + self.errors)
        try:
            with self._pinned_ledger():
                self._qo().record_suite_end(
                    self.run_id,
                    total=total,
                    passed=self.passed,
                    failed=self.failed,
                    skipped=self.skipped,
                    errors=self.errors,
                    duration_s=duration,
                    exit_status=(int(exitstatus) if exitstatus is not None else None),
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("suite_end record failed: %s", exc)


# ---------------------------------------------------------------------------
# Plugin registration entry points
# ---------------------------------------------------------------------------

def pytest_configure(config: Any) -> None:
    if not _enabled():
        return
    try:
        # Honor VECTRAX_VAULT_DIR up front so the ledger path is bound even for
        # code paths that read the module global directly.
        from core.self_observation import quality_observer as qo

        qo.bind_vault_dir()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("ledger plugin: quality_observer unavailable: %s", exc)
        return
    reporter = LedgerReporter()
    config.pluginmanager.register(reporter, "vectrax_ledger_reporter")
    config._vectrax_ledger_reporter = reporter


def pytest_unconfigure(config: Any) -> None:
    reporter = getattr(config, "_vectrax_ledger_reporter", None)
    if reporter is not None:
        try:
            config.pluginmanager.unregister(reporter)
        except Exception:  # pragma: no cover - defensive
            pass
        try:
            del config._vectrax_ledger_reporter
        except Exception:  # pragma: no cover - defensive
            pass
