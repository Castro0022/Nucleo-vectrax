"""
connectors/etoro/knowledge_gravity_seed.py — Incorporar el conocimiento
TA-Lib al universo cognitivo por su puerta YA EXISTENTE.

CORRECCIÓN (2026-09-24): la versión anterior de este módulo creaba 196
`GravityRecord` directamente en `gravity_index.json`, vía
`GravityIndex.update_records()`. Esa NO es la ruta que el creador definió
para "estrella normal de conocimiento" — `gravity_index.json` es la capa
de aprendizaje de PATRONES por dominio (`market:{symbol}`,
`freight:{lane}`, ...), no el universo cognitivo visual
(`vectrax.db.stars`, capas Core/Mid/Outer).

La ruta correcta, ya existente y ya usada en producción para conocimiento
de sistema (no conversacional): `vectrax.engine.ingest()`. Hay precedente
exacto: `core/gravity_sync.py::_promote_mature_patterns()` ya usa esta
misma función, con la misma convención `channel="user",
owner="vectrax_system"`, para llevar patrones maduros de dominio al
universo — la diferencia es que aquí el conocimiento entra ANTES de tener
experiencia (masa cero real, no artificial), y ese patrón lo hace después.

Esta corrección REUTILIZA `ingest()`, no reproduce su comportamiento:
  - No se crea ningún `GravityRecord` — cero import de `gravity_engine`.
  - No se asigna capa manualmente: resulta de `compute_star_gravity()` +
    `assign_layer()` dentro de `ingest()` (`vectrax/engine.py`,
    `vectrax/gravity.py`) — ninguno de los dos se toca aquí.
  - No se asigna masa artificial: una estrella nueva nace en `MIN_MASS`
    (`vectrax/models.py`), igual que cualquier otro contenido ingerido.
  - No se crean conexiones/convergencias/constelaciones en este archivo:
    las ejecuta `_post_ingest()` (dentro de `ingest()`), el mismo camino
    que sigue cualquier otro contenido del sistema.
  - La identidad/deduplicación es la que YA tiene `ingest()`
    (near-duplicate por similitud de embedding, dentro de
    `channel`+`owner`) — este módulo no añade una capa de identidad propia
    ni fuerza que sean 196 estrellas nuevas si alguna ya existe.

Texto de cada estrella: nombre + nombre largo oficial + categoría, tal
como los expone TA-Lib mismo (`talib.abstract.Function(name).info`) — dato
factual de la propia librería, no una relación inventada entre
indicadores ni ninguna implicación de trading.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger("vectrax.etoro.knowledge_gravity_seed")

# Misma convención que ya usa core/gravity_sync.py para conocimiento
# propio del sistema (no conversacional, no de un usuario real) — ya
# reconocida aparte por core/universe_census.py.
SEED_CHANNEL = "user"
SEED_OWNER = "vectrax_system"


def _knowledge_text(function_name: str) -> str:
    """Texto factual de UNA función de TA-Lib, tomado de sus propios
    metadatos (`display_name`/`group`) — nunca describe relaciones con
    otras funciones ni implicaciones de trading. Defensivo: si TA-Lib no
    está disponible o la función no resuelve metadatos, cae al nombre
    escueto en vez de lanzar."""
    try:
        from talib import abstract
        info = abstract.Function(function_name).info
        display_name = info.get("display_name") or function_name
        group = info.get("group") or ""
        if group:
            return f"{display_name} ({function_name}) — {group}"
        return f"{display_name} ({function_name})"
    except Exception:
        return function_name


def seed_knowledge_stars() -> Dict[str, Any]:
    """Incorpora el catálogo de `connectors.market.ta_knowledge` al
    universo cognitivo llamando a `vectrax.engine.ingest()` una vez por
    función — la puerta que Vectrax ya tenía para "esto es conocimiento
    nuevo", no una ruta construida para este propósito.

    Returns: {"total_candidates", "created", "already_present", "errors",
              "stars": [{"function", "star_id", "layer", "mass",
                         "gravity_score", "channel", "owner",
                         "repetition_count", "new"}, ...]}
    Nunca lanza — un fallo puntual por función se cuenta y se continúa.
    """
    from vectrax.engine import ingest
    from vectrax.db import get_all_stars
    from connectors.market.ta_knowledge import known_function_names

    names = known_function_names()
    # Identidad ANTES de tocar nada: qué estrellas de este canal/owner ya
    # existían. `ingest()` decide por sí mismo si una función es "nueva" o
    # "ya vista" (near-duplicate de embedding) — esto solo OBSERVA su
    # decisión para reportarla, no la sustituye.
    existing_ids_before = {
        s.id for s in get_all_stars(channel=SEED_CHANNEL, owner=SEED_OWNER)
    }

    created: List[str] = []
    already_present: List[str] = []
    stars_report: List[Dict[str, Any]] = []
    errors = 0

    for name in names:
        try:
            text = _knowledge_text(name)
            star = ingest(text=text, success=False, channel=SEED_CHANNEL, owner=SEED_OWNER)
            is_new = star.id not in existing_ids_before
            (created if is_new else already_present).append(name)
            stars_report.append({
                "function": name,
                "star_id": star.id,
                "layer": star.layer,
                "mass": star.mass,
                "gravity_score": star.gravity_score,
                "channel": star.channel,
                "owner": star.owner,
                "repetition_count": star.repetition_count,
                "new": is_new,
            })
        except Exception as exc:
            errors += 1
            logger.warning("[KNOWLEDGE_SEED] fallo en %s: %s", name, exc)

    logger.info(
        "[KNOWLEDGE_SEED] candidatas=%d creadas=%d ya_presentes=%d errores=%d",
        len(names), len(created), len(already_present), errors,
    )
    return {
        "total_candidates": len(names),
        "created": created,
        "already_present": already_present,
        "errors": errors,
        "stars": stars_report,
    }
