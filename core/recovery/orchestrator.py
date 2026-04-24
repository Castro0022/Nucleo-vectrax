"""
HealthOrchestrator — runs detectors on a schedule and dispatches events.

Design:
  - Each registered detector runs in its own daemon thread.
  - Detectors are read-only: they call their probe() and emit HealthEvent.
  - The orchestrator passes events to the executor AND records them in
    the ledger. It does not make recovery decisions.
  - snapshot() returns the latest event from each detector for the
    /v1/recovery/status endpoint.

Lifecycle:
  start() launches threads; stop() signals them to exit; both are idempotent.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, Optional

from core.recovery.events import HealthEvent, HealthState
from core.recovery import ledger as _ledger

logger = logging.getLogger("vectrax.recovery.orchestrator")

# Probe signature: callable that returns a HealthEvent (detector_id filled in
# by orchestrator if missing).
ProbeFn = Callable[[], HealthEvent]


class _DetectorRunner:
    """Thread that runs one probe at a fixed interval."""

    def __init__(
        self,
        detector_id: str,
        probe: ProbeFn,
        interval_s: int,
        dispatcher: Callable[[HealthEvent], None],
    ) -> None:
        self.detector_id = detector_id
        self.probe = probe
        self.interval_s = max(5, int(interval_s))  # hard floor 5s
        self.dispatcher = dispatcher
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._sequence = 0
        self._last_event: Optional[HealthEvent] = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            name=f"rec-det-{self.detector_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        # Initial jitter-free sleep to avoid all detectors firing at t=0.
        # Not a full jitter — just offset by a small fraction of interval.
        time.sleep(min(2.0, self.interval_s / 4))

        while self._running:
            try:
                event = self.probe()
                # Allow probes to construct events without knowing their own id.
                if event.detector_id != self.detector_id:
                    # Probes should set their id; we override to be safe.
                    event = HealthEvent(
                        detector_id=self.detector_id,
                        state=event.state,
                        evidence=event.evidence,
                        ts=event.ts,
                        sequence=self._sequence,
                    )
                else:
                    event = HealthEvent(
                        detector_id=event.detector_id,
                        state=event.state,
                        evidence=event.evidence,
                        ts=event.ts,
                        sequence=self._sequence,
                    )
                self._sequence += 1
                self._last_event = event
                self.dispatcher(event)
            except Exception as exc:
                logger.exception(
                    "Probe %s crashed — treating as DEGRADED: %s",
                    self.detector_id, exc,
                )
                event = HealthEvent(
                    detector_id=self.detector_id,
                    state=HealthState.DEGRADED,
                    evidence={"probe_exception": str(exc)},
                    sequence=self._sequence,
                )
                self._sequence += 1
                self._last_event = event
                # Don't dispatch probe crashes — they'd likely trigger on
                # infrastructure issues unrelated to the service being probed.

            # Sleep in small increments so stop() is responsive.
            for _ in range(self.interval_s * 2):
                if not self._running:
                    return
                time.sleep(0.5)

    def last_event(self) -> Optional[HealthEvent]:
        return self._last_event


class HealthOrchestrator:
    """Top-level coordinator for all detectors.

    Not thread-safe for concurrent register_detector calls AFTER start();
    register everything before calling start().
    """

    def __init__(self, executor_callback: Optional[Callable[[HealthEvent], None]] = None) -> None:
        self._runners: Dict[str, _DetectorRunner] = {}
        self._executor_callback = executor_callback
        self._started = False
        self._startup_ts: Optional[float] = None

    def set_executor(self, callback: Callable[[HealthEvent], None]) -> None:
        """Wire the executor after construction (breaks the circular dep)."""
        self._executor_callback = callback

    def register_detector(
        self,
        detector_id: str,
        probe: ProbeFn,
        interval_s: int,
    ) -> None:
        """Register a probe to be run every interval_s seconds.

        Must be called before start().  Calling after start() raises.
        """
        if self._started:
            raise RuntimeError(
                "Cannot register detectors after start(); register before start()."
            )
        if detector_id in self._runners:
            raise ValueError(f"Detector {detector_id!r} already registered")
        self._runners[detector_id] = _DetectorRunner(
            detector_id=detector_id,
            probe=probe,
            interval_s=interval_s,
            dispatcher=self._dispatch,
        )
        logger.info(
            "Detector registered: %s (interval=%ds)", detector_id, interval_s,
        )

    def _dispatch(self, event: HealthEvent) -> None:
        """Internal: called by each runner whenever its probe completes.

        Every event goes to the executor (not just BROKEN). The executor
        needs OK/DEGRADED to reset its per-strategy consecutive-BROKEN
        counter; without that context it can't enforce the
        2-consecutive-BROKEN gate that prevents flapping.
        """
        # Always write to ledger (cheap, append-only)
        _ledger.write_entry("event", event.to_dict())

        # Forward ALL events to executor (executor filters internally).
        if self._executor_callback is not None:
            try:
                self._executor_callback(event)
            except Exception as exc:
                logger.exception("Executor callback crashed on event: %s", exc)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._startup_ts = time.time()
        logger.info(
            "HealthOrchestrator starting with %d detectors", len(self._runners),
        )
        _ledger.write_entry(
            "startup",
            {"detectors": list(self._runners.keys())},
        )
        for runner in self._runners.values():
            runner.start()

    def stop(self) -> None:
        if not self._started:
            return
        logger.info("HealthOrchestrator stopping")
        _ledger.write_entry("shutdown", {})
        for runner in self._runners.values():
            runner.stop()
        self._started = False

    def snapshot(self) -> Dict[str, Optional[Dict]]:
        """Latest event from each detector, serialized.  For /status endpoint."""
        return {
            det_id: (runner.last_event().to_dict() if runner.last_event() else None)
            for det_id, runner in self._runners.items()
        }

    def is_running(self) -> bool:
        return self._started
