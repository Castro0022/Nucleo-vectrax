"""
System event collector for Vectrax daemon.

Collects macOS system metrics and converts threshold-crossing events into
descriptive text suitable for ingestion as stars.

Sources:
  - Load average (os.getloadavg)
  - Disk usage (shutil.disk_usage)
  - Memory (psutil if available, else vm_stat via subprocess)
  - Process count (psutil if available, else ps via subprocess)

Events are only emitted when:
  - A metric crosses a threshold
  - State changes relative to last observation
  - Periodic heartbeat (every N cycles regardless)

This keeps the star graph meaningful — not flooded with identical readings.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import List, Optional

# ---------------------------------------------------------------------------
# State tracking (in-process memory — reset on daemon restart)
# ---------------------------------------------------------------------------

_prev: dict = {
    "load_1":       0.0,
    "disk_pct":     0.0,
    "mem_pct":      0.0,
    "proc_count":   0,
    "last_hb":      0.0,
}

# Thresholds
LOAD_SPIKE_THRESHOLD  = 3.0     # 1-min load average
DISK_WARN_PCT         = 80.0    # %
MEM_WARN_PCT          = 80.0    # %
PROC_DELTA_THRESHOLD  = 50      # significant process count change
HEARTBEAT_INTERVAL    = 900     # seconds between unconditional heartbeats (15 min)


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

def _load() -> Optional[tuple]:
    try:
        return os.getloadavg()
    except OSError:
        return None


def _disk_pct(path: str = "/") -> float:
    try:
        u = shutil.disk_usage(path)
        return (u.used / u.total) * 100
    except Exception:
        return 0.0


def _memory_pct() -> float:
    """Return approximate memory pressure percentage."""
    try:
        import psutil
        vm = psutil.virtual_memory()
        return vm.percent
    except ImportError:
        pass

    # Fallback: parse vm_stat on macOS
    try:
        out = subprocess.check_output(
            ["vm_stat"], text=True, stderr=subprocess.DEVNULL, timeout=5
        )
        page_size = 4096
        stats: dict = {}
        for line in out.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                v = v.strip().rstrip(".")
                try:
                    stats[k.strip()] = int(v) * page_size
                except ValueError:
                    pass
        total = stats.get("Pages free", 0) + stats.get("Pages active", 0) + \
                stats.get("Pages inactive", 0) + stats.get("Pages wired down", 0)
        used = total - stats.get("Pages free", 0)
        if total > 0:
            return (used / total) * 100
    except Exception:
        pass
    return 0.0


def _proc_count() -> int:
    try:
        import psutil
        return len(psutil.pids())
    except ImportError:
        pass
    try:
        out = subprocess.check_output(
            ["ps", "ax"], text=True, stderr=subprocess.DEVNULL, timeout=5
        )
        return max(0, len(out.splitlines()) - 1)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Main collection function
# ---------------------------------------------------------------------------

def collect() -> List[str]:
    """
    Collect system events. Returns a list of event description strings.
    Only returns entries when something notable has changed.
    """
    global _prev
    events: List[str] = []
    now = time.time()

    # Load average
    load = _load()
    if load:
        l1, l5, l15 = load
        if l1 > LOAD_SPIKE_THRESHOLD and abs(l1 - _prev["load_1"]) > 0.5:
            events.append(
                f"System load spike detected: {l1:.1f} (1m) {l5:.1f} (5m) {l15:.1f} (15m) "
                f"— exceeds threshold {LOAD_SPIKE_THRESHOLD}"
            )
        elif _prev["load_1"] > LOAD_SPIKE_THRESHOLD and l1 <= LOAD_SPIKE_THRESHOLD:
            events.append(
                f"System load normalised: {l1:.1f} (1m) — returned below threshold"
            )
        _prev["load_1"] = l1

    # Disk usage
    disk_pct = _disk_pct("/")
    if disk_pct > DISK_WARN_PCT and abs(disk_pct - _prev["disk_pct"]) > 1.0:
        events.append(
            f"Disk usage warning: {disk_pct:.1f}% of root filesystem used "
            f"(threshold: {DISK_WARN_PCT}%)"
        )
    _prev["disk_pct"] = disk_pct

    # Memory
    mem_pct = _memory_pct()
    if mem_pct > MEM_WARN_PCT and abs(mem_pct - _prev["mem_pct"]) > 5.0:
        events.append(
            f"Memory pressure warning: {mem_pct:.1f}% used "
            f"(threshold: {MEM_WARN_PCT}%)"
        )
    elif _prev["mem_pct"] > MEM_WARN_PCT and mem_pct <= MEM_WARN_PCT:
        events.append(f"Memory pressure relieved: {mem_pct:.1f}% used")
    _prev["mem_pct"] = mem_pct

    # Process count
    proc = _proc_count()
    delta = abs(proc - _prev["proc_count"])
    if _prev["proc_count"] > 0 and delta >= PROC_DELTA_THRESHOLD:
        direction = "increased" if proc > _prev["proc_count"] else "decreased"
        events.append(
            f"Process count {direction} significantly: "
            f"{_prev['proc_count']} → {proc} (delta: {delta})"
        )
    _prev["proc_count"] = proc

    # Periodic heartbeat
    if now - _prev["last_hb"] >= HEARTBEAT_INTERVAL:
        load_str = f"{load[0]:.2f}" if load else "n/a"
        events.append(
            f"Vectrax daemon heartbeat — "
            f"load={load_str} disk={disk_pct:.1f}% mem={mem_pct:.1f}% procs={proc}"
        )
        _prev["last_hb"] = now

    return events
