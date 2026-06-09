"""
core/self_observation/state_collector.py — recolector de estado vivo.

Reúne, sin red, snapshots del sistema accesibles desde el proceso:
  - heartbeat de gateway y worker
  - tamaño de la cola de mensajes
  - errores recientes en worker.log
  - reinicios detectables (gaps de heartbeat)
  - tablas y filas en las DBs operativas
  - flags de modo (audio_mode, sovereignty_mode, etc.)
  - count en continuity ledger

NO hace red, NO toca Telegram, NO manda mensajes. Solo observa.

API pública:
    collect_state() -> dict[str, Any]
    SystemSnapshot

Diseño: cada componente es defensive. Si una fuente falla (archivo
inexistente, DB rota), aporta None y sigue. Nunca raise.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.self_observation.state")


# ===========================================================================
# Defaults / paths
# ===========================================================================

_HOME_VECTRAX = os.path.join(os.path.expanduser("~"), ".vectrax")

_GATEWAY_HEARTBEAT = os.path.join(_HOME_VECTRAX, "gateway_heartbeat")
_WORKER_HEARTBEAT = os.path.join(_HOME_VECTRAX, "worker_heartbeat")
_WORKER_LOG = os.path.join(_HOME_VECTRAX, "worker.log")

_QUEUE_DB = os.environ.get(
    "VECTRAX_QUEUE_DB",
    "/app/vault/message_queue.db",
)
_GRAVITY_DB = os.environ.get(
    "VECTRAX_GRAVITY_DB",
    os.path.join(_HOME_VECTRAX, "gravity.db"),
)
_CONTINUITY_DB = os.environ.get(
    "VECTRAX_CONTINUITY_DB",
    os.path.join(_HOME_VECTRAX, "continuity.db"),
)
_SOVEREIGNTY_LEDGER = os.environ.get(
    "VECTRAX_SOVEREIGNTY_LEDGER",
    os.path.join(_HOME_VECTRAX, "sovereignty.jsonl"),
)


# ===========================================================================
# Snapshot
# ===========================================================================

@dataclass
class SystemSnapshot:
    """Snapshot estructurado del estado de Baytracks en un instante."""
    timestamp: float = 0.0
    gateway_heartbeat_age_s: Optional[float] = None
    worker_heartbeat_age_s: Optional[float] = None
    queue_pending: int = 0
    queue_processing: int = 0
    queue_done: int = 0
    queue_error: int = 0
    deep_memory_count: int = 0
    deep_memory_active: int = 0
    deep_memory_fused: int = 0
    deep_memory_archived: int = 0
    context_identities_count: int = 0
    essential_memories_count: int = 0
    continuity_processed_updates: int = 0
    audio_mode: str = ""
    sovereignty_mode: str = ""
    proactive_engine_enabled: bool = False
    recent_error_lines: List[str] = field(default_factory=list)
    recent_error_count_24h: int = 0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ===========================================================================
# Collectors
# ===========================================================================

def _heartbeat_age(path: str) -> Optional[float]:
    """Devuelve segundos desde el último heartbeat. None si no hay archivo."""
    try:
        with open(path, "r") as f:
            ts = float(f.read().strip())
        return max(0.0, time.time() - ts)
    except Exception:
        return None


def _queue_counts() -> Dict[str, int]:
    """Cuenta por status en message_queue.db."""
    out = {"pending": 0, "processing": 0, "done": 0, "error": 0}
    if not os.path.exists(_QUEUE_DB):
        return out
    try:
        c = sqlite3.connect(_QUEUE_DB, timeout=2)
        try:
            rows = c.execute(
                "SELECT status, COUNT(*) FROM queue GROUP BY status",
            ).fetchall()
            for status, n in rows:
                out[str(status)] = int(n)
        finally:
            c.close()
    except Exception as exc:
        logger.debug("queue_counts failed: %s", exc)
    return out


# Path to the actual gravitational DB (stars, user_stars, patterns).
# The env var VECTRAX_DB may be a relative path (e.g. "vectrax.db") which
# would resolve to /app/vectrax.db (the old empty copy). Always prefer
# the runtime copy at ~/.vectrax/vectrax.db which has the real data.
_VECTRAX_DB_ENV = os.environ.get("VECTRAX_DB", "")
_VECTRAX_DB_RUNTIME = os.path.join(_HOME_VECTRAX, "vectrax.db")
# Use runtime path if it exists; fall back to env var; fall back to runtime default
if os.path.exists(_VECTRAX_DB_RUNTIME):
    _VECTRAX_DB = _VECTRAX_DB_RUNTIME
elif _VECTRAX_DB_ENV and os.path.isabs(_VECTRAX_DB_ENV):
    _VECTRAX_DB = _VECTRAX_DB_ENV
else:
    _VECTRAX_DB = _VECTRAX_DB_RUNTIME


def _gravity_counts() -> Dict[str, int]:
    """Cuenta estrellas, user_stars, patrones e identidades.

    Reads from vectrax.db (where the gravitational engine actually stores
    data) instead of the missing gravity.db.
    Falls back to gravity.db/deep_memory if vectrax.db doesn't have stars.
    """
    out = {
        "total": 0, "active": 0, "fused": 0, "archived": 0,
        "identities": 0, "essentials": 0,
    }

    # Primary: read from vectrax.db (stars + user_stars + patterns)
    if os.path.exists(_VECTRAX_DB):
        try:
            c = sqlite3.connect(_VECTRAX_DB, timeout=2)
            try:
                # Stars = knowledge nodes (the gravitational memory)
                row = c.execute("SELECT COUNT(*) FROM stars").fetchone()
                stars_total = int(row[0]) if row else 0

                # User stars
                row = c.execute("SELECT COUNT(*) FROM user_stars").fetchone()
                user_stars = int(row[0]) if row else 0

                # Patterns
                row = c.execute("SELECT COUNT(*) FROM patterns").fetchone()
                patterns = int(row[0]) if row else 0

                # Layer distribution as active/fused/archived proxy
                core = mid = outer = 0
                for layer, cnt in c.execute(
                    "SELECT layer, COUNT(*) FROM stars GROUP BY layer"
                ).fetchall():
                    if layer == "core":
                        core = cnt
                    elif layer == "mid":
                        mid = cnt
                    else:
                        outer = cnt

                out["total"] = stars_total + user_stars
                out["active"] = core + mid   # core + mid = active gravitational mass
                out["fused"] = patterns      # patterns = fused knowledge
                out["archived"] = outer      # outer = cold/peripheral
                out["identities"] = user_stars
                out["essentials"] = core      # core stars = essential memories

                if stars_total > 0:
                    return out
            finally:
                c.close()
        except Exception as exc:
            logger.debug("vectrax.db gravity_counts failed: %s", exc)

    # Fallback: legacy gravity.db with deep_memory table
    if os.path.exists(_GRAVITY_DB):
        try:
            c = sqlite3.connect(_GRAVITY_DB, timeout=2)
            try:
                row = c.execute("SELECT COUNT(*) FROM deep_memory").fetchone()
                out["total"] = int(row[0]) if row else 0
                for status in ("ACTIVE", "FUSED", "ARCHIVED"):
                    row = c.execute(
                        "SELECT COUNT(*) FROM deep_memory "
                        "WHERE COALESCE(memory_status, 'ACTIVE') = ?",
                        (status,),
                    ).fetchone()
                    out[status.lower()] = int(row[0]) if row else 0
                try:
                    row = c.execute("SELECT COUNT(*) FROM context_identities").fetchone()
                    out["identities"] = int(row[0]) if row else 0
                except Exception:
                    pass
                try:
                    row = c.execute("SELECT COUNT(*) FROM essential_memories").fetchone()
                    out["essentials"] = int(row[0]) if row else 0
                except Exception:
                    pass
            finally:
                c.close()
        except Exception as exc:
            logger.debug("gravity.db gravity_counts failed: %s", exc)

    return out


def _continuity_counts() -> int:
    """Cuántos update_id están en el ledger de continuity."""
    if not os.path.exists(_CONTINUITY_DB):
        return 0
    try:
        c = sqlite3.connect(_CONTINUITY_DB, timeout=2)
        try:
            row = c.execute(
                "SELECT COUNT(*) FROM vctx_processed_updates",
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            c.close()
    except Exception:
        return 0


# ERROR/CRITICAL only — real errors that need attention.
# WARNING is tracked separately; it was inflating the error count
# (e.g. "etoro search failed" or "TTS timeout" are warnings, not errors).
_ERROR_PATTERN = re.compile(
    r"\[(?:ERROR|CRITICAL)\]", re.IGNORECASE,
)
# Broader pattern for recent_errors display (includes warnings for context)
_ERROR_OR_WARN_PATTERN = re.compile(
    r"\[(?:ERROR|CRITICAL|WARNING)\]", re.IGNORECASE,
)


def _parse_log_timestamp(line: str) -> float:
    """Parse timestamp from log line. Returns 0.0 if unparseable."""
    try:
        import datetime
        dt = datetime.datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
        return dt.timestamp()
    except Exception:
        return 0.0


def _recent_errors(window_seconds: int = 86400, max_lines: int = 8) -> List[str]:
    """Last ERROR/CRITICAL/WARNING lines within the time window.

    Returns up to `max_lines` messages for creator context injection.
    Includes warnings for diagnostic context but the count uses
    _error_count_window which only counts ERROR/CRITICAL.
    """
    if not os.path.exists(_WORKER_LOG):
        return []
    cutoff = time.time() - window_seconds
    try:
        with open(_WORKER_LOG, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        out: List[str] = []
        for line in reversed(lines[-2000:]):
            if not _ERROR_OR_WARN_PATTERN.search(line):
                continue
            ts = _parse_log_timestamp(line)
            if ts > 0 and ts < cutoff:
                continue
            if ts == 0:
                continue  # skip lines without valid timestamp
            out.append(line.strip()[:200])
            if len(out) >= max_lines:
                break
        out.reverse()
        return out
    except Exception:
        return []


def _error_count_window(window_seconds: int = 86400) -> int:
    """Count of [ERROR] and [CRITICAL] lines in the time window.

    Does NOT count [WARNING] — warnings like 'etoro search failed' or
    'TTS timeout' are operational noise, not real errors.
    Lines without a parseable timestamp are skipped (not counted).
    """
    if not os.path.exists(_WORKER_LOG):
        return 0
    cutoff = time.time() - window_seconds
    n = 0
    try:
        with open(_WORKER_LOG, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not _ERROR_PATTERN.search(line):
                    continue
                ts = _parse_log_timestamp(line)
                if ts > 0 and ts < cutoff:
                    continue
                if ts == 0:
                    continue  # skip unparseable lines
                n += 1
    except Exception:
        return 0
    return n


def _modes() -> Dict[str, Any]:
    """Estado de los modos operativos vía env."""
    audio_mode = (os.environ.get("VECTRAX_AUDIO_MODE") or "voice").lower()
    if os.environ.get("VECTRAX_AUDIO_DISABLED") == "1":
        audio_mode = "off"
    sov_mode = (os.environ.get("SOVEREIGNTY_MODE") or "observe").lower()
    proactive = os.environ.get("PROACTIVE_ENGINE_ENABLED") == "1"
    return {
        "audio_mode": audio_mode,
        "sovereignty_mode": sov_mode,
        "proactive_engine_enabled": proactive,
    }


# ===========================================================================
# Public entrypoint
# ===========================================================================

def collect_state() -> SystemSnapshot:
    """Recolecta el snapshot completo del estado actual de Baytracks.

    Cada subcomponente es defensive; si falla, contribuye con default y
    sigue.
    """
    snap = SystemSnapshot(timestamp=time.time())
    snap.gateway_heartbeat_age_s = _heartbeat_age(_GATEWAY_HEARTBEAT)
    snap.worker_heartbeat_age_s = _heartbeat_age(_WORKER_HEARTBEAT)

    qc = _queue_counts()
    snap.queue_pending = qc["pending"]
    snap.queue_processing = qc["processing"]
    snap.queue_done = qc["done"]
    snap.queue_error = qc["error"]

    gc = _gravity_counts()
    snap.deep_memory_count = gc["total"]
    snap.deep_memory_active = gc["active"]
    snap.deep_memory_fused = gc["fused"]
    snap.deep_memory_archived = gc["archived"]
    snap.context_identities_count = gc["identities"]
    snap.essential_memories_count = gc["essentials"]

    snap.continuity_processed_updates = _continuity_counts()

    modes = _modes()
    snap.audio_mode = modes["audio_mode"]
    snap.sovereignty_mode = modes["sovereignty_mode"]
    snap.proactive_engine_enabled = modes["proactive_engine_enabled"]

    snap.recent_error_lines = _recent_errors()
    snap.recent_error_count_24h = _error_count_window()

    return snap
