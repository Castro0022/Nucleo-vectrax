"""
intents/freight_intents.py — Freight Logistics conversational retrieval.

Fixes the audited PRIMARY break: freight evidence is persisted and queryable by
domain, but the Telegram response layer never read it. This module lets the
response pipeline, when a query is Freight Logistics, RETRIEVE the real
already-persisted evidence for domain ``freight_logistics`` from the three
domain-queryable stores and GROUND the answer exclusively on it — or ABSTAIN
explicitly when there is no evidence. It never falls back to the LLM and never
adds general knowledge.

Evidence sources (read-only, domain-scoped):
  • gravity_index      → GravityIndex().by_domain("freight_logistics")
  • observation_ledger → get_by_domain("freight_logistics")
  • domain_library     → get_domain_priors("freight_logistics") / summary

Provenance (which store) and the domain are preserved on every retrieved item.

This module does NOT touch the observation / ingestion / learning / maturation
cycle, thresholds, or existing data. It only reads.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger("vectrax.freight_intents")

# Canonical domain key used by connectors/freight/learning_cycle.py and the
# domain ingester. Single source of truth for retrieval.
FREIGHT_DOMAIN = "freight_logistics"

# Detection: freight / logistics vocabulary (EN + ES) plus the canonical event
# types from config/domain_templates/freight_logistics.json. Whole-word match.
_FREIGHT_TERMS = [
    "freight", "flete", "fletes", "fletar",
    "carga", "cargas", "cargamento", "cargamentos",
    "logistica", "logística", "logistico", "logístico", "logistics", "logistic",
    "load", "loads", "shipment", "shipments",
    "carrier", "carriers", "transportista", "transportistas",
    "camion", "camión", "camiones", "truck", "trucks", "trucking",
    "lane", "lanes", "ruta", "rutas", "route", "routes",
    "delivered", "delivery", "entrega", "entregas", "entregado", "entregados",
    "empty miles", "millas vacias", "millas vacías",
    # canonical event types
    "quote_request", "load_booking", "delivery_complete", "delay_reported",
    "rate_change", "capacity_update", "empty_miles", "weather_event",
]

_FREIGHT_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in _FREIGHT_TERMS) + r")\b",
    re.IGNORECASE,
)


def detect_freight_intent(text: str) -> bool:
    """Return True if the query is about Freight Logistics."""
    if not text:
        return False
    return bool(_FREIGHT_RE.search(text))


# ---------------------------------------------------------------------------
# Retrieval — real persisted evidence, by domain, with provenance
# ---------------------------------------------------------------------------

def retrieve_freight_evidence(limit: int = 5) -> Dict[str, Any]:
    """Retrieve freight_logistics evidence from the three domain stores.

    Read-only. Never raises. Each item keeps its ``source`` (store) and
    ``domain`` so provenance is preserved downstream.
    """
    ev: Dict[str, Any] = {
        "domain": FREIGHT_DOMAIN,
        "gravity": [],
        "ledger": [],
        "priors": [],
        "summary": {},
        "counts": {"gravity": 0, "ledger": 0, "priors": 0},
        "has_evidence": False,
    }

    # 1) gravity_index — stars observed under the domain
    try:
        from core.learn.gravity_engine import get_gravity_index
        recs = get_gravity_index().by_domain(FREIGHT_DOMAIN)
        recs = sorted(
            recs,
            key=lambda r: (getattr(r, "hits", 0), getattr(r, "cc_score", 0.0)),
            reverse=True,
        )[:limit]
        for r in recs:
            ev["gravity"].append({
                "source": "gravity_index.by_domain",
                "domain": getattr(r, "domain", FREIGHT_DOMAIN),
                "intent": getattr(r, "intent", "") or "",
                "fingerprint": getattr(r, "fingerprint", "") or "",
                "hits": getattr(r, "hits", 0),
                "cc_score": round(float(getattr(r, "cc_score", 0.0) or 0.0), 3),
                "tier": getattr(r, "tier", "") or "",
                "summary": (getattr(r, "summary", "") or "")[:140],
            })
    except Exception as exc:
        logger.debug("freight retrieve gravity failed: %s", exc)

    # 2) observation_ledger — recent domain observations
    try:
        from core.self_observation.observation_ledger import get_by_domain
        for o in get_by_domain(FREIGHT_DOMAIN, limit=limit):
            ev["ledger"].append({
                "source": "observation_ledger.get_by_domain",
                "domain": o.get("domain", FREIGHT_DOMAIN),
                "timestamp": o.get("timestamp", "") or "",
                "obs_type": o.get("obs_type", "") or "",
                "summary": (o.get("summary", "") or "")[:140],
                "star_id": o.get("star_id", "") or "",
            })
    except Exception as exc:
        logger.debug("freight retrieve ledger failed: %s", exc)

    # 3) domain_library — elevated (matured) domain patterns
    try:
        from core.domain_knowledge import get_domain_priors, get_domain_summary
        for p in get_domain_priors(FREIGHT_DOMAIN)[:limit]:
            ev["priors"].append({
                "source": "domain_library.get_domain_priors",
                "domain": getattr(p, "domain", FREIGHT_DOMAIN),
                "pattern_type": getattr(p, "pattern_type", "") or "",
                "win_rate": float(getattr(p, "win_rate", 0.0) or 0.0),
                "expectancy": float(getattr(p, "expectancy", 0.0) or 0.0),
                "sample_size": int(getattr(p, "sample_size", 0) or 0),
                "confidence": getattr(p, "confidence", "") or "",
                "contributing_tenants": int(getattr(p, "contributing_tenants", 0) or 0),
            })
        try:
            ev["summary"] = get_domain_summary(FREIGHT_DOMAIN) or {}
        except Exception:
            ev["summary"] = {}
    except Exception as exc:
        logger.debug("freight retrieve priors failed: %s", exc)

    ev["counts"] = {
        "gravity": len(ev["gravity"]),
        "ledger": len(ev["ledger"]),
        "priors": len(ev["priors"]),
    }
    ev["has_evidence"] = any(ev["counts"].values())
    return ev


# ---------------------------------------------------------------------------
# Response — grounded EXCLUSIVELY in retrieved evidence, or explicit abstention
# ---------------------------------------------------------------------------

def build_grounded_response(ev: Dict[str, Any]) -> str:
    """Build a response strictly from retrieved evidence, or abstain.

    No general knowledge, no invented facts: every line is derived from the
    retrieved records' own fields, tagged with the domain and the source store.
    """
    domain = ev.get("domain", FREIGHT_DOMAIN)
    counts = ev.get("counts", {})

    # Explicit abstention when there is no evidence in any store.
    if not ev.get("has_evidence"):
        return (
            f"No tengo evidencia suficiente en el dominio {domain} para responder eso.\n"
            f"(gravity_index: {counts.get('gravity', 0)} stars | "
            f"observation_ledger: {counts.get('ledger', 0)} obs | "
            f"domain_library: {counts.get('priors', 0)} patrones)\n"
            f"No genero respuesta sin evidencia observada."
        )

    lines = [f"📦 Freight Logistics — evidencia observada (dominio: {domain})", ""]

    if ev["priors"]:
        lines.append("🧠 Patrones (domain_library.get_domain_priors):")
        for p in ev["priors"]:
            lines.append(
                f"  • {p['pattern_type']} | WR {p['win_rate']:.0f}% "
                f"E {p['expectancy']:+.3f} | N={p['sample_size']} "
                f"tenants={p['contributing_tenants']} conf {p['confidence']}"
            )

    if ev["gravity"]:
        lines.append("🌌 Stars (gravity_index.by_domain):")
        for g in ev["gravity"]:
            label = g["intent"] or g["fingerprint"][:40]
            lines.append(
                f"  • {label} — hits {g['hits']}, cc {g['cc_score']}, tier {g['tier']}"
            )

    if ev["ledger"]:
        lines.append("📝 Observaciones (observation_ledger.get_by_domain):")
        for o in ev["ledger"]:
            ts = (o["timestamp"] or "")[:16]
            lines.append(f"  • {ts} {o['obs_type']} — {o['summary']}")

    lines.append("")
    lines.append(
        f"Fuente: dominio {domain} (evidencia real persistida y consultable por dominio). "
        f"Sin conocimiento externo."
    )
    return "\n".join(lines)


def resolve_freight_query(content: str = "") -> str:
    """Retrieve freight evidence and return a grounded response or abstention.

    Always returns a non-empty string when called, so the caller can safely
    short-circuit BEFORE the LLM (guaranteeing no general-knowledge answer).
    """
    ev = retrieve_freight_evidence()
    return build_grounded_response(ev)
