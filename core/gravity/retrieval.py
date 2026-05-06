"""
core/gravity/retrieval.py — Memory retrieval con ranking combinado.

Fórmula del score final (Memory Governance Engine):

    final_score =
        semantic_similarity   * 0.55   # cosine_similarity (vector_store)
      + identity_mass_norm    * 0.25   # masa gravitacional acumulada
      + recency_norm          * 0.10   # frescura del último retrieval/ts
      + retrieval_count_norm  * 0.10   # cuántas veces se trajo

Reglas duras:
  - identity_mass DOMINA sobre cronología: una memoria con masa alta
    (DEEP, PERSON_RECOGNIZED, essential_memory) gana a una nueva.
  - NUNCA cruzar user_id: el filtro está en el SELECT del store.
  - NUNCA disparar deep retrieval sin señales de pasado (gate previo
    en `gravity_engine.should_use_deep_memory`).
  - NUNCA inyectar memoria completa al prompt: este módulo devuelve
    los records candidatos; el caller decide qué fragmentos
    incorporar.

API:
    retrieve(store, user_id, query_embedding, limit=5,
             gate_message=None, weights=None) -> list[dict]
        Devuelve la lista de hits ordenados por final_score, cada
        uno con su breakdown (`semantic`, `mass`, `recency`,
        `retrieval`, `final`).
        Actualiza retrieval_count + last_retrieved_at de los hits
        retornados.

    rank_results(records, query_embedding, weights=None,
                 now=None) -> list[dict]
        Funcional puro (sin tocar el store ni hacer touch). Útil para
        tests deterministas.

    DEFAULT_WEIGHTS  exposed para tweakeo
"""

from __future__ import annotations

import datetime
import logging
import math
from typing import Any, Dict, List, Optional, Sequence

from core.gravity.vector_store import cosine_similarity

logger = logging.getLogger("vectrax.gravity.retrieval")


# ===========================================================================
# Pesos
# ===========================================================================

DEFAULT_WEIGHTS = {
    "semantic": 0.55,
    "mass": 0.25,
    "recency": 0.10,
    "retrieval": 0.10,
}

# Ventana de recency: 30 días = 1.0; >30d cae logarítmicamente
_RECENCY_WINDOW_DAYS = 30.0


def _validate_weights(w: Dict[str, float]) -> Dict[str, float]:
    base = dict(DEFAULT_WEIGHTS)
    if w:
        base.update({k: float(v) for k, v in w.items() if k in base})
    return base


# ===========================================================================
# Normalizaciones
# ===========================================================================

def _normalize_mass(records: List[Dict[str, Any]]) -> Dict[str, float]:
    """Normaliza la masa cognitiva de cada record contra el máximo del set.

    Cada record contribuye con max(mass, gravity_score, identity_mass).
    Devuelve dict id → 0..1.
    """
    out: Dict[str, float] = {}
    if not records:
        return out
    raw = {}
    for r in records:
        score = max(
            float(r.get("mass") or 0.0),
            float(r.get("gravity_score") or 0.0),
            float(r.get("identity_mass") or 0.0),
        )
        raw[r["id"]] = score
    max_v = max(raw.values()) if raw else 0.0
    if max_v <= 0:
        return {k: 0.0 for k in raw}
    return {k: v / max_v for k, v in raw.items()}


def _normalize_retrieval(records: List[Dict[str, Any]]) -> Dict[str, float]:
    """Normaliza retrieval_count contra el máximo del set."""
    out: Dict[str, float] = {}
    if not records:
        return out
    raw = {r["id"]: float(r.get("retrieval_count") or 0.0) for r in records}
    max_v = max(raw.values()) if raw else 0.0
    if max_v <= 0:
        return {k: 0.0 for k in raw}
    return {k: v / max_v for k, v in raw.items()}


def _normalize_recency(
    records: List[Dict[str, Any]],
    now: Optional[datetime.datetime] = None,
) -> Dict[str, float]:
    """Recency 0..1 basada en last_retrieved_at o ts.

    >0 días: cae logarítmicamente. Records sin timestamp obtienen 0.
    """
    n = now or datetime.datetime.utcnow()
    out: Dict[str, float] = {}
    for r in records:
        last_iso = r.get("last_retrieved_at") or ""
        last_dt: Optional[datetime.datetime] = None
        if last_iso:
            try:
                last_dt = datetime.datetime.fromisoformat(
                    last_iso.rstrip("Z"),
                )
            except Exception:
                last_dt = None
        if last_dt is None and r.get("ts"):
            try:
                last_dt = datetime.datetime.utcfromtimestamp(
                    float(r["ts"]),
                )
            except Exception:
                last_dt = None
        if last_dt is None:
            out[r["id"]] = 0.0
            continue
        days = max(0.0, (n - last_dt).total_seconds() / 86400.0)
        if days <= 0:
            out[r["id"]] = 1.0
            continue
        # 0 días → 1.0 ; 30 días → ~0.5 ; 365 días → ~0.1
        score = 1.0 / (1.0 + math.log1p(days / _RECENCY_WINDOW_DAYS))
        out[r["id"]] = max(0.0, min(1.0, score))
    return out


# ===========================================================================
# Pure ranking
# ===========================================================================

def rank_results(
    records: List[Dict[str, Any]],
    query_embedding: Sequence[float],
    weights: Optional[Dict[str, float]] = None,
    now: Optional[datetime.datetime] = None,
) -> List[Dict[str, Any]]:
    """Aplica la fórmula combinada y devuelve records ordenados DESC
    por final_score. NO toca el store.
    """
    if not records:
        return []
    w = _validate_weights(weights or {})
    mass_norm = _normalize_mass(records)
    rcount_norm = _normalize_retrieval(records)
    recency_norm = _normalize_recency(records, now=now)

    out: List[Dict[str, Any]] = []
    for r in records:
        emb = r.get("embedding") or []
        sem = cosine_similarity(query_embedding, emb) if emb else 0.0
        sem = max(0.0, min(1.0, sem))
        m = mass_norm.get(r["id"], 0.0)
        rc = rcount_norm.get(r["id"], 0.0)
        rec = recency_norm.get(r["id"], 0.0)
        final = (
            sem * w["semantic"]
            + m * w["mass"]
            + rec * w["recency"]
            + rc * w["retrieval"]
        )
        out.append({
            **r,
            "score_semantic": round(sem, 4),
            "score_mass": round(m, 4),
            "score_recency": round(rec, 4),
            "score_retrieval": round(rc, 4),
            "score": round(final, 4),
            "final_score": round(final, 4),
        })
    out.sort(key=lambda x: x["final_score"], reverse=True)
    return out


# ===========================================================================
# Public retrieve()
# ===========================================================================

def retrieve(
    store,
    user_id: str,
    query_embedding: Sequence[float],
    limit: int = 5,
    gate_message: Optional[str] = None,
    weights: Optional[Dict[str, float]] = None,
    known_names: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Retorna los top_k records del user con scoring combinado.

    Args:
      store: SQLiteVectorStore.
      user_id: dueño estricto (RBAC).
      query_embedding: vector de consulta.
      limit: cuántos hits devolver.
      gate_message: si se pasa, se valida con `should_use_deep_memory`
        antes de tocar la DB. Si el gate cierra, devuelve [] sin
        consultar.
      weights: override de pesos (semantic/mass/recency/retrieval).
      known_names: lista de nombres del face_memory del user, para
        ayudar al gate.

    Side-effect: para cada hit retornado se actualiza retrieval_count
    y last_retrieved_at en el store.
    """
    if not user_id or not query_embedding:
        return []

    # Gate previo (regla dura: nunca disparar deep retrieval sin señales)
    if gate_message is not None:
        try:
            from core.gravity.gravity_engine import should_use_deep_memory
            if not should_use_deep_memory(
                gate_message,
                user_id=user_id,
                known_names=known_names,
            ):
                logger.debug(
                    "retrieval: gate cerró el deep retrieval (msg=%r)",
                    (gate_message or "")[:60],
                )
                return []
        except Exception as exc:
            logger.debug("retrieval: gate import failed: %s", exc)

    # Recolecta solo records ACTIVE del user (RBAC en SQL)
    active = store.list_active_for_user(user_id, limit=2000)
    if not active:
        return []

    ranked = rank_results(active, query_embedding, weights=weights)
    top = ranked[: max(0, int(limit))]

    # Side-effect: registra cada hit como retrieved
    for hit in top:
        try:
            store.touch_retrieval(hit["id"])
        except Exception as exc:
            logger.debug(
                "retrieval: touch_retrieval failed for %s: %s",
                hit.get("id"), exc,
            )

    return top
