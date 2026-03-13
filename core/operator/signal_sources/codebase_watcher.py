"""
Vectrax Core — Codebase Watcher
====================================
Signal source that produces CognitiveSignal objects from real project activity:

  1. git status --short  — modified/added/deleted files (filesystem changes)
  2. git log --oneline   — new commits since last scan
  3. ~/.vectrax/*.err    — new lines in daemon/launch log files

Persists scan state to ~/.vectrax/codebase_watcher_state.json to avoid
re-reporting the same signals across cycles.

Capa: 2 — Percepción (signal source)
Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import subprocess
import time
from typing import Any, Dict, List, Optional

from cognition.types import CognitiveSignal, SignalCategory, SignalSource

logger = logging.getLogger("vectrax.operator.signal_sources.codebase_watcher")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Project root: 4 levels up from core/operator/signal_sources/
_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.parent
_STATE_DIR = pathlib.Path.home() / ".vectrax"
_WATCHER_STATE_FILE = _STATE_DIR / "codebase_watcher_state.json"
_LOG_FILES = [
    _STATE_DIR / "daemon.err",
    _STATE_DIR / "launch.err",
]


# ---------------------------------------------------------------------------
# Codebase Watcher
# ---------------------------------------------------------------------------

class CodebaseWatcher:
    """
    Produces CognitiveSignal objects from git activity and log files.

    Tracks scan state between calls to avoid re-reporting stale signals.
    State is persisted to ~/.vectrax/codebase_watcher_state.json.

    Usage::

        watcher = CodebaseWatcher()
        signals = watcher.scan()   # returns List[CognitiveSignal]
    """

    def __init__(self) -> None:
        self._state: Dict[str, Any] = self._load_state()
        logger.info("CodebaseWatcher initialized | project_root=%s", _PROJECT_ROOT)

    # -- State management ---------------------------------------------------

    def _load_state(self) -> Dict[str, Any]:
        """Load persisted watcher state or return empty defaults."""
        try:
            if _WATCHER_STATE_FILE.exists():
                return json.loads(_WATCHER_STATE_FILE.read_text())
        except Exception as exc:
            logger.debug("Could not load watcher state: %s", exc)
        return {
            "last_git_hash": None,
            "last_status_fingerprint": None,
            "last_err_positions": {},
            "last_scan_time": None,
        }

    def _save_state(self) -> None:
        """Persist watcher state to disk atomically."""
        try:
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = _WATCHER_STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state))
            tmp.replace(_WATCHER_STATE_FILE)
        except Exception as exc:
            logger.debug("Could not save watcher state: %s", exc)

    # -- Git helpers --------------------------------------------------------

    def _run_git(self, *args: str) -> Optional[str]:
        """Run a git command in the project root. Returns stdout or None."""
        try:
            result = subprocess.run(
                ["git", "-C", str(_PROJECT_ROOT), *args],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as exc:
            logger.debug("git command failed %s: %s", args, exc)
        return None

    # -- Signal sources -----------------------------------------------------

    def _scan_git_status(self) -> List[CognitiveSignal]:
        """
        Produce signals for files that are modified/added/deleted in the
        working tree. Only emits when the dirty-state fingerprint changes.
        """
        signals: List[CognitiveSignal] = []
        output = self._run_git("status", "--short")
        if output is None:
            return signals

        fingerprint = hashlib.sha256(output.encode()).hexdigest()[:16]
        if fingerprint == self._state.get("last_status_fingerprint"):
            return signals  # dirty state unchanged since last scan

        self._state["last_status_fingerprint"] = fingerprint

        lines = [ln for ln in output.splitlines() if ln.strip()]
        if not lines:
            return signals

        for line in lines[:20]:  # cap to avoid signal flood
            if len(line) < 3:
                continue
            xy = line[:2]
            path = line[3:].strip().strip('"')

            if any(xy.startswith(p) for p in ("??", "A ", " A")):
                change_type, priority = "added", 0.55
            elif any(xy.startswith(p) for p in ("D ", " D", "AD")):
                change_type, priority = "deleted", 0.60
            else:
                change_type, priority = "modified", 0.65

            signals.append(CognitiveSignal(
                source="git_status",
                source_type=SignalSource.FILESYSTEM,
                category=SignalCategory.CHANGE,
                priority=priority,
                summary=f"File {change_type}: {path}",
                intent=f"codebase_{change_type}",
                metadata={"path": path, "change_type": change_type, "xy": xy.strip()},
            ))

        if len(lines) > 5:
            signals.append(CognitiveSignal(
                source="git_status",
                source_type=SignalSource.FILESYSTEM,
                category=SignalCategory.PATTERN,
                priority=0.75,
                summary=f"Multiple codebase changes: {len(lines)} files dirty",
                intent="codebase_bulk_change",
                metadata={"dirty_count": len(lines)},
            ))

        return signals

    def _scan_git_log(self) -> List[CognitiveSignal]:
        """
        Produce signals for commits that are newer than the last tracked HEAD.
        On the very first scan, emits only the current HEAD as orientation.
        """
        signals: List[CognitiveSignal] = []
        output = self._run_git("log", "--oneline", "-5")
        if not output:
            return signals

        lines = [ln for ln in output.splitlines() if ln.strip()]
        if not lines:
            return signals

        current_head = lines[0].split()[0]
        last_hash = self._state.get("last_git_hash")

        if current_head == last_hash:
            return signals  # no new commits

        self._state["last_git_hash"] = current_head

        # Collect commits newer than last_hash
        new_lines: List[str] = []
        for line in lines:
            if line.split()[0] == last_hash:
                break
            new_lines.append(line)

        if not new_lines:
            # First run — record HEAD quietly (no signal spam)
            return signals

        for line in new_lines[:3]:
            parts = line.split(" ", 1)
            commit_hash = parts[0]
            message = parts[1] if len(parts) > 1 else "(no message)"
            signals.append(CognitiveSignal(
                source="git_log",
                source_type=SignalSource.FILESYSTEM,
                category=SignalCategory.CHANGE,
                priority=0.70,
                summary=f"New commit: {message[:80]}",
                intent="codebase_commit",
                metadata={"commit_hash": commit_hash, "message": message[:120]},
            ))

        return signals

    def _scan_log_files(self) -> List[CognitiveSignal]:
        """
        Produce signals from new lines appended to daemon/launch error logs.
        Tracks byte offsets per file to only read new content.
        """
        signals: List[CognitiveSignal] = []
        positions: Dict[str, int] = self._state.setdefault("last_err_positions", {})

        for log_file in _LOG_FILES:
            key = str(log_file)
            if not log_file.exists():
                continue
            try:
                current_size = log_file.stat().st_size
                # First time: start near end so we don't flood with history
                last_pos = positions.get(key, max(0, current_size - 512))

                if current_size < last_pos:
                    # File was truncated/rotated
                    positions[key] = 0
                    last_pos = 0

                if current_size <= last_pos:
                    continue

                with log_file.open("r", errors="replace") as fh:
                    fh.seek(last_pos)
                    new_content = fh.read(min(current_size - last_pos, 4096))

                positions[key] = current_size

                new_lines = [ln.strip() for ln in new_content.splitlines() if ln.strip()]
                if not new_lines:
                    continue

                has_error = any(
                    kw in ln.lower()
                    for ln in new_lines
                    for kw in ("error", "exception", "traceback", "critical")
                )
                has_warning = any(
                    kw in ln.lower()
                    for ln in new_lines
                    for kw in ("warning", "warn")
                )

                if has_error:
                    category, priority, intent = SignalCategory.ANOMALY, 0.85, "runtime_error"
                elif has_warning:
                    category, priority, intent = SignalCategory.CHANGE, 0.60, "runtime_warning"
                else:
                    category, priority, intent = SignalCategory.CHANGE, 0.45, "runtime_activity"

                sample = new_lines[-1][:100]
                signals.append(CognitiveSignal(
                    source=f"log:{log_file.name}",
                    source_type=SignalSource.FILESYSTEM,
                    category=category,
                    priority=priority,
                    summary=f"{log_file.name}: {len(new_lines)} new line(s) — {sample}",
                    intent=intent,
                    metadata={
                        "file": log_file.name,
                        "new_lines": len(new_lines),
                        "has_error": has_error,
                        "sample": sample,
                    },
                ))

            except Exception as exc:
                logger.debug("Error scanning log file %s: %s", log_file, exc)

        return signals

    # -- Public API ---------------------------------------------------------

    def scan(self) -> List[CognitiveSignal]:
        """
        Scan all signal sources and return new CognitiveSignal objects.

        Only returns signals that represent changes since the last call.
        State is persisted to disk after each scan.
        """
        signals: List[CognitiveSignal] = []

        try:
            signals.extend(self._scan_git_log())
        except Exception as exc:
            logger.warning("git_log scan error: %s", exc)

        try:
            signals.extend(self._scan_git_status())
        except Exception as exc:
            logger.warning("git_status scan error: %s", exc)

        try:
            signals.extend(self._scan_log_files())
        except Exception as exc:
            logger.warning("log_files scan error: %s", exc)

        self._state["last_scan_time"] = time.time()
        self._save_state()

        if signals:
            logger.info(
                "CodebaseWatcher.scan(): %d new signals (git_log=%d, git_status=%d, logs=%d)",
                len(signals),
                sum(1 for s in signals if s.source == "git_log"),
                sum(1 for s in signals if s.source == "git_status"),
                sum(1 for s in signals if s.source.startswith("log:")),
            )

        return signals
