"""
Vectrax Core — Dashboard Routes (Public)
==========================================
Endpoints públicos que agregan datos de ambas bases de datos
(vectrax.db gravitacional + user_memory.db Telegram) para el
panel web. Sin auth — el dashboard es read-only.

  GET /v1/dashboard/summary      — stats agregados
  GET /v1/dashboard/stars        — knowledge stars (top por masa)
  GET /v1/dashboard/constellations — constellations
  GET /v1/dashboard/interactions  — historial de Telegram
  GET /v1/dashboard/users        — perfiles de usuarios
  GET /v1/dashboard/operator     — estado operacional completo
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Query

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
logger = logging.getLogger("vectrax.core.routes.dashboard")

# DB paths (same as used by the rest of the system)
_GRAV_DB = Path.home() / ".vectrax" / "vectrax.db"
_USER_DB = Path(__file__).resolve().parents[3] / "vault" / "user_memory.db"
_QUEUE_DB = Path(__file__).resolve().parents[3] / "vault" / "message_queue.db"


def _grav_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_GRAV_DB), timeout=3)
    conn.row_factory = sqlite3.Row
    return conn


def _user_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_USER_DB), timeout=3)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# GET /v1/dashboard/summary
# ---------------------------------------------------------------------------

@router.get("/summary")
async def dashboard_summary() -> Dict[str, Any]:
    """Aggregated stats from gravitational + Telegram databases."""
    result: Dict[str, Any] = {"ts": time.time()}

    # Gravitational DB
    try:
        conn = _grav_conn()
        result["knowledge_stars"] = conn.execute(
            "SELECT COUNT(*) FROM stars"
        ).fetchone()[0]
        result["constellations"] = conn.execute(
            "SELECT COUNT(*) FROM constellations"
        ).fetchone()[0]
        result["user_stars"] = conn.execute(
            "SELECT COUNT(*) FROM user_stars"
        ).fetchone()[0]
        result["patterns"] = conn.execute(
            "SELECT COUNT(*) FROM patterns"
        ).fetchone()[0]
        result["trajectories"] = conn.execute(
            "SELECT COUNT(*) FROM trajectories"
        ).fetchone()[0]
        # Layer distribution
        layers = {}
        for row in conn.execute(
            "SELECT layer, COUNT(*) as c FROM stars GROUP BY layer"
        ).fetchall():
            layers[row["layer"]] = row["c"]
        result["layers"] = layers
        conn.close()
    except Exception as exc:
        result["grav_error"] = str(exc)

    # Telegram user memory
    try:
        conn = _user_conn()
        result["telegram_users"] = conn.execute(
            "SELECT COUNT(*) FROM profiles"
        ).fetchone()[0]
        result["telegram_interactions"] = conn.execute(
            "SELECT COUNT(*) FROM interactions"
        ).fetchone()[0]
        try:
            result["facts"] = conn.execute(
                "SELECT COUNT(*) FROM user_facts"
            ).fetchone()[0]
        except Exception:
            result["facts"] = 0
        try:
            result["core_memory"] = conn.execute(
                "SELECT COUNT(*) FROM core_memory"
            ).fetchone()[0]
        except Exception:
            result["core_memory"] = 0
        conn.close()
    except Exception as exc:
        result["user_error"] = str(exc)

    # System metrics
    try:
        from core.operator.system_monitor import collect_metrics
        m = collect_metrics()
        result["system"] = {
            "status": m.status,
            "worker_alive": m.worker_alive,
            "queue_pending": m.queue_pending,
            "queue_processing": m.queue_processing,
            "memory_mb": m.memory_mb,
            "active_users": m.active_users,
            "avg_latency_s": m.avg_latency_s,
        }
    except Exception as exc:
        result["system"] = {"error": str(exc)}

    return result


# ---------------------------------------------------------------------------
# GET /v1/dashboard/stars
# ---------------------------------------------------------------------------

@router.get("/stars")
async def dashboard_stars(
    limit: int = Query(50, ge=1, le=500),
) -> Dict[str, Any]:
    """Knowledge stars ordered by gravity score (highest first)."""
    try:
        conn = _grav_conn()
        rows = conn.execute(
            "SELECT id, content, layer, gravity_score, repetition_count, "
            "channel, owner "
            "FROM stars ORDER BY gravity_score DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        stars = [
            {
                "id": r["id"],
                "content": r["content"][:200],
                "layer": r["layer"],
                "gravity_score": round(r["gravity_score"], 4),
                "repetition_count": r["repetition_count"],
                "channel": r["channel"],
                "owner": r["owner"],
            }
            for r in rows
        ]
        return {"stars": stars, "total": len(stars)}
    except Exception as exc:
        return {"stars": [], "total": 0, "error": str(exc)}


# ---------------------------------------------------------------------------
# GET /v1/dashboard/constellations
# ---------------------------------------------------------------------------

@router.get("/constellations")
async def dashboard_constellations(
    limit: int = Query(50, ge=1, le=500),
) -> Dict[str, Any]:
    """Constellations ordered by gravity score."""
    try:
        conn = _grav_conn()
        rows = conn.execute(
            "SELECT id, star_ids, coherence_score, repetition_count, "
            "success_rate, gravity_score, channel, owner "
            "FROM constellations ORDER BY gravity_score DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        import json
        constellations = [
            {
                "id": r["id"],
                "member_count": len(json.loads(r["star_ids"])),
                "coherence_score": round(r["coherence_score"], 4),
                "repetition_count": r["repetition_count"],
                "success_rate": round(r["success_rate"], 4),
                "gravity_score": round(r["gravity_score"], 4),
                "channel": r["channel"],
                "owner": r["owner"],
            }
            for r in rows
        ]
        return {"constellations": constellations, "total": len(constellations)}
    except Exception as exc:
        return {"constellations": [], "total": 0, "error": str(exc)}


# ---------------------------------------------------------------------------
# GET /v1/dashboard/interactions
# ---------------------------------------------------------------------------

@router.get("/interactions")
async def dashboard_interactions(
    limit: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    """Recent Telegram interactions (user_memory.db)."""
    try:
        conn = _user_conn()
        rows = conn.execute(
            "SELECT i.id, i.user_id, i.user_input, i.bot_output, i.timestamp, "
            "p.name "
            "FROM interactions i "
            "LEFT JOIN profiles p ON i.user_id = p.user_id "
            "ORDER BY i.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        messages = []
        for r in rows:
            if r["user_input"]:
                messages.append({
                    "role": "user",
                    "content": r["user_input"][:300],
                    "user_name": r["name"] or r["user_id"][:15],
                    "timestamp": r["timestamp"],
                })
            if r["bot_output"]:
                messages.append({
                    "role": "assistant",
                    "content": r["bot_output"][:300],
                    "user_name": "Vectrax",
                    "timestamp": r["timestamp"],
                })
        return {"messages": messages, "total": len(messages)}
    except Exception as exc:
        return {"messages": [], "total": 0, "error": str(exc)}


# ---------------------------------------------------------------------------
# GET /v1/dashboard/users
# ---------------------------------------------------------------------------

@router.get("/users")
async def dashboard_users() -> Dict[str, Any]:
    """User profiles from Telegram (user_memory.db)."""
    try:
        conn = _user_conn()
        rows = conn.execute(
            "SELECT p.user_id, p.name, p.language, p.updated_at, "
            "(SELECT COUNT(*) FROM interactions i WHERE i.user_id = p.user_id) as msg_count "
            "FROM profiles p "
            "WHERE p.user_id NOT LIKE 'test:%' "
            "ORDER BY p.updated_at DESC"
        ).fetchall()
        conn.close()
        users = [
            {
                "user_id": r["user_id"],
                "name": r["name"] or "(sin nombre)",
                "language": r["language"] or "?",
                "msg_count": r["msg_count"],
                "last_active": r["updated_at"],
            }
            for r in rows
        ]
        return {"users": users, "total": len(users)}
    except Exception as exc:
        return {"users": [], "total": 0, "error": str(exc)}


# ---------------------------------------------------------------------------
# GET /v1/dashboard/operator
# ---------------------------------------------------------------------------

@router.get("/operator")
async def dashboard_operator() -> Dict[str, Any]:
    """Full operator status: runtime + governor + universe."""
    result: Dict[str, Any] = {}

    # Runtime metrics
    try:
        from core.operator.system_monitor import collect_metrics
        m = collect_metrics()
        result["runtime"] = {
            "status": m.status,
            "worker_alive": m.worker_alive,
            "worker_heartbeat_age_s": round(m.worker_heartbeat_age_s, 1),
            "queue_pending": m.queue_pending,
            "queue_processing": m.queue_processing,
            "queue_error": m.queue_error,
            "memory_mb": m.memory_mb,
            "active_users": m.active_users,
            "avg_latency_s": m.avg_latency_s,
            "max_latency_s": m.max_latency_s,
        }
    except Exception as exc:
        result["runtime"] = {"error": str(exc)}

    # Governor
    try:
        from core.governor import get_current_policy
        result["governor"] = get_current_policy()
    except Exception as exc:
        result["governor"] = {"error": str(exc)}

    # Universe snapshot
    try:
        from core.self_observation.universe_observer import observe_universe
        snap = observe_universe()
        result["universe"] = {
            "knowledge_stars": snap.knowledge_star_count,
            "user_stars": snap.star_count,
            "total_mass": round(snap.total_mass, 4),
            "pattern_count": snap.pattern_count,
            "convergences": len(snap.convergences),
            "core_stars": snap.core_star_count,
            "deep_memory": snap.deep_memory_count,
            "errors_24h": snap.recent_error_count_24h,
        }
    except Exception as exc:
        result["universe"] = {"error": str(exc)}

    # Operator layers
    try:
        from core.operator.nucleus import LAYERS
        result["layers"] = [
            {"id": l.id, "name": l.name, "status": l.status.value}
            for l in LAYERS
        ]
    except Exception as exc:
        result["layers"] = []

    result["ts"] = time.time()
    return result


# ---------------------------------------------------------------------------
# GET /v1/dashboard/proposals
# ---------------------------------------------------------------------------

@router.get("/proposals")
async def dashboard_proposals() -> Dict[str, Any]:
    """Proposals from gravitational DB."""
    try:
        conn = _grav_conn()
        rows = conn.execute(
            "SELECT id, constellation_id, description, evidence, status, created_at "
            "FROM proposals ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        conn.close()
        proposals = [
            {
                "id": r["id"],
                "constellation_id": r["constellation_id"],
                "description": r["description"][:300],
                "evidence": r["evidence"][:200] if r["evidence"] else "",
                "status": r["status"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
        return {"proposals": proposals, "total": len(proposals)}
    except Exception as exc:
        return {"proposals": [], "total": 0, "error": str(exc)}


# ---------------------------------------------------------------------------
# GET /v1/dashboard/audit
# ---------------------------------------------------------------------------

@router.get("/audit")
async def dashboard_audit(
    limit: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    """Audit events from audit_ledger.db (118K+ entries)."""
    _audit_db = Path(__file__).resolve().parents[3] / "vault" / "audit_ledger.db"
    try:
        conn = sqlite3.connect(str(_audit_db), timeout=3)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, timestamp, actor, role, action, decision, reason "
            "FROM audit_ledger ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        entries = [
            {
                "id": r["id"],
                "timestamp": (r["timestamp"] or "")[:19],
                "actor": r["actor"] or "",
                "role": r["role"] or "",
                "action": r["action"] or "",
                "decision": r["decision"] or "",
                "reason": r["reason"] or "",
            }
            for r in rows
        ]
        return {"entries": entries, "total": len(entries)}
    except Exception as exc:
        return {"entries": [], "total": 0, "error": str(exc)}
