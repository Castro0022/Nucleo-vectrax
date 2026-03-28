"""
Vectrax Silent Watchdog
========================
Watches Python files in the repo and runs the ingestion pipeline
on every change.  Read-only: never writes to source directories.

Uses the ``watchdog`` library for filesystem events.
Daemon mode: PID stored at ``~/.vectrax/watchdog.pid``.
Silent: no popups — results only via ``vx learn report``.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from typing import List, Optional

from core.learn import PROJECT_ROOT, WATCHED_DIRS, RUNTIME_DIR, WATCHDOG_PID_PATH

logger = logging.getLogger("vectrax.watchdog")


# ---------------------------------------------------------------------------
# Filesystem event handler
# ---------------------------------------------------------------------------

def _make_handler():
    """Create a watchdog event handler (lazy import)."""
    from watchdog.events import FileSystemEventHandler

    class VectraxHandler(FileSystemEventHandler):
        """Handles .py file changes by running ingestion."""

        def __init__(self, use_semantic: bool = True):
            super().__init__()
            self.use_semantic = use_semantic
            self._last_seen: dict[str, float] = {}

        def _should_process(self, path: str) -> bool:
            if not path.endswith(".py"):
                return False
            if "__pycache__" in path:
                return False
            # Debounce: skip if same file changed within last 2s
            now = time.time()
            last = self._last_seen.get(path, 0.0)
            if now - last < 2.0:
                return False
            self._last_seen[path] = now
            return True

        def on_modified(self, event):
            if event.is_directory:
                return
            if self._should_process(event.src_path):
                self._ingest(event.src_path)

        def on_created(self, event):
            if event.is_directory:
                return
            if self._should_process(event.src_path):
                self._ingest(event.src_path)

        def _ingest(self, path: str):
            try:
                from core.learn.ingestion import ingest_file
                result = ingest_file(path, use_semantic=self.use_semantic)
                if not result.skipped:
                    logger.info(
                        "Ingested %s → %s (conf=%s)",
                        result.file_path, result.intent_primary, result.candidate_id,
                    )
                    # Emit watchdog event to Universal Bus
                    try:
                        from core.operator.universal_bus import get_universal_bus
                        from core.operator.bus_reactor import LiveChannels, EventTypes
                        get_universal_bus().emit(
                            channel=LiveChannels.WATCHDOG,
                            event_type=EventTypes.WATCHDOG_INGESTED,
                            source_layer=2,
                            payload={
                                "path": result.file_path,
                                "intent": result.intent_primary or "unknown",
                                "candidate_id": result.candidate_id or "",
                            },
                        )
                    except Exception:
                        pass  # bus hook is non-fatal
                    # Feed active learning if enabled
                    try:
                        from core.learn.active_learning import get_orchestrator
                        orch = get_orchestrator()
                        if orch.is_active():
                            orch.on_file_ingested(
                                file_path=result.file_path,
                                intent=result.intent_primary or "unknown",
                                confidence=result.cc_score,
                            )
                    except Exception:
                        pass  # active learning hook is non-fatal
            except Exception as e:
                logger.error("Ingestion error for %s: %s", path, e)

    return VectraxHandler


# ---------------------------------------------------------------------------
# Watch directories
# ---------------------------------------------------------------------------

def _get_watch_paths() -> List[str]:
    """Return absolute paths of directories to watch."""
    paths = []
    for d in WATCHED_DIRS:
        abs_d = os.path.join(PROJECT_ROOT, d)
        if os.path.isdir(abs_d):
            paths.append(abs_d)
    code_dir = os.path.join(PROJECT_ROOT, "code")
    if os.path.isdir(code_dir):
        paths.append(code_dir)
    return paths


# ---------------------------------------------------------------------------
# PID management
# ---------------------------------------------------------------------------

def _write_pid() -> None:
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    with open(WATCHDOG_PID_PATH, "w") as f:
        f.write(str(os.getpid()))


def _read_pid() -> Optional[int]:
    if not os.path.isfile(WATCHDOG_PID_PATH):
        return None
    try:
        with open(WATCHDOG_PID_PATH) as f:
            return int(f.read().strip())
    except (ValueError, OSError):
        return None


def _remove_pid() -> None:
    try:
        os.unlink(WATCHDOG_PID_PATH)
    except OSError:
        pass


def is_running() -> bool:
    """Check if a watchdog process is currently running."""
    pid = _read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, 0)  # signal 0 = check existence
        return True
    except OSError:
        _remove_pid()
        return False


# ---------------------------------------------------------------------------
# Start / Stop
# ---------------------------------------------------------------------------

def start_watcher(
    foreground: bool = False,
    use_semantic: bool = True,
) -> int:
    """
    Start the file watcher.

    Args:
        foreground: If True, run in foreground (blocking).
                    If False, fork a background daemon process.
        use_semantic: Pass through to ingestion pipeline.

    Returns:
        PID of the watcher process.
    """
    if is_running():
        pid = _read_pid()
        logger.info("Watchdog already running (PID %s)", pid)
        return pid

    if not foreground:
        # Fork a child process
        child_pid = os.fork()
        if child_pid > 0:
            # Parent: return child PID
            return child_pid
        # Child: detach
        os.setsid()

    _write_pid()

    def _cleanup(signum, frame):
        _remove_pid()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)

    try:
        from watchdog.observers import Observer
    except ImportError:
        print("ERROR: watchdog is required: pip install watchdog", file=sys.stderr)
        _remove_pid()
        sys.exit(1)

    HandlerClass = _make_handler()
    handler = HandlerClass(use_semantic=use_semantic)
    observer = Observer()

    watch_paths = _get_watch_paths()
    if not watch_paths:
        logger.warning("No directories to watch")
        _remove_pid()
        return os.getpid()

    for wp in watch_paths:
        observer.schedule(handler, wp, recursive=True)
        logger.info("Watching: %s", wp)

    observer.start()

    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        observer.stop()
    finally:
        observer.join()
        _remove_pid()

    return os.getpid()


def stop_watcher() -> bool:
    """
    Stop a running watcher daemon.

    Returns True if a process was stopped, False if none was running.
    """
    pid = _read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        # Wait briefly for process to exit
        for _ in range(10):
            try:
                os.kill(pid, 0)
                time.sleep(0.2)
            except OSError:
                break
        _remove_pid()
        return True
    except OSError:
        _remove_pid()
        return False


def watcher_status() -> dict:
    """Return watchdog status info."""
    running = is_running()
    return {
        "running": running,
        "pid": _read_pid() if running else None,
        "watch_paths": _get_watch_paths(),
        "pid_file": WATCHDOG_PID_PATH,
    }
