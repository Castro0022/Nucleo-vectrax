"""
Vectrax Daemon — permanent background observer process.

Architecture:
  - Double-fork Unix daemon (detaches completely from terminal)
  - PID file:  ~/.vectrax/daemon.pid
  - Log file:  ~/.vectrax/daemon.log
  - Socket:    ~/.vectrax/events.sock  (IPC for event injection)

Main loop (select-based, non-blocking):
  ┌─────────────────────────────────────────────────────────────┐
  │  Every 1s:    poll IPC socket for injected events           │
  │  Every 60s:   collect system metric events                  │
  │  Every 300s:  recompute gravity for all stars               │
  │  Every 600s:  detect patterns + check for proposals         │
  │  Every 900s:  heartbeat star                                │
  └─────────────────────────────────────────────────────────────┘

All events → ingested as creator-channel stars (channel=creator, owner=mario)
Wake word detection active on every injected event.
Proposals queued to creator channel, never auto-executed.
"""
from __future__ import annotations

import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from vectrax import config as cfg
from vectrax.identity import CHANNEL_CREATOR, CREATOR_OWNER

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BASE = Path.home() / ".vectrax"
PID_FILE  = _BASE / "daemon.pid"
LOG_FILE  = _BASE / "daemon.log"

# Loop intervals (seconds)
SOCKET_POLL_INTERVAL   = 1
SYSTEM_COLLECT_INTERVAL = 60
GRAVITY_INTERVAL        = 300
PROPOSAL_INTERVAL       = 600


# ---------------------------------------------------------------------------
# PID file helpers
# ---------------------------------------------------------------------------

def _write_pid(pid: int) -> None:
    _BASE.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid))


def _read_pid() -> Optional[int]:
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def _clear_pid() -> None:
    try:
        PID_FILE.unlink()
    except Exception:
        pass


def is_running() -> bool:
    """Return True if the daemon process is alive."""
    pid = _read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, 0)   # signal 0 = check existence only
        return True
    except (ProcessLookupError, PermissionError):
        _clear_pid()
        return False


def get_pid() -> Optional[int]:
    return _read_pid() if is_running() else None


# ---------------------------------------------------------------------------
# Logging inside the daemon
# ---------------------------------------------------------------------------

_log_fd = None


def _log(msg: str) -> None:
    global _log_fd
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}\n"
        if _log_fd:
            _log_fd.write(line)
            _log_fd.flush()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Double-fork daemonize
# ---------------------------------------------------------------------------

def _daemonize() -> None:
    """
    Standard Unix double-fork to fully detach from the controlling terminal.
    After this returns, we are in the grandchild process.
    """
    # First fork
    pid = os.fork()
    if pid > 0:
        # Parent exits — returns control to the shell
        sys.exit(0)

    os.setsid()   # New session, no controlling terminal

    # Second fork — prevents the daemon from reacquiring a terminal
    pid = os.fork()
    if pid > 0:
        sys.exit(0)

    # We are now the daemon process
    # Redirect standard file descriptors
    _BASE.mkdir(parents=True, exist_ok=True)
    sys.stdout.flush()
    sys.stderr.flush()

    null_fd = open(os.devnull, "r")
    log_out  = open(LOG_FILE, "a", buffering=1)

    os.dup2(null_fd.fileno(), sys.stdin.fileno())
    os.dup2(log_out.fileno(), sys.stdout.fileno())
    os.dup2(log_out.fileno(), sys.stderr.fileno())

    global _log_fd
    _log_fd = log_out


# ---------------------------------------------------------------------------
# Signal handlers
# ---------------------------------------------------------------------------

_running = True


def _handle_sigterm(signum, frame) -> None:
    global _running
    _running = False
    _log("SIGTERM received — shutting down cleanly")


def _handle_sighup(signum, frame) -> None:
    _log("SIGHUP received — reloading config")
    cfg.load()   # reload config from disk


# ---------------------------------------------------------------------------
# Main daemon loop
# ---------------------------------------------------------------------------

def _daemon_main() -> None:
    global _running

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT,  _handle_sigterm)
    signal.signal(signal.SIGHUP,  _handle_sighup)

    _write_pid(os.getpid())
    cfg.activate_observer()

    _log(f"Vectrax daemon started — PID {os.getpid()}")
    _log(f"Creator: {cfg.get('creator_identity')}")

    # Lazy imports inside daemon process
    from vectrax.engine import ingest as _ingest, detect_patterns, reorganize
    from vectrax.collectors.socket_srv import create_server, close_server, poll
    from vectrax.collectors import system as sys_col
    from vectrax import wakeword as ww

    srv = create_server()
    _log(f"IPC socket ready: {srv.getsockname()}")

    last_system   = 0.0
    last_gravity  = 0.0
    last_proposals = 0.0

    def _ingest_event(text: str, success: bool = False) -> None:
        """Ingest an event, with wake word detection."""
        if ww.detect_in_text(text, CHANNEL_CREATOR):
            result = ww.trigger_activation(source="daemon")
            ts = datetime.fromtimestamp(result["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
            activation_text = f"[WAKE ACTIVATION] Despierta Vectrax — {ts}"
            _log(f"WAKE WORD DETECTED — observer activated at {ts}")
            try:
                _ingest(activation_text, success=True,
                        channel=CHANNEL_CREATOR, owner=CREATOR_OWNER)
            except Exception as e:
                _log(f"wake ingest error: {e}")
            return
        try:
            star = _ingest(text, success=success,
                           channel=CHANNEL_CREATOR, owner=CREATOR_OWNER)
            cfg.increment_events(1)
            _log(f"★ [{star.id[:8]}] {text[:80]}")
        except Exception as e:
            _log(f"ingest error: {e}")

    # Initial startup star
    _ingest_event(
        f"Vectrax daemon started at "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — "
        f"observer mode permanent",
        success=True,
    )

    while _running:
        now = time.time()

        # 1. Poll IPC socket for injected events
        try:
            injected = poll(srv, timeout=SOCKET_POLL_INTERVAL)
            for text in injected:
                _ingest_event(text)
        except Exception as e:
            _log(f"socket poll error: {e}")

        # 2. System metric collection
        if now - last_system >= SYSTEM_COLLECT_INTERVAL:
            try:
                events = sys_col.collect()
                for ev in events:
                    _ingest_event(ev)
                last_system = now
            except Exception as e:
                _log(f"system collect error: {e}")

        # 3. Gravity recomputation
        if now - last_gravity >= GRAVITY_INTERVAL:
            try:
                result = reorganize()
                _log(
                    f"Gravity recomputed: {result['stars_updated']} stars, "
                    f"{result['constellations_updated']} constellations"
                )
                last_gravity = now
            except Exception as e:
                _log(f"gravity error: {e}")

        # 4. Pattern detection + proposal generation
        if now - last_proposals >= PROPOSAL_INTERVAL:
            try:
                constellations = detect_patterns(
                    channel=CHANNEL_CREATOR, owner=CREATOR_OWNER
                )
                if constellations:
                    _log(f"Pattern detection: {len(constellations)} constellations updated")
                last_proposals = now
            except Exception as e:
                _log(f"pattern detection error: {e}")

    # Clean shutdown
    close_server(srv)
    cfg.deactivate_observer()
    _clear_pid()
    _log(f"Vectrax daemon stopped — {cfg.get('total_events_observed', 0)} total events")


# ---------------------------------------------------------------------------
# Public API  (called by CLI)
# ---------------------------------------------------------------------------

def start(foreground: bool = False) -> bool:
    """
    Start the daemon. Returns True if successfully started.
    foreground=True skips the double-fork (useful for debugging).
    """
    if is_running():
        return False   # already running

    _BASE.mkdir(parents=True, exist_ok=True)

    if foreground:
        global _log_fd
        _log_fd = open(LOG_FILE, "a", buffering=1)
        _daemon_main()
        return True

    # Double-fork into background
    pid = os.fork()
    if pid > 0:
        # Parent: wait briefly for child to write PID
        time.sleep(0.5)
        return is_running()

    # Child: second fork + run daemon
    try:
        _daemonize()
        _daemon_main()
    except Exception as e:
        try:
            Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_FILE, "a") as f:
                f.write(f"[FATAL] Daemon crash: {e}\n")
        except Exception:
            pass
    finally:
        sys.exit(0)


def stop() -> bool:
    """Send SIGTERM to the daemon. Returns True if it was running."""
    pid = _read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        # Wait up to 5 seconds for it to exit
        for _ in range(50):
            time.sleep(0.1)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                _clear_pid()
                return True
        # Force kill if still alive
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
        _clear_pid()
        return True
    except ProcessLookupError:
        _clear_pid()
        return True


def restart() -> bool:
    """Stop then start the daemon."""
    stop()
    time.sleep(1)
    return start()


def status() -> dict:
    """Return a status dict for display."""
    running = is_running()
    pid = _read_pid() if running else None
    summary = cfg.observer_summary()
    from vectrax.collectors.socket_srv import ping_daemon
    socket_alive = ping_daemon() if running else False

    return {
        "running": running,
        "pid": pid,
        "socket_alive": socket_alive,
        "observer_active": summary["active"],
        "creator": summary["creator"],
        "total_events": summary["total_events"],
        "started_at": summary["started_at"],
        "last_activation": summary["activated_at"],
        "wake_word_configured": summary["wake_word_configured"],
        "log_path": str(LOG_FILE),
    }
