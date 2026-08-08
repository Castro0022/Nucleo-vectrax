"""
core/operator/external_learning.py — Lazo de aprendizaje externo (Fase 5 del
pedido / Fase 4 del plan).

Cuando Vectrax sale a buscar afuera por falta de convergencia interna, ese
resultado NO se descarta ni se asume como verdad: entra como una OBSERVACIÓN
nueva con procedencia marcada `external`, y se alimenta al ciclo de aprendizaje
(`learning_cycle`) para que atraviese contraste → verificación → outcome.

La promoción a conocimiento propio (regla/convergencia) NO ocurre por el solo
hecho de repetirse: la decide el pipeline existente, que exige hipótesis
CONFIRMED y confianza ≥ MIN_CONFIDENCE_FOR_INTEGRATION, más Decision Authority.
Aquí solo SEMBRAMOS la observación y la señal; nunca integramos directamente.

Principios:
  - Reutiliza `observation_ledger` (memoria de observaciones) y `learning_cycle`
    (pipeline de aprendizaje). No crea sistemas paralelos.
  - Defensivo: nunca rompe la respuesta al usuario (todo en try/except).
  - No-op si no hay respuesta externa real.

Creador: Mario Bravo Castro
Fecha: 2026-08-08
"""
from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger("vectrax.external_learning")


def is_external_route(route: str) -> bool:
    """True si la ruta indica que se usó una búsqueda EXTERNA (web/online).

    Cubre `online`, `local_to_online`, `places_to_online`, etc. NO marca como
    externas las rutas grounded internas (memoria, criterio, freight, etc.).
    """
    if not route:
        return False
    return "online" in route.lower()


def _resolve_domain(query: str, domain: Optional[str]) -> str:
    """Bucket de dominio para la observación. Deriva del query si no se da."""
    if domain:
        return domain
    try:
        from core.learn.criterion import detect_domain
        return detect_domain(query) or "external"
    except Exception:
        return "external"


def ingest_external_result(
    query: str,
    answer: str,
    route: str,
    confidence: float = 0.0,
    engines: Optional[List[str]] = None,
    domain: Optional[str] = None,
) -> bool:
    """Siembra un resultado externo como observación + señal de aprendizaje.

    Devuelve True si al menos se registró la observación. Nunca lanza.

    Pasos:
      1. Registra la observación con procedencia `external` en observation_ledger
         (contenido durable y auditable de lo que se trajo de afuera).
      2. Alimenta el learning_cycle con un InputEvent etiquetado `source=external`
         e `intent=external_knowledge`, para que el pipeline lo contraste y
         eventualmente lo promueva (solo si CONFIRMED y supera el umbral).
    """
    if not query or not answer or not answer.strip():
        return False

    recorded = False
    dom = _resolve_domain(query, domain)

    # --- 1. Observación con procedencia external ---
    try:
        from core.self_observation.observation_ledger import record
        record(
            domain=dom,
            obs_type="external_answer",
            summary=f"Consulta externa resuelta | q={query[:60]}",
            evidence={
                "source": "external",
                "route": route,
                "query": query[:200],
                "engines": list(engines or []),
                "confidence": round(float(confidence or 0.0), 4),
                "answer_preview": answer[:160],
            },
            severity="info",
        )
        recorded = True
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("external observation record failed: %s", exc)

    # --- 2. Señal al ciclo de aprendizaje (contraste/verificación/promoción) ---
    try:
        from core.learning_cycle.anomaly_detector import InputEvent
        from core.learning_cycle.pipeline import get_learning_pipeline
        get_learning_pipeline().process_event(InputEvent(
            text=query[:200],
            intent="external_knowledge",
            source="external",
            length=len(query),
            metadata={
                "route": route,
                "engines": list(engines or []),
                "confidence": round(float(confidence or 0.0), 4),
                "domain": dom,
            },
        ))
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("external learning feed failed: %s", exc)

    return recorded
