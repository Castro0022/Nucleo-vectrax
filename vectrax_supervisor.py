#!/usr/bin/env python3
"""
Vectrax Supervisor — Punto de arranque unificado
===================================================
Un solo proceso que supervisa todos los servicios de Vectrax:

  1. Telegram Gateway (conexión con usuarios)
  2. Meta Loop (ciclo cognitivo continuo)
  3. Health checks periódicos

Si un servicio muere, el supervisor lo reinicia automáticamente.
Si el supervisor muere, launchd lo reinicia (KeepAlive=true).

Uso:
  python3 vectrax_supervisor.py           # arranque normal
  python3 vectrax_supervisor.py --check   # health check rápido

Creado: 2026-03-28
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import fcntl
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

VECTRAX_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = Path.home() / ".vectrax"
PID_FILE = RUNTIME_DIR / "supervisor.pid"
LOCK_FILE = RUNTIME_DIR / "supervisor.lock"
LOG_FILE = RUNTIME_DIR / "supervisor.log"
PYTHON = sys.executable

# Ensure dirs exist
RUNTIME_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
    ],
)
logger = logging.getLogger("vectrax.supervisor")

# ---------------------------------------------------------------------------
# Service definitions
# ---------------------------------------------------------------------------

SERVICES: Dict[str, Dict] = {
    "telegram_gateway": {
        "cmd": [PYTHON, "-m", "vectrax.telegram_gateway"],
        "cwd": str(VECTRAX_DIR),
        "required": True,    # Must be running
        "restart_delay": 5,  # Seconds before restart
        "max_restarts": 10,  # Max restarts before giving up
    },
    "meta_loop": {
        "cmd": [PYTHON, str(VECTRAX_DIR / "vectrax_unified.py")],
        "cwd": str(VECTRAX_DIR),
        "required": False,   # Nice to have, not critical
        "restart_delay": 10,
        "max_restarts": 5,
    },
    "pipeline_worker": {
        "cmd": [PYTHON, "-m", "core.transport.pipeline_worker"],
        "cwd": str(VECTRAX_DIR),
        "required": True,    # Sin worker, no hay respuestas
        "restart_delay": 3,
        "max_restarts": 20,
    },
}

# ---------------------------------------------------------------------------
# Single-instance lock
# ---------------------------------------------------------------------------

_lock_fd = None


def acquire_lock() -> bool:
    """Ensure only one supervisor instance runs."""
    global _lock_fd
    try:
        _lock_fd = open(LOCK_FILE, "w")
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
        return True
    except (IOError, OSError):
        return False


def release_lock():
    global _lock_fd
    if _lock_fd:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            _lock_fd.close()
        except Exception:
            pass
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------

class ManagedService:
    """A supervised child process."""

    def __init__(self, name: str, config: Dict) -> None:
        self.name = name
        self.cmd = config["cmd"]
        self.cwd = config.get("cwd", str(VECTRAX_DIR))
        self.required = config.get("required", True)
        self.restart_delay = config.get("restart_delay", 5)
        self.max_restarts = config.get("max_restarts", 10)
        self.process: Optional[subprocess.Popen] = None
        self.restart_count: int = 0
        self.last_start: float = 0
        self.healthy: bool = False

    def start(self) -> bool:
        """Start the service process."""
        if self.process and self.process.poll() is None:
            logger.info("[%s] Already running (PID %d)", self.name, self.process.pid)
            self.healthy = True
            return True

        if self.restart_count >= self.max_restarts:
            logger.error(
                "[%s] Max restarts (%d) exceeded — giving up",
                self.name, self.max_restarts,
            )
            self.healthy = False
            return False

        try:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(VECTRAX_DIR)

            self.process = subprocess.Popen(
                self.cmd,
                cwd=self.cwd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            self.last_start = time.time()
            self.healthy = True
            logger.info(
                "[%s] Started (PID %d, restart #%d)",
                self.name, self.process.pid, self.restart_count,
            )
            return True
        except Exception as exc:
            logger.error("[%s] Failed to start: %s", self.name, exc)
            self.healthy = False
            return False

    def check(self) -> bool:
        """Check if the process is alive. Returns True if healthy."""
        if self.process is None:
            self.healthy = False
            return False

        retcode = self.process.poll()
        if retcode is None:
            self.healthy = True
            return True

        # Process died
        self.healthy = False
        uptime = time.time() - self.last_start

        # Read last stderr for diagnostics
        stderr_tail = ""
        try:
            if self.process.stderr:
                stderr_tail = self.process.stderr.read(500).decode("utf-8", errors="replace")
        except Exception:
            pass

        logger.warning(
            "[%s] Died (exit=%d, uptime=%.0fs, stderr=%s)",
            self.name, retcode, uptime, stderr_tail[:200],
        )
        return False

    def restart(self) -> bool:
        """Restart after a delay."""
        self.restart_count += 1
        logger.info(
            "[%s] Restarting in %ds (attempt %d/%d)...",
            self.name, self.restart_delay,
            self.restart_count, self.max_restarts,
        )
        time.sleep(self.restart_delay)
        return self.start()

    def stop(self) -> None:
        """Gracefully stop the process."""
        if self.process and self.process.poll() is None:
            logger.info("[%s] Stopping (PID %d)...", self.name, self.process.pid)
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            logger.info("[%s] Stopped", self.name)
        self.healthy = False

    def status(self) -> Dict:
        alive = self.process is not None and self.process.poll() is None
        return {
            "name": self.name,
            "alive": alive,
            "pid": self.process.pid if self.process and alive else None,
            "restarts": self.restart_count,
            "healthy": self.healthy,
            "uptime_s": round(time.time() - self.last_start) if alive else 0,
        }


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

WORKER_HEARTBEAT_PATH = RUNTIME_DIR / "worker_heartbeat"
WORKER_HEARTBEAT_MAX_AGE = 30  # seconds without heartbeat = hung

GATEWAY_HEARTBEAT_PATH = RUNTIME_DIR / "gateway_heartbeat"
GATEWAY_HEARTBEAT_MAX_AGE = 90  # gateway polls every 30s, so 90s = 3 missed polls


class VectraxSupervisor:
    """Main supervisor loop."""

    HEALTH_CHECK_INTERVAL = 15  # seconds

    def __init__(self) -> None:
        self.services: Dict[str, ManagedService] = {}
        self._running = False

        for name, config in SERVICES.items():
            self.services[name] = ManagedService(name, config)

    def start_all(self) -> None:
        """Start all services."""
        logger.info("=" * 55)
        logger.info("  VECTRAX SUPERVISOR — Starting all services")
        logger.info("=" * 55)

        # Kill any orphan telegram gateways first
        self._kill_orphans()

        for svc in self.services.values():
            svc.start()

    def _kill_orphans(self) -> None:
        """Kill orphan telegram_gateway processes from previous runs."""
        try:
            result = subprocess.run(
                ["pgrep", "-f", "vectrax.telegram_gateway"],
                capture_output=True, text=True,
            )
            if result.stdout.strip():
                pids = result.stdout.strip().split("\n")
                for pid in pids:
                    pid = pid.strip()
                    if pid and pid != str(os.getpid()):
                        logger.info("Killing orphan telegram_gateway PID %s", pid)
                        try:
                            os.kill(int(pid), signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                time.sleep(2)  # Wait for cleanup
        except Exception:
            pass

    def run(self) -> None:
        """Main supervisor loop."""
        self._running = True
        self.start_all()

        while self._running:
            time.sleep(self.HEALTH_CHECK_INTERVAL)

            for svc in self.services.values():
                if not svc.check():
                    if svc.restart_count < svc.max_restarts:
                        svc.restart()
                    elif svc.required:
                        logger.critical(
                            "[%s] REQUIRED service failed permanently!", svc.name,
                        )

            # --- Heartbeat checks: detect hung processes ---
            self._check_worker_heartbeat()
            self._check_gateway_heartbeat()

    def _check_worker_heartbeat(self) -> None:
        """Kill and restart pipeline_worker if heartbeat is stale."""
        if "pipeline_worker" not in self.services:
            return

        svc = self.services["pipeline_worker"]
        if not svc.healthy or svc.process is None:
            return  # Already dead, will be restarted by main loop

        try:
            if not WORKER_HEARTBEAT_PATH.exists():
                return  # Worker just started, no heartbeat yet

            heartbeat_ts = float(WORKER_HEARTBEAT_PATH.read_text().strip())
            age = time.time() - heartbeat_ts

            if age > WORKER_HEARTBEAT_MAX_AGE:
                logger.warning(
                    "[pipeline_worker] HEARTBEAT STALE (%.0fs > %ds) — killing hung worker (PID %d)",
                    age, WORKER_HEARTBEAT_MAX_AGE, svc.process.pid,
                )
                # Record failure for immunity
                try:
                    from core.operator.failure_immunity import record_failure
                    record_failure("worker_hung", "heartbeat_stale",
                                   f"age={age:.0f}s pid={svc.process.pid}")
                except Exception:
                    pass
                # Force kill
                svc.process.kill()
                try:
                    svc.process.wait(timeout=5)
                except Exception:
                    pass
                svc.healthy = False
                # Remove stale heartbeat so next check doesn't re-trigger
                try:
                    WORKER_HEARTBEAT_PATH.unlink()
                except Exception:
                    pass
                # Will be restarted by main loop on next cycle
        except Exception as exc:
            logger.debug("Worker heartbeat check error: %s", exc)

    def _check_gateway_heartbeat(self) -> None:
        """Kill and restart gateway if heartbeat is stale."""
        if "telegram_gateway" not in self.services:
            return

        svc = self.services["telegram_gateway"]
        if not svc.healthy or svc.process is None:
            return

        try:
            if not GATEWAY_HEARTBEAT_PATH.exists():
                return

            heartbeat_ts = float(GATEWAY_HEARTBEAT_PATH.read_text().strip())
            age = time.time() - heartbeat_ts

            if age > GATEWAY_HEARTBEAT_MAX_AGE:
                logger.warning(
                    "[telegram_gateway] HEARTBEAT STALE (%.0fs > %ds) — killing hung gateway (PID %d)",
                    age, GATEWAY_HEARTBEAT_MAX_AGE, svc.process.pid,
                )
                # Record failure for immunity
                try:
                    from core.operator.failure_immunity import record_failure
                    record_failure("gateway_stale", "heartbeat_stale",
                                   f"age={age:.0f}s pid={svc.process.pid}")
                except Exception:
                    pass
                svc.process.kill()
                try:
                    svc.process.wait(timeout=5)
                except Exception:
                    pass
                svc.healthy = False
                try:
                    GATEWAY_HEARTBEAT_PATH.unlink()
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("Gateway heartbeat check error: %s", exc)

    def stop_all(self) -> None:
        """Stop all services gracefully."""
        logger.info("Supervisor shutting down...")
        self._running = False
        for svc in self.services.values():
            svc.stop()

    def status(self) -> Dict:
        return {
            "supervisor_pid": os.getpid(),
            "services": {
                name: svc.status() for name, svc in self.services.items()
            },
            "timestamp": datetime.now().isoformat(),
        }


# ---------------------------------------------------------------------------
# Signal handlers
# ---------------------------------------------------------------------------

_supervisor: Optional[VectraxSupervisor] = None


def _handle_shutdown(signum, frame):
    logger.info("Received signal %d — shutting down", signum)
    if _supervisor:
        _supervisor.stop_all()
    release_lock()
    sys.exit(0)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def main():
    global _supervisor

    # Health check mode
    if "--check" in sys.argv:
        if LOCK_FILE.exists():
            try:
                with open(LOCK_FILE) as f:
                    pid = int(f.read().strip())
                os.kill(pid, 0)  # Check if alive
                print(f"Vectrax supervisor running (PID {pid})")
                sys.exit(0)
            except (ProcessLookupError, ValueError):
                pass
        print("Vectrax supervisor NOT running")
        sys.exit(1)

    # Single instance check
    if not acquire_lock():
        print("Another supervisor is already running. Exiting.")
        sys.exit(1)

    # Write PID
    PID_FILE.write_text(str(os.getpid()))

    # Signal handlers
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    logger.info("Vectrax Supervisor starting (PID %d)", os.getpid())

    _supervisor = VectraxSupervisor()
    try:
        _supervisor.run()
    except KeyboardInterrupt:
        pass
    finally:
        _supervisor.stop_all()
        release_lock()
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
