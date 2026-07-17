"""
connectors/freight/learning_cycle.py — Freight periodic learning cycle.

Architecture
------------
Runs as a timed tick inside pipeline_worker.py (every 6h, same pattern as
the eToro market learning cycle).  Key design decisions:

  1. Provider-agnostic: reads events from any FreightFeedProvider; the cycle
     itself never imports a concrete adapter.

  2. Elevation is PERIODIC, not per-ingest:
     Previously try_elevate_from_gravity() was called on every ingest_event()
     call, making it O(N×domains) and rewriting the domain library JSON on
     every single event.  Now elevation runs ONCE per cycle, after all events
     have been ingested.  This is equivalent to the eToro pattern where
     pattern_memory is rebuilt from outcomes in batch.

  3. Observable: every cycle emits a structured summary to the ledger and
     logs.  The summary is machine-readable so dashboards can consume it.

  4. Fault-isolated: any exception in the cycle is caught and logged; the
     pipeline_worker loop never crashes due to freight errors.

Env vars
--------
FREIGHT_FEED_PROVIDER   simulator | real            (default: simulator)
FREIGHT_EVENTS_PER_CYCLE  int                       (default: 200)
FREIGHT_LEARN_ENABLED     1 | 0                     (default: 1)
FREIGHT_TENANT_ID         tenant to record against  (default: auto)

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("vectrax.freight.learning_cycle")

_DOMAIN = "freight_logistics"
_DEFAULT_EVENTS = 200
_ENABLED = os.environ.get("FREIGHT_LEARN_ENABLED", "1") == "1"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_learning_cycle(
    n_events: Optional[int] = None,
    tenant_id: Optional[str] = None,
    provider=None,
) -> Dict[str, Any]:
    """
    Execute one freight learning cycle.

    Steps:
      1. Resolve provider (FREIGHT_FEED_PROVIDER → get_provider())
      2. Health-check provider
      3. Resolve / create tenant
      4. Stream n_events from provider
      5. Ingest each event via core.domain_ingester.ingest_event()
      6. Elevate mature patterns ONCE (not per-event)
      7. Record summary to ledger
      8. Return structured summary

    Args:
        n_events:  Override FREIGHT_EVENTS_PER_CYCLE. None = use env/default.
        tenant_id: Override FREIGHT_TENANT_ID. None = auto-resolve.
        provider:  Inject a FreightFeedProvider (used in tests).

    Returns:
        {
            "success": bool,
            "provider": str,
            "events_requested": int,
            "events_ingested": int,
            "errors": int,
            "stars_before": int,
            "stars_after": int,
            "mature_stars": int,
            "patterns_elevated": int,
            "elapsed_s": float,
        }
    """
    if not _ENABLED:
        return {"success": False, "detail": "FREIGHT_LEARN_ENABLED=0"}

    t0 = time.time()
    n = n_events or int(os.environ.get("FREIGHT_EVENTS_PER_CYCLE", _DEFAULT_EVENTS))
    summary: Dict[str, Any] = {
        "success": False,
        "provider": "unknown",
        "events_requested": n,
        "events_ingested": 0,
        "errors": 0,
        "stars_before": 0,
        "stars_after": 0,
        "mature_stars": 0,
        "patterns_elevated": 0,
        "elapsed_s": 0.0,
    }

    try:
        # ── 1. Provider ───────────────────────────────────────────────────
        if provider is None:
            from connectors.freight import get_provider
            provider = get_provider()
        summary["provider"] = provider.provider_name

        # ── 2. Health check ───────────────────────────────────────────────
        hc = provider.health_check()
        if not hc.get("healthy"):
            summary["detail"] = f"provider unhealthy: {hc.get('detail')}"
            _record_ledger(summary)
            return summary

        # ── 3. Tenant ─────────────────────────────────────────────────────
        tid = tenant_id or os.environ.get("FREIGHT_TENANT_ID", "")
        if not tid:
            tid = _resolve_or_create_tenant()
        summary["tenant_id"] = tid

        # ── 4. Stars before ───────────────────────────────────────────────
        summary["stars_before"] = _count_stars(_DOMAIN)

        # ── 5-6. Ingest events ────────────────────────────────────────────
        from core.domain_ingester import ingest_event
        events = provider.stream_events(n)
        for ev in events:
            try:
                result = ingest_event(
                    tenant_id=tid,
                    domain=_DOMAIN,
                    event_type=ev.event_type,
                    data=ev.data,
                )
                if result.get("success"):
                    summary["events_ingested"] += 1
                else:
                    summary["errors"] += 1
            except Exception as exc:
                summary["errors"] += 1
                logger.debug("freight ingest error: %s", exc)

        # ── 7. Single elevation at cycle end ──────────────────────────────
        try:
            from core.domain_knowledge import try_elevate_from_gravity
            elevated = try_elevate_from_gravity(_DOMAIN, tid)
            summary["patterns_elevated"] = elevated or 0
        except Exception as exc:
            logger.debug("freight elevation error: %s", exc)

        # ── 7.5 Verificación de resultados (cierre del ciclo) ─────────────
        # ADITIVO y defensivo: convierte los eventos de resultado ya streameados
        # (delivery_complete/delay_reported) en Outcomes VERIFICADOS contra la
        # verdad objetiva del dominio (on_time/delay) y los persiste en el
        # verification_ledger. Reemplaza el proxy de coherencia como fuente de
        # DESEMPEÑO. No toca ingest/elevación; si falla, el ciclo continúa igual.
        if os.environ.get("FREIGHT_VERIFY_ENABLED", "1") == "1":
            try:
                from connectors.freight.verification_cycle import verify_events
                vscore = verify_events(events, record=True)
                summary["verified_decisive"] = vscore.n_decisive
                summary["verified_wins"] = vscore.wins
                summary["verified_losses"] = vscore.losses
                summary["verified_win_rate"] = vscore.win_rate
                summary["verified_accuracy"] = vscore.accuracy
            except Exception as exc:
                logger.debug("freight verification error: %s", exc)

        # ── 8. Stars after + mature ────────────────────────────────
        summary["stars_after"], summary["mature_stars"] = _count_stars_mature(_DOMAIN)
        summary["success"] = True
        summary["elapsed_s"] = round(time.time() - t0, 2)

        logger.info(
            "freight.learning_cycle | provider=%s | ingested=%d/%d | errors=%d | "
            "stars=%d→%d | mature=%d | elevated=%d | %.1fs",
            summary["provider"],
            summary["events_ingested"], n, summary["errors"],
            summary["stars_before"], summary["stars_after"],
            summary["mature_stars"], summary["patterns_elevated"],
            summary["elapsed_s"],
        )

    except Exception as exc:
        summary["detail"] = str(exc)[:200]
        summary["elapsed_s"] = round(time.time() - t0, 2)
        logger.error("freight.learning_cycle FAILED: %s", exc)

    _record_ledger(summary)
    return summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_or_create_tenant() -> str:
    """Return or create the default freight tenant."""
    try:
        from core.tenant import create_tenant
        t = create_tenant(name="FreightSim", plan="creator", domain=_DOMAIN)
        return t["tenant_id"]
    except Exception:
        return "t_freight_default"


def _count_stars(domain: str) -> int:
    try:
        from core.learn.gravity_engine import GravityIndex
        gi = GravityIndex()
        return len(gi.by_domain(domain))
    except Exception:
        return 0


def _count_stars_mature(domain: str, min_hits: int = 15):
    try:
        from core.learn.gravity_engine import GravityIndex
        gi = GravityIndex()
        stars = gi.by_domain(domain)
        mature = [s for s in stars if s.hits >= min_hits]
        return len(stars), len(mature)
    except Exception:
        return 0, 0


def _record_ledger(summary: Dict[str, Any]) -> None:
    try:
        from core.operator import ledger_bridge as ledger
        ledger.record_event(
            action="freight_learning_cycle",
            category=ledger.EventCategory.LEARNING,
            risk_zone=ledger.RiskZone.GREEN,
            reason=(
                f"provider={summary.get('provider')} "
                f"ingested={summary.get('events_ingested')} "
                f"mature={summary.get('mature_stars')} "
                f"elevated={summary.get('patterns_elevated')}"
            ),
            details=summary,
        )
    except Exception:
        pass  # ledger unavailable — cycle still ran
