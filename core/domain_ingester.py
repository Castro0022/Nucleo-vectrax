"""
Domain Ingester — JSON → Gravitational Star
==============================================
Converts arbitrary business events into text that the gravitational
universe can embed and learn from.

Input: {"event_type": "sale", "data": {"product": "iPhone", "amount": 1199}}
Output: "sale: product=iPhone amount=1199"

The text is then embedded, passed through Learning Gate, and
incorporated into the gravitational universe under the tenant's domain.

Creador: Mario Bravo Castro
Fecha: 2026-06-15
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("vectrax.domain_ingester")


# Domain template cache
_templates: Dict[str, Dict] = {}


def _load_template(domain: str) -> Optional[Dict]:
    """Load domain template from config/domain_templates/{domain}.json."""
    if domain in _templates:
        return _templates[domain]
    try:
        import json
        template_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "domain_templates", f"{domain}.json",
        )
        if os.path.exists(template_path):
            tpl = json.load(open(template_path))
            _templates[domain] = tpl
            return tpl
    except Exception:
        pass
    _templates[domain] = None
    return None


def event_to_text(event_type: str, data: Dict[str, Any], domain: str = "") -> str:
    """Convert a business event into embeddable text.

    If a domain template exists with a text_template for this event_type,
    uses the template for richer text. Otherwise falls back to key=value format.
    """
    # Try domain template first
    if domain:
        tpl = _load_template(domain)
        if tpl:
            evt_config = tpl.get("event_types", {}).get(event_type, {})
            text_tpl = evt_config.get("text_template", "")
            if text_tpl:
                try:
                    return text_tpl.format(**data)
                except (KeyError, ValueError):
                    pass  # fallback to generic

    # Generic format
    parts = [event_type]
    for key, value in data.items():
        if isinstance(value, dict):
            for k2, v2 in value.items():
                parts.append(f"{key}.{k2}={v2}")
        elif isinstance(value, list):
            parts.append(f"{key}=[{len(value)} items]")
        else:
            parts.append(f"{key}={value}")

    return ": ".join(parts[:1]) + " " + " | ".join(parts[1:])


def ingest_event(
    tenant_id: str,
    domain: str,
    event_type: str,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Ingest a business event into the gravitational universe.

    Pipeline:
      1. Convert JSON to text
      2. Feed to gravity engine with domain tag
      3. Pass through Learning Gate
      4. Record in observation ledger

    Returns: {"success": True, "star_id": ..., "learning": "active/passive"}
    """
    t0 = time.perf_counter()
    text = event_to_text(event_type, data, domain=domain)
    fingerprint = f"{domain}:{event_type}:{_hash_data(data)}"

    result: Dict[str, Any] = {
        "success": False,
        "text": text[:200],
        "domain": domain,
        "event_type": event_type,
    }

    # 1. Feed to gravity engine
    try:
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()

        # Compute coherence from data richness
        field_count = len(data)
        cc_score = min(0.3 + (field_count * 0.1), 1.0)
        impact = "high" if field_count >= 5 else "medium" if field_count >= 3 else "low"

        gi.record_event(
            fingerprint=fingerprint,
            cc_score=cc_score,
            impact=impact,
            domain=domain,
            intent=event_type,
            outcome=text[:100],
            summary=text[:80],
        )
        result["star_id"] = fingerprint
    except Exception as exc:
        logger.warning("gravity ingest failed: %s", exc)
        result["error"] = str(exc)
        return result

    # 2. Embed and evaluate via Learning Gate
    learning_type = "active"
    try:
        from vectrax.embeddings import embed
        from core.learn.learning_gate import evaluate as lg_evaluate

        vec = embed(text)

        # Get existing domain embeddings for coherence check
        existing = []
        try:
            records = gi.all_records()
            domain_records = [r for r in records if r.domain == domain]
            # Use summaries as proxy (no direct embeddings in gravity engine)
        except Exception:
            pass

        decision = lg_evaluate(
            embedding=vec,
            user_id=tenant_id,
            pattern_count=len([r for r in gi.all_records() if r.domain == domain]),
            is_creator=False,
        )
        learning_type = decision.learning_type.value
        result["learning"] = learning_type
        result["novelty"] = round(decision.novelty, 3)
        result["coherence"] = round(decision.coherence, 3)
    except Exception as exc:
        logger.debug("learning gate eval failed: %s", exc)
        result["learning"] = "active"  # default to active if gate fails

    # 3. Record in observation ledger
    try:
        from core.self_observation.observation_ledger import record
        record(
            domain=domain,
            obs_type=f"ingest_{event_type}",
            summary=text[:120],
            star_id=fingerprint,
            evidence={"tenant": tenant_id, "fields": list(data.keys())[:10]},
        )
    except Exception:
        pass

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    result["success"] = True
    result["latency_ms"] = elapsed_ms

    logger.info(
        "[INGEST] %s | %s/%s | %s | %dms",
        tenant_id[:15], domain, event_type, learning_type, elapsed_ms,
    )

    # 4. Domain Knowledge Elevation — fire-and-forget
    # Check if any patterns in this domain have matured enough to elevate
    # to the shared domain library. Never blocks or affects the ingest.
    try:
        from core.domain_knowledge import try_elevate_from_gravity
        elevated = try_elevate_from_gravity(domain, tenant_id)
        if elevated:
            result["domain_elevated"] = elevated
    except Exception:
        pass

    return result


def _hash_data(data: Dict) -> str:
    """Short hash of data for deduplication."""
    import hashlib
    raw = str(sorted(data.items()))
    return hashlib.md5(raw.encode()).hexdigest()[:12]
