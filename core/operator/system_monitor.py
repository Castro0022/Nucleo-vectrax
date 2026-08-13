"""
Vectrax System Monitor — Observabilidad + Límites Duros
=========================================================
Métricas en tiempo real + protección contra crecimiento descontrolado.

Métricas:
  - Usuarios activos (mensajes en últimos 5 min)
  - Jobs pendientes / processing / done
  - Latencia promedio de respuesta
  - CPU / RAM del proceso (RSS *actual*, no el pico histórico)
  - Workers activos + heartbeat age

Medición de memoria:
  `memory_mb` es el RSS actual del proceso y es el único valor que clasifica
  el estado. `memory_peak_mb` (ru_maxrss) se expone solo como dato
  informativo: nunca decrece durante la vida del proceso, así que usarlo
  para clasificar dejaría el sistema en `degraded` permanente tras cualquier
  pico transitorio.

Límites duros:
  - MAX_QUEUE_DEPTH: si la cola supera este número, nuevos jobs se rechazan
  - MAX_MEMORY_MB: si RAM supera este valor, se fuerza cleanup
  - MAX_LATENCY_S: si latencia promedio supera, se registra alerta
  - DUPLICATE_WINDOW_S: mensajes idénticos del mismo usuario en N segundos = duplicado
  - MAX_CONCURRENT: ya enforced en el worker

Principio: mejor rechazar un mensaje que colapsar el sistema.

Creado: 2026-03-28
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.system_monitor")


# ---------------------------------------------------------------------------
# Hard limits (NUNCA se superan, el sistema se protege)
# ---------------------------------------------------------------------------

MAX_QUEUE_DEPTH = 50          # máx mensajes en cola antes de rechazar
MAX_MEMORY_MB = 800           # máx RAM por proceso antes de alerta
MAX_LATENCY_S = 15.0          # latencia promedio máxima antes de alerta
DUPLICATE_WINDOW_S = 120      # ventana para detectar mensajes duplicados (2 min)
ACTIVE_USER_WINDOW_S = 300    # 5 minutos para considerar usuario "activo"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SystemMetrics:
    """Snapshot de métricas del sistema en un momento."""
    timestamp: float = 0.0

    # Queue
    queue_pending: int = 0
    queue_processing: int = 0
    queue_done: int = 0
    queue_error: int = 0
    queue_total: int = 0

    # Users
    active_users: int = 0

    # Latency
    avg_latency_s: float = 0.0
    max_latency_s: float = 0.0

    # System
    memory_mb: float = 0.0        # RSS actual — clasifica el estado
    memory_peak_mb: float = 0.0   # pico histórico (ru_maxrss) — informativo
    cpu_percent: float = 0.0

    # Workers
    worker_alive: bool = False
    worker_heartbeat_age_s: float = 0.0

    # Limits
    queue_at_limit: bool = False
    memory_at_limit: bool = False
    latency_at_limit: bool = False

    # Status
    status: str = "healthy"  # healthy | degraded | critical

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Metrics collection
# ---------------------------------------------------------------------------

_QUEUE_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "vault", "message_queue.db",
)

_HEARTBEAT_PATH = os.path.join(os.path.expanduser("~"), ".vectrax", "worker_heartbeat")

# Cache corto para la lectura de RSS: evita una tormenta de subprocesos `ps`
# cuando el dashboard y el gateway consultan métricas en paralelo.
_RSS_CACHE_TTL_S = 2.0
_rss_cache: tuple = (0.0, 0.0)  # (medido_en, valor_mb)


def _read_current_rss_mb() -> float:
    """
    RSS *actual* del proceso en MB. Devuelve 0.0 si no se puede medir.

    Orden de preferencia:
      1. /proc/self/status → VmRSS  (Linux: lectura directa, sin coste)
      2. psutil                     (multiplataforma, si está instalado)
      3. ps -o rss=                 (macOS/BSD: fallback vía subproceso)

    Nunca usa ru_maxrss: ese valor es el pico histórico, no el uso actual.
    """
    global _rss_cache
    now = time.time()
    measured_at, cached = _rss_cache
    if cached and now - measured_at < _RSS_CACHE_TTL_S:
        return cached

    value = 0.0

    # 1) Linux — VmRSS en kB
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    value = round(int(line.split()[1]) / 1024, 1)
                    break
    except Exception:
        pass

    # 2) psutil — RSS en bytes
    if value == 0.0:
        try:
            import psutil
            value = round(
                psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 1
            )
        except Exception:
            pass

    # 3) ps — RSS en kB (macOS/BSD)
    if value == 0.0:
        try:
            import subprocess
            out = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(os.getpid())],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip()
            if out:
                value = round(int(out.split()[0]) / 1024, 1)
        except Exception:
            pass

    if value:
        _rss_cache = (now, value)
    return value


def _read_peak_rss_mb() -> float:
    """
    Pico histórico de RSS en MB (ru_maxrss). Solo informativo.

    No decrece nunca durante la vida del proceso, así que no debe usarse
    para evaluar límites ni clasificar el estado del sistema.
    """
    try:
        import platform
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Darwin/BSD devuelve bytes; Linux devuelve kilobytes.
        if platform.system() == "Darwin":
            return round(peak / (1024 * 1024), 1)
        return round(peak / 1024, 1)
    except Exception:
        return 0.0


def collect_metrics() -> SystemMetrics:
    """Recolecta todas las métricas del sistema en un snapshot."""
    m = SystemMetrics(timestamp=time.time())

    # --- Queue stats ---
    try:
        conn = sqlite3.connect(_QUEUE_DB, timeout=2)
        rows = conn.execute("SELECT status, COUNT(*) FROM queue GROUP BY status").fetchall()
        for status, count in rows:
            if status == "pending":
                m.queue_pending = count
            elif status == "processing":
                m.queue_processing = count
            elif status == "done":
                m.queue_done = count
            elif status == "error":
                m.queue_error = count
        m.queue_total = m.queue_pending + m.queue_processing + m.queue_done + m.queue_error

        # Active users (distinct users in last 5 min)
        cutoff = time.time() - ACTIVE_USER_WINDOW_S
        m.active_users = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM queue WHERE created_at > ?",
            (cutoff,),
        ).fetchone()[0]

        # Latency (avg time from created to done, last 20 completed)
        latency_rows = conn.execute(
            "SELECT done_at - created_at FROM queue "
            "WHERE status = 'done' AND done_at > 0 "
            "ORDER BY done_at DESC LIMIT 20",
        ).fetchall()
        if latency_rows:
            latencies = [r[0] for r in latency_rows if r[0] > 0]
            if latencies:
                m.avg_latency_s = round(sum(latencies) / len(latencies), 2)
                m.max_latency_s = round(max(latencies), 2)

        conn.close()
    except Exception as exc:
        logger.debug("Queue metrics failed: %s", exc)

    # --- System resources ---
    # Uso actual, no pico: el pico nunca baja y marcaría el sistema como
    # degradado de forma permanente tras cualquier pico transitorio.
    m.memory_mb = _read_current_rss_mb()
    m.memory_peak_mb = _read_peak_rss_mb()

    # --- Worker heartbeat ---
    try:
        if os.path.exists(_HEARTBEAT_PATH):
            with open(_HEARTBEAT_PATH) as f:
                hb_ts = float(f.read().strip())
            m.worker_heartbeat_age_s = round(time.time() - hb_ts, 1)
            m.worker_alive = m.worker_heartbeat_age_s < 30
        else:
            m.worker_alive = False
            m.worker_heartbeat_age_s = -1
    except Exception:
        m.worker_alive = False

    # --- Evaluate limits ---
    m.queue_at_limit = m.queue_pending >= MAX_QUEUE_DEPTH
    m.memory_at_limit = m.memory_mb > MAX_MEMORY_MB
    m.latency_at_limit = m.avg_latency_s > MAX_LATENCY_S

    # --- Overall status ---
    if m.queue_at_limit or not m.worker_alive:
        m.status = "critical"
    elif m.latency_at_limit or m.memory_at_limit:
        m.status = "degraded"
    else:
        m.status = "healthy"

    return m


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

_recent_messages: Dict[str, float] = {}  # "user_id:hash" → timestamp


def is_duplicate(user_id: str, content: str) -> bool:
    """
    Detecta si el mismo usuario envió el mismo mensaje en los últimos N segundos.
    Previene jobs duplicados por doble-tap o reintentos.

    Uses a stable hash of (user_id + normalized content) to catch duplicates
    even with minor whitespace differences.
    """
    import hashlib
    _normalized = content.strip().lower()
    _hash = hashlib.md5(f"{user_id}:{_normalized}".encode()).hexdigest()[:16]
    key = f"{user_id}:{_hash}"
    now = time.time()

    # Cleanup old entries
    stale = [k for k, ts in _recent_messages.items() if now - ts > DUPLICATE_WINDOW_S]
    for k in stale:
        _recent_messages.pop(k, None)

    if key in _recent_messages:
        age = now - _recent_messages[key]
        if age < DUPLICATE_WINDOW_S:
            logger.info("Duplicate detected: %s (%.1fs ago, hash=%s)",
                        user_id[:20], age, _hash[:8])
            return True

    _recent_messages[key] = now
    return False


# ---------------------------------------------------------------------------
# Queue gate (should we accept this job?)
# ---------------------------------------------------------------------------

def should_accept_job(user_id: str, content: str) -> tuple:
    """
    Decide si el sistema puede aceptar un nuevo job.

    Returns:
        (accepted: bool, reason: str)

    Rechaza si:
      - Cola llena (MAX_QUEUE_DEPTH)
      - Duplicado (mismo mensaje en DUPLICATE_WINDOW_S)
      - Worker muerto y cola ya tiene mensajes pending
    """
    # Duplicate check
    if is_duplicate(user_id, content):
        return False, "duplicate"

    # Queue depth check
    try:
        conn = sqlite3.connect(_QUEUE_DB, timeout=2)
        pending = conn.execute(
            "SELECT COUNT(*) FROM queue WHERE status = 'pending'",
        ).fetchone()[0]
        conn.close()

        if pending >= MAX_QUEUE_DEPTH:
            logger.warning("Queue full: %d pending (max=%d)", pending, MAX_QUEUE_DEPTH)
            return False, "queue_full"
    except Exception:
        pass

    # Worker alive check
    try:
        if os.path.exists(_HEARTBEAT_PATH):
            with open(_HEARTBEAT_PATH) as f:
                hb_ts = float(f.read().strip())
            age = time.time() - hb_ts
            if age > 60:  # Worker dead for >1 min
                try:
                    conn = sqlite3.connect(_QUEUE_DB, timeout=2)
                    pending = conn.execute(
                        "SELECT COUNT(*) FROM queue WHERE status = 'pending'",
                    ).fetchone()[0]
                    conn.close()
                    if pending > 5:
                        return False, "worker_dead_queue_backlog"
                except Exception:
                    pass
    except Exception:
        pass

    return True, "accepted"


# ---------------------------------------------------------------------------
# CLI-friendly status string
# ---------------------------------------------------------------------------

def format_status(m: Optional[SystemMetrics] = None) -> str:
    """Formatea métricas para CLI/logs."""
    if m is None:
        m = collect_metrics()

    icon = {"healthy": "🟢", "degraded": "🟡", "critical": "🔴"}.get(m.status, "⚪")

    lines = [
        f"  {icon} System: {m.status.upper()}",
        f"",
        f"  Queue:",
        f"    Pending:     {m.queue_pending}",
        f"    Processing:  {m.queue_processing}",
        f"    Done:        {m.queue_done}",
        f"    Errors:      {m.queue_error}",
        f"",
        f"  Users active:  {m.active_users} (last 5min)",
        f"  Latency avg:   {m.avg_latency_s}s (max {m.max_latency_s}s)",
        f"  Memory:        {m.memory_mb} MB (peak {m.memory_peak_mb} MB)",
        f"  Worker:        {'ALIVE' if m.worker_alive else 'DEAD'} (heartbeat {m.worker_heartbeat_age_s}s ago)",
        f"",
        f"  Limits:",
        f"    Queue:       {'⚠ FULL' if m.queue_at_limit else 'OK'} ({m.queue_pending}/{MAX_QUEUE_DEPTH})",
        f"    Memory:      {'⚠ HIGH' if m.memory_at_limit else 'OK'} ({m.memory_mb}/{MAX_MEMORY_MB} MB)",
        f"    Latency:     {'⚠ SLOW' if m.latency_at_limit else 'OK'} ({m.avg_latency_s}/{MAX_LATENCY_S}s)",
    ]
    return "\n".join(lines)
