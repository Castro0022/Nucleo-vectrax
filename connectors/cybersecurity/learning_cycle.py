"""
connectors/cybersecurity/learning_cycle.py — Ciclo incremental del dominio.

Espejo de connectors/real_estate/learning_cycle.py, adaptado a cybersecurity:
trae las CVE modificadas recientemente (proveedor incremental por `lastMod`),
las verifica (outcome por nivel de subject) y acumula masa de estrellas (bulk),
persistiendo el índice una vez al final.

IMPORTANTE: STANDALONE y GATED. `CYBER_LEARN_ENABLED` por defecto **0** (apagado)
hasta validar el backfill. NO está cableado al worker de forma activa; el bloque
del worker también respeta el gate. Fault-isolated: cualquier excepción se captura.

Env:
    CYBER_LEARN_ENABLED     1 | 0     (default: 0 — APAGADO)
    CYBER_FEED_PROVIDER     nvd | simulator (default: simulator)
    CYBER_EVENTS_PER_CYCLE  int       (default: 500)
    CYBER_VERIFY_ENABLED    1 | 0     (default: 1)

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from connectors.cybersecurity import verification_cycle as vc

logger = logging.getLogger("vectrax.cybersecurity.learning_cycle")

_DOMAIN = "cybersecurity"
_DEFAULT_EVENTS = 500


def _enabled() -> bool:
    return os.environ.get("CYBER_LEARN_ENABLED", "0") == "1"


def run_learning_cycle(n_events: Optional[int] = None, provider=None,
                       now: Optional[datetime] = None) -> Dict[str, Any]:
    """Ejecuta un ciclo incremental. No hace nada si CYBER_LEARN_ENABLED != 1."""
    if not _enabled():
        return {"success": False, "detail": "CYBER_LEARN_ENABLED=0 (apagado)"}

    t0 = time.time()
    n = n_events or int(os.environ.get("CYBER_EVENTS_PER_CYCLE", _DEFAULT_EVENTS))
    summary: Dict[str, Any] = {
        "success": False, "provider": "unknown", "events": 0,
        "verified_decisive": 0, "wins": 0, "losses": 0, "elapsed_s": 0.0,
    }
    try:
        if provider is None:
            from connectors.cybersecurity import get_provider
            provider = get_provider()
        summary["provider"] = provider.provider_name

        hc = provider.health_check()
        if not hc.get("healthy"):
            summary["detail"] = f"provider unhealthy: {hc.get('detail')}"
            _record_ledger(summary)
            return summary

        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
        records = gi.load_raw()

        events = list(provider.stream_events(n))
        record = os.environ.get("CYBER_VERIFY_ENABLED", "1") == "1"
        score = vc.verify_events(events, record=record,
                                 now=now or datetime.now(timezone.utc),
                                 gravity_records=records)
        gi.update_records(records)

        summary.update(
            events=len(events),
            verified_decisive=score.n_decisive,
            wins=score.wins, losses=score.losses,
            win_rate=score.win_rate,
            success=True, elapsed_s=round(time.time() - t0, 2),
        )
        logger.info(
            "cyber.learning_cycle | provider=%s events=%d dec=%d wins=%d losses=%d %.1fs",
            summary["provider"], summary["events"], summary["verified_decisive"],
            summary["wins"], summary["losses"], summary["elapsed_s"],
        )
    except Exception as exc:
        summary["detail"] = str(exc)[:200]
        summary["elapsed_s"] = round(time.time() - t0, 2)
        logger.error("cyber.learning_cycle FAILED: %s", exc)

    _record_ledger(summary)
    return summary


def _record_ledger(summary: Dict[str, Any]) -> None:
    try:
        from core.operator import ledger_bridge as ledger
        ledger.record_event(
            action="cyber_learning_cycle",
            category=ledger.EventCategory.LEARNING,
            risk_zone=ledger.RiskZone.GREEN,
            reason=(
                f"provider={summary.get('provider')} "
                f"events={summary.get('events')} "
                f"dec={summary.get('verified_decisive')}"
            ),
            details=summary,
        )
    except Exception:
        pass  # ledger no disponible — el ciclo igual corrió
