"""
core/learn/strategic_assets.py — Estrellas documentales estratégicas.

Registra documentos estratégicos del propio sistema (p. ej. el Asset Evidence
Map) como estrellas en el universo gravitacional, para que VECTRAX los reconozca
como mapas de evidencia y valor — no notas cualquiera.

A diferencia de las 7 Leyes Fundamentales (que son SESGO DE SELECCIÓN
pre-universo y NO deben ser contenido del universo), un documento estratégico SÍ
es contenido legítimo: una estrella con masa propia en el dominio
``strategic_asset``.

Reutiliza el gravity index (mismas estructuras que el resto de estrellas).
Idempotente y best-effort: nunca rompe el arranque.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
from typing import Dict, List

logger = logging.getLogger("vectrax.learn.strategic_assets")

# Dominio bajo el que viven las estrellas documentales estratégicas.
ASSET_DOMAIN = "strategic_asset"
_FINGERPRINT_PREFIX = "asset"

# Documentos estratégicos vivos del sistema.
_STRATEGIC_ASSETS: List[Dict[str, str]] = [
    {
        "name": "VECTRAX_ASSET_EVIDENCE_MAP",
        "intent": "evidence",
        "status": "living_document",
        "path": "docs/VECTRAX_ASSET_EVIDENCE_MAP.md",
        "domains": "company/architecture/evidence",
    },
]


def _fingerprint(name: str) -> str:
    """Id estable de la estrella documental: ``asset:{name}``."""
    return f"{_FINGERPRINT_PREFIX}:{(name or '').strip().lower()}"


def seed_strategic_asset_stars() -> List[str]:
    """Registra una estrella documental por asset estratégico si aún no existe.

    Idempotente (no re-siembra si el fingerprint ya existe) y best-effort
    (nunca lanza). Devuelve los nombres recién sembrados.
    """
    seeded: List[str] = []
    try:
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
    except Exception as exc:
        logger.debug("seed_strategic_asset_stars: gravity index unavailable: %s", exc)
        return seeded

    for asset in _STRATEGIC_ASSETS:
        name = asset.get("name", "")
        if not name:
            continue
        fp = _fingerprint(name)
        try:
            if gi.get(fp) is not None:
                continue  # ya registrado — idempotente
            summary = (
                f"{name} | {asset.get('status', 'living_document')} | "
                f"{asset.get('domains', '')} | {asset.get('path', '')}"
            ).strip()
            gi.record_event(
                fingerprint=fp,
                cc_score=0.9,
                impact="low",
                domain=ASSET_DOMAIN,
                intent=asset.get("intent", "evidence"),
                outcome="observed",
                summary=summary,
                source="strategic_asset_seed",
            )
            seeded.append(name)
        except Exception as exc:
            logger.debug("seed_strategic_asset_stars(%s) swallowed: %s", name, exc)
    return seeded


def list_strategic_asset_stars() -> List[Dict[str, str]]:
    """Inventario de estrellas documentales estratégicas (APIs/tests/panel)."""
    out: List[Dict[str, str]] = []
    try:
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
        for r in gi.all_records():
            if r.domain == ASSET_DOMAIN:
                parts = r.fingerprint.split(":", 1)
                out.append({
                    "fingerprint": r.fingerprint,
                    "name": parts[1] if len(parts) > 1 else r.fingerprint,
                    "domain": r.domain,
                    "intent": r.intent,
                    "summary": r.summary,
                })
    except Exception as exc:
        logger.debug("list_strategic_asset_stars swallowed: %s", exc)
    return out
