"""
Vectrax Core — System Monitor Route
=====================================
GET /v1/system/monitor

Agrega en un solo endpoint todo el estado operacional del sistema:
  - Servicios activos (gateway, worker, meta_loop, core_api)
  - Cola de jobs (pending / processing)
  - RAM, CPU, uptime
  - IdeaStore stats
  - Última reflexión del meta_loop
  - Governor policy

Solo accesible con permiso core.read (creator / operator).
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from fastapi import APIRouter, Depends

from services.core.auth import AuthContext
from services.core.middleware.user_context import require_permission

router = APIRouter(prefix="/system", tags=["system"])
logger = logging.getLogger("vectrax.core.routes.monitor")

_project_root = str(Path(__file__).resolve().parents[3])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


@router.get("/monitor")
async def system_monitor(
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """
    Panel de control del sistema completo.
    Devuelve estado operacional, IdeaStore y meta_loop en un solo call.
    """
    result: dict = {}

    # ── 1. Métricas operacionales (gateway, worker, queue, RAM) ───────────
    try:
        from core.operator.system_monitor import collect_metrics
        m = collect_metrics()
        result["runtime"] = {
            "status":              m.status,
            "worker_alive":        m.worker_alive,
            "worker_heartbeat_age_s": round(m.worker_heartbeat_age_s, 1),
            "queue_pending":       m.queue_pending,
            "queue_processing":    m.queue_processing,
            "memory_mb":           m.memory_mb,
            "memory_peak_mb":      getattr(m, "memory_peak_mb", 0.0),
            "active_users":        getattr(m, "active_users", 0),
        }
    except Exception as exc:
        result["runtime"] = {"error": str(exc)}

    # ── 2. IdeaStore ─────────────────────────────────────────────────────
    try:
        from core.idea_store import get_idea_store
        store = get_idea_store()
        stats = store.stats()
        top3  = store.get_top(n=3)
        result["ideas"] = {
            "total":        stats["total"],
            "by_status":    stats["by_status"],
            "by_priority":  stats["by_priority"],
            "convergence":  round(stats["convergence_level"], 3),
            "top3": [
                {
                    "idea_id":   i.idea_id,
                    "title":     i.title,
                    "priority":  i.priority.value,
                    "score":     round(i.priority_score, 3),
                    "component": i.affected_component,
                    "status":    i.status.value,
                }
                for i in top3
            ],
        }
    except Exception as exc:
        result["ideas"] = {"error": str(exc)}

    # ── 3. Meta-loop — última reflexión ──────────────────────────────────
    try:
        from core import state_manager
        state = state_manager.load()
        result["meta_loop"] = state.get("last_reflection", {})
    except Exception as exc:
        result["meta_loop"] = {"error": str(exc)}

    # ── 4. Governor ──────────────────────────────────────────────────────
    try:
        from core.governor import get_current_policy
        result["governor"] = get_current_policy()
    except Exception as exc:
        result["governor"] = {"error": str(exc)}

    # ── 5. Procesos del supervisor ────────────────────────────────────────
    try:
        import os
        pid_dir = os.path.expanduser("~/.vectrax")
        procs = {}
        for name in ("supervisor", "gateway"):
            pid_file = os.path.join(pid_dir, f"{name}.pid")
            try:
                pid = int(open(pid_file).read().strip())
                # Verificar si el proceso está vivo
                os.kill(pid, 0)
                procs[name] = {"pid": pid, "alive": True}
            except Exception:
                procs[name] = {"pid": None, "alive": False}
        result["processes"] = procs
    except Exception as exc:
        result["processes"] = {"error": str(exc)}

    result["sampled_at"] = time.time()
    return result
