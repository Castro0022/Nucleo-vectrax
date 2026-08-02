"""
connectors/real_estate/learning_cycle.py — Ciclo de aprendizaje del dominio.

Espejo del ciclo de freight (connectors/freight/learning_cycle.py): agnóstico al
proveedor, periódico y observable. Ingesta eventos → gravity, eleva patrones
maduros al domain library, y verifica los desenlaces contra la verdad objetiva.
Con eso, el criterio (core.learn.criterion) ya puede opinar sobre
florida_real_estate igual que sobre market o freight.

IMPORTANTE: este módulo es STANDALONE (se invoca con run_learning_cycle()). NO
está cableado al tick del pipeline_worker todavía — activarlo en el worker es un
paso deliberado posterior. Fault-isolated: cualquier excepción se captura.

Env:
    REAL_ESTATE_FEED_PROVIDER    simulator | attom   (default: simulator)
    REAL_ESTATE_EVENTS_PER_CYCLE int                 (default: 200)
    REAL_ESTATE_LEARN_ENABLED    1 | 0               (default: 1)
    REAL_ESTATE_VERIFY_ENABLED   1 | 0               (default: 1)
    REAL_ESTATE_TENANT_ID        tenant a registrar  (default: auto)

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("vectrax.real_estate.learning_cycle")

_DOMAIN = "florida_real_estate"
_DEFAULT_EVENTS = 200
_ENABLED = os.environ.get("REAL_ESTATE_LEARN_ENABLED", "1") == "1"


def run_learning_cycle(
    n_events: Optional[int] = None,
    tenant_id: Optional[str] = None,
    provider=None,
) -> Dict[str, Any]:
    """Ejecuta un ciclo de aprendizaje del dominio inmobiliario.

    Pasos: resolver proveedor → health-check → tenant → stream de eventos →
    ingesta (core.domain_ingester) → elevación única → verificación de
    desenlaces → resumen al ledger.
    """
    if not _ENABLED:
        return {"success": False, "detail": "REAL_ESTATE_LEARN_ENABLED=0"}

    t0 = time.time()
    n = n_events or int(os.environ.get("REAL_ESTATE_EVENTS_PER_CYCLE", _DEFAULT_EVENTS))
    summary: Dict[str, Any] = {
        "success": False, "provider": "unknown",
        "events_requested": n, "events_ingested": 0, "errors": 0,
        "stars_before": 0, "stars_after": 0, "mature_stars": 0,
        "patterns_elevated": 0, "elapsed_s": 0.0,
    }

    try:
        # 1. Proveedor
        if provider is None:
            from connectors.real_estate import get_provider
            provider = get_provider()
        summary["provider"] = provider.provider_name

        # 2. Health check (si no está sano, no rompemos: salimos limpio)
        hc = provider.health_check()
        if not hc.get("healthy"):
            summary["detail"] = f"provider unhealthy: {hc.get('detail')}"
            _record_ledger(summary)
            return summary

        # 3. Tenant
        tid = tenant_id or os.environ.get("REAL_ESTATE_TENANT_ID", "")
        if not tid:
            tid = _resolve_or_create_tenant()
        summary["tenant_id"] = tid

        # 4. Stars antes
        summary["stars_before"] = _count_stars(_DOMAIN)

        # 5-6. Ingesta
        from core.domain_ingester import ingest_event
        events = list(provider.stream_events(n))
        for ev in events:
            try:
                result = ingest_event(
                    tenant_id=tid, domain=_DOMAIN,
                    event_type=ev.event_type, data=ev.data,
                )
                if result.get("success"):
                    summary["events_ingested"] += 1
                else:
                    summary["errors"] += 1
            except Exception as exc:
                summary["errors"] += 1
                logger.debug("real_estate ingest error: %s", exc)

        # 7. Elevación única al cierre
        try:
            from core.domain_knowledge import try_elevate_from_gravity
            summary["patterns_elevated"] = try_elevate_from_gravity(_DOMAIN, tid) or 0
        except Exception as exc:
            logger.debug("real_estate elevation error: %s", exc)

        # 7.5 Verificación de desenlaces (verdad objetiva → ledger)
        if os.environ.get("REAL_ESTATE_VERIFY_ENABLED", "1") == "1":
            try:
                from connectors.real_estate.verification_cycle import verify_events
                vscore = verify_events(events, record=True)
                summary["verified_decisive"] = vscore.n_decisive
                summary["verified_wins"] = vscore.wins
                summary["verified_losses"] = vscore.losses
                summary["verified_win_rate"] = vscore.win_rate
                summary["verified_accuracy"] = vscore.accuracy
            except Exception as exc:
                logger.debug("real_estate verification error: %s", exc)

        # 8. Stars después + maduras
        summary["stars_after"], summary["mature_stars"] = _count_stars_mature(_DOMAIN)
        summary["success"] = True
        summary["elapsed_s"] = round(time.time() - t0, 2)

        logger.info(
            "real_estate.learning_cycle | provider=%s | ingested=%d/%d | errors=%d | "
            "stars=%d→%d | mature=%d | elevated=%d | %.1fs",
            summary["provider"], summary["events_ingested"], n, summary["errors"],
            summary["stars_before"], summary["stars_after"],
            summary["mature_stars"], summary["patterns_elevated"], summary["elapsed_s"],
        )
    except Exception as exc:
        summary["detail"] = str(exc)[:200]
        summary["elapsed_s"] = round(time.time() - t0, 2)
        logger.error("real_estate.learning_cycle FAILED: %s", exc)

    _record_ledger(summary)
    return summary


# ── Helpers ──────────────────────────────────────────────────────────────────

def _resolve_or_create_tenant() -> str:
    try:
        from core.tenant import create_tenant
        t = create_tenant(name="RealEstateSim", plan="creator", domain=_DOMAIN)
        return t["tenant_id"]
    except Exception:
        return "t_real_estate_default"


def _count_stars(domain: str) -> int:
    try:
        from core.learn.gravity_engine import GravityIndex
        return len(GravityIndex().by_domain(domain))
    except Exception:
        return 0


def _count_stars_mature(domain: str, min_hits: int = 15):
    try:
        from core.learn.gravity_engine import GravityIndex
        stars = GravityIndex().by_domain(domain)
        mature = [s for s in stars if s.hits >= min_hits]
        return len(stars), len(mature)
    except Exception:
        return 0, 0


def _record_ledger(summary: Dict[str, Any]) -> None:
    try:
        from core.operator import ledger_bridge as ledger
        ledger.record_event(
            action="real_estate_learning_cycle",
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
        pass  # ledger no disponible — el ciclo igual corrió
