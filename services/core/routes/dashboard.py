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
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
logger = logging.getLogger("vectrax.core.routes.dashboard")

# DB paths (same as used by the rest of the system)
_GRAV_DB = Path.home() / ".vectrax" / "vectrax.db"
_USER_DB = Path(__file__).resolve().parents[3] / "vault" / "user_memory.db"
_QUEUE_DB = Path(__file__).resolve().parents[3] / "vault" / "message_queue.db"
_CYCLES_DB = Path(__file__).resolve().parents[3] / "vault" / "operational_cycles.db"
_TRAIN_HTML = Path(__file__).resolve().parents[2] / "ui" / "static" / "pipeline_train.html"


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
# GET /v1/dashboard/cycles  — Pipeline Train: operational cycle history
# ---------------------------------------------------------------------------

@router.get("/cycles")
async def dashboard_cycles(limit: int = Query(100, ge=1, le=1000)) -> Dict[str, Any]:
    """Recent operational cycles (perceive->interpret->decide->act->verify->respond).

    Read-only exposure of the already-persisted `op_cycles` table — no new
    computation. Powers the 'Pipeline Train' cycle-history view.
    """
    out: Dict[str, Any] = {"ts": time.time(), "total": 0, "summary": {}, "cycles": []}
    try:
        conn = sqlite3.connect(str(_CYCLES_DB), timeout=3)
        conn.row_factory = sqlite3.Row
        out["total"] = conn.execute("SELECT COUNT(*) FROM op_cycles").fetchone()[0]
        rows = conn.execute(
            "SELECT id, timestamp, channel, user_tier, perceive_intent, perceive_lang, "
            "interpret_action, decide_route, decide_confidence, act_latency_ms, act_fallback, "
            "verify_ran, verify_passed, respond_len, respond_source, total_latency_ms, success "
            "FROM op_cycles ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        n = len(rows)
        succ = sum(1 for r in rows if r["success"])
        by_source: Dict[str, int] = {}
        by_route: Dict[str, int] = {}
        for r in rows:
            by_source[r["respond_source"] or "?"] = by_source.get(r["respond_source"] or "?", 0) + 1
            by_route[r["decide_route"] or "?"] = by_route.get(r["decide_route"] or "?", 0) + 1
        out["summary"] = {
            "window": n,
            "success_rate": round(succ / max(n, 1) * 100, 1),
            "avg_latency_ms": round(sum((r["total_latency_ms"] or 0) for r in rows) / max(n, 1), 1),
            "fallbacks": sum(1 for r in rows if r["act_fallback"]),
            "by_source": by_source,
            "by_route": by_route,
        }
        out["cycles"] = [{k: r[k] for k in r.keys()} for r in rows]
    except Exception as exc:
        out["error"] = str(exc)
    return out


@router.get("/train/view", response_class=HTMLResponse)
async def pipeline_train_view():
    """Serve the Pipeline Train page (trading analytics + cycle history)."""
    if _TRAIN_HTML.exists():
        return HTMLResponse(_TRAIN_HTML.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>pipeline_train.html not found</h1>", status_code=404)


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
            "memory_peak_mb": m.memory_peak_mb,
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
        from core.universe_census import get_census
        snap = observe_universe()
        # convergences: canonical total from Census (SSOT), NOT
        # len(snap.convergences) - that list is a raw sample of graph
        # edges + legacy ledger rows, never a deduplicated total.
        result["universe"] = {
            "knowledge_stars": snap.knowledge_star_count,
            "user_stars": snap.star_count,
            "total_mass": round(snap.total_mass, 4),
            "pattern_count": snap.pattern_count,
            "convergences": get_census().convergences,
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
# GET /v1/dashboard/api_gates
# ---------------------------------------------------------------------------

@router.get("/api_gates")
async def dashboard_api_gates() -> Dict[str, Any]:
    """API rate limit gates — current backoff state per provider."""
    try:
        from core.api_gate import get_all_gates
        return {"gates": get_all_gates()}
    except Exception as exc:
        return {"gates": {}, "error": str(exc)}


# ---------------------------------------------------------------------------
# GET /v1/dashboard/word_gravity
# ---------------------------------------------------------------------------

@router.get("/word_gravity")
async def dashboard_word_gravity(
    scope: str = Query("global", description="Scope: 'global' or user_id"),
    limit: int = Query(30, ge=1, le=100),
) -> Dict[str, Any]:
    """Word Gravity Index — top words by gravitational mass."""
    try:
        from core.word_gravity import get_top_words, get_index_stats
        words = get_top_words(scope=scope, limit=limit)
        stats = get_index_stats()
        return {
            "words": [
                {"word": w, "mass": round(m, 4), "category": c}
                for w, m, c in words
            ],
            "stats": stats,
            "scope": scope,
        }
    except Exception as exc:
        return {"words": [], "stats": {}, "error": str(exc)}


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


# ---------------------------------------------------------------------------
# GET /v1/dashboard/incidents
# ---------------------------------------------------------------------------

@router.get("/incidents")
async def dashboard_incidents(
    limit: int = Query(20, ge=1, le=100),
) -> Dict[str, Any]:
    """Worker incidents captured by the BlackBox."""
    try:
        from core.observability.worker_blackbox import get_incidents, get_feedback_stats
        incidents = get_incidents(limit=limit)
        snapshots = [i for i in incidents if "worker_pid" in i]
        diagnoses = [i for i in incidents if "causa_probable" in i]
        feedbacks = [i for i in incidents if i.get("type") == "feedback"]
        return {
            "incidents": snapshots,
            "diagnoses": diagnoses,
            "feedbacks": feedbacks,
            "stats": get_feedback_stats(),
            "total": len(incidents),
        }
    except Exception as exc:
        return {"incidents": [], "diagnoses": [], "total": 0, "error": str(exc)}


from pydantic import BaseModel

class DiagnosisFeedback(BaseModel):
    incident_id: str
    verdict: str  # confirmed | rejected | partial
    creator_cause: str = ""
    notes: str = ""


@router.post("/incidents/feedback")
async def submit_diagnosis_feedback(fb: DiagnosisFeedback) -> Dict[str, Any]:
    """Creator validates or rejects a BlackBox diagnosis."""
    if fb.verdict not in ("confirmed", "rejected", "partial"):
        return {"error": "verdict must be confirmed|rejected|partial"}
    try:
        from core.observability.worker_blackbox import submit_feedback
        result = submit_feedback(
            incident_id=fb.incident_id,
            verdict=fb.verdict,
            creator_cause=fb.creator_cause,
            notes=fb.notes,
        )
        return {"success": True, **result}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# GET /v1/dashboard/observatory
# ---------------------------------------------------------------------------

@router.get("/observatory")
async def dashboard_observatory() -> Dict[str, Any]:
    """Consolidated observatory: every data source in one response.

    Sections: overview, universe, market, convergences, evolution, operator.
    All data is real — no placeholders, no simulated values.
    """
    result: Dict[str, Any] = {"ts": time.time()}

    # === CENSUS (single source of truth for totals) ===
    from core.universe_census import get_census
    census = get_census()

    # === GRAVITY ENGINE (details — totals come from census) ===
    gravity = {"total": census.gravitational, "domains": {}, "top_stars": [], "convergences": [], "tiers": {}}
    try:
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
        gravity["domains"] = gi.domain_stats()
        gravity["tiers"] = gi.tier_counts()
        # Reuse the cached global-scan candidate list (3s TTL) instead of
        # calling cross_domain_convergences() directly here - that scan is
        # expensive at real scale and this only needs a 20-item sample.
        # The total below always comes from the canonical registry via
        # Census, never from len(this sample).
        from core.self_observation.universe_observer import (
            get_full_convergence_candidates,
        )
        all_convergences = get_full_convergence_candidates()
        gravity["convergences_total"] = census.convergences
        gravity["convergences"] = all_convergences[:20]
        top = gi.top_stars(n=20)
        gravity["top_stars"] = [
            {
                "id": r.fingerprint[:30], "domain": r.domain, "intent": r.intent,
                "tier": r.tier, "hits": r.hits, "cc": round(r.cc_score, 3),
                "freq": round(r.freq, 3),
                "weight": round(r.hits * max(r.cc_score, 0.01) * max(r.freq, 0.01) * r.decay_factor, 2),
                "summary": (r.summary or "")[:80],
            }
            for r in top
        ]
        trends = gi.growth_trends(days=7)
        gravity["trends_7d"] = {
            "new": trends["new_stars"],
            "active": trends["active_stars"],
            "growing": trends["growing_stars"],
            "new_by_domain": trends["new_by_domain"],
        }
        trends_1d = gi.growth_trends(days=1)
        gravity["trends_24h"] = {
            "new": trends_1d["new_stars"],
            "active": trends_1d["active_stars"],
            "new_by_domain": trends_1d["new_by_domain"],
        }
    except Exception as exc:
        gravity["error"] = str(exc)
    result["gravity"] = gravity

    # === LEGACY GRAVITATIONAL DB ===
    legacy = {"knowledge_stars": 0, "user_stars": 0, "patterns": 0, "constellations": 0, "layers": {}}
    try:
        conn = _grav_conn()
        legacy["knowledge_stars"] = conn.execute("SELECT COUNT(*) FROM stars").fetchone()[0]
        legacy["user_stars"] = conn.execute("SELECT COUNT(*) FROM user_stars").fetchone()[0]
        legacy["patterns"] = conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0]
        try:
            legacy["constellations"] = conn.execute("SELECT COUNT(*) FROM constellations").fetchone()[0]
        except Exception:
            pass
        for row in conn.execute("SELECT layer, COUNT(*) as c FROM stars GROUP BY layer").fetchall():
            legacy["layers"][row["layer"]] = row["c"]
        conn.close()
    except Exception as exc:
        legacy["error"] = str(exc)
    result["legacy"] = legacy

    # === TOTAL STARS (from census — single source of truth) ===
    result["total_stars"] = census.total
    result["star_breakdown"] = {
        "gravitational": census.gravitational,
        "knowledge": census.knowledge,
        "users": census.users,
    }

    # === OBSERVATION BIAS ===
    try:
        from core.learn.observation_bias import get_bias
        result["observation_bias"] = get_bias()
    except Exception:
        result["observation_bias"] = {}

    # === LEARNING GATE STATS ===
    # Provide learning core visibility
    result["learning"] = {}
    try:
        from core.learn.pattern_refinement import _interaction_count, CYCLE_INTERVAL
        result["learning"]["interactions_until_refine"] = CYCLE_INTERVAL - _interaction_count
        result["learning"]["refine_interval"] = CYCLE_INTERVAL
    except Exception:
        pass

    # === MARKET OBSERVATORY (totals from census, details computed) ===
    market = {
        "symbols": [],
        "total_signals": census.market_signals,
        "total_patterns": census.market_patterns,
        "global_win_rate": census.market_win_rate,
    }
    try:
        from connectors.etoro.learning_engine import DEFAULT_WATCHLIST
        from connectors.etoro.signal_recorder import load_signals
        from connectors.etoro.pattern_memory import get_patterns

        all_signals = load_signals(limit=500)
        all_patterns = get_patterns()

        for sym in DEFAULT_WATCHLIST:
            s_upper = sym.upper()
            sym_signals = [s for s in all_signals if s.symbol == s_upper]
            sym_patterns = [p for p in all_patterns if p.symbol == s_upper]
            wins = sum(1 for s in sym_signals if s.status == "win")
            losses = sum(1 for s in sym_signals if s.status == "loss")
            best_exp = max((p.expectancy for p in sym_patterns), default=0)

            # Check gravity star
            gravity_star = None
            try:
                rec = gi.get(f"market:{s_upper}")
                if rec:
                    gravity_star = {
                        "hits": rec.hits, "cc": round(rec.cc_score, 3),
                        "tier": rec.tier,
                    }
            except Exception:
                pass

            market["symbols"].append({
                "symbol": s_upper,
                "signals": len(sym_signals),
                "patterns": len(sym_patterns),
                "wins": wins, "losses": losses,
                "win_rate": round(wins / max(wins + losses, 1) * 100, 1),
                "best_expectancy": round(best_exp, 3),
                "gravity_star": gravity_star,
                "last_signal": sym_signals[-1].timestamp if sym_signals else None,
            })
    except Exception as exc:
        market["error"] = str(exc)
    result["market"] = market

    # === USER MEMORY (from census) ===
    result["users"] = {
        "total": census.users_total,
        "interactions": census.interactions,
        "facts": census.user_facts,
        "core_memory": census.core_memory,
        "teams": census.teams,
    }

    # === CONVERGENCES (total from census) ===
    convergences = {"cross_domain": 0, "details": []}
    try:
        convergences["cross_domain"] = census.convergences
        convergences["details"] = gravity.get("convergences", [])[:10]
        # Alert history
        from core.learn.gravity_engine import get_alert_history
        convergences["alert_history"] = get_alert_history(limit=10)
    except Exception as exc:
        convergences["error"] = str(exc)
    result["convergences"] = convergences

    # === OPERATOR ===
    operator = {}
    try:
        from core.operator.system_monitor import collect_metrics
        m = collect_metrics()
        operator["worker_alive"] = m.worker_alive
        operator["worker_heartbeat_age"] = round(m.worker_heartbeat_age_s, 1)
        operator["status"] = m.status
        operator["queue_pending"] = m.queue_pending
        operator["queue_processing"] = m.queue_processing
        operator["queue_error"] = m.queue_error
        operator["memory_mb"] = m.memory_mb
        operator["memory_peak_mb"] = m.memory_peak_mb
        operator["active_users"] = m.active_users
        operator["avg_latency_s"] = m.avg_latency_s
    except Exception as exc:
        operator["runtime_error"] = str(exc)

    # Scheduler
    try:
        _sched_db = Path(__file__).resolve().parents[3] / "vault" / "scheduler.db"
        conn = sqlite3.connect(str(_sched_db), timeout=2)
        operator["scheduled_tasks"] = conn.execute("SELECT COUNT(*) FROM scheduled_tasks").fetchone()[0]
        conn.close()
    except Exception:
        operator["scheduled_tasks"] = 0

    # Audit ledger
    try:
        _audit_db = Path(__file__).resolve().parents[3] / "vault" / "audit_ledger.db"
        conn = sqlite3.connect(str(_audit_db), timeout=2)
        operator["audit_entries"] = conn.execute("SELECT COUNT(*) FROM audit_ledger").fetchone()[0]
        latest = conn.execute("SELECT timestamp FROM audit_ledger ORDER BY id DESC LIMIT 1").fetchone()
        operator["audit_latest"] = (latest[0] or "")[:19] if latest else ""
        conn.close()
    except Exception:
        operator["audit_entries"] = 0

    # Operational cycles
    try:
        _cycles_db = Path(__file__).resolve().parents[3] / "vault" / "operational_cycles.db"
        conn = sqlite3.connect(str(_cycles_db), timeout=2)
        operator["convergence_cycles"] = conn.execute("SELECT COUNT(*) FROM op_cycles").fetchone()[0]
        conn.close()
    except Exception:
        operator["convergence_cycles"] = 0

    result["operator"] = operator

    return result
