"""
Vectrax — Convergence Hook
============================
Wrapper no-fatal del motor de Convergencia Total (core/nucleus/total_convergence.py).

Inyectable en cualquier punto del pipeline — garantiza que TODO mensaje
pase obligatoriamente por el ciclo de 7 fases antes de generar respuesta,
con énfasis en:

  [3] Memoria Estructural — conecta con patrones previos (gravity + CC + graph)
  [6] Gravitación         — almacena según peso gravitacional en el núcleo

Uso:
    from core.convergence_hook import run_convergence_cycle
    record = run_convergence_cycle(content, source="telegram", owner=user_id)
    # record.memory_connections  → fase [3] ejecutada
    # record.gravitational_tier  → fase [6] ejecutada
    # record.action_recommended  → "proceed" | "review" | "block" | "learn_only"

Principios:
  - NON-FATAL: si el motor falla, el pipeline continúa sin interrupciones.
  - SINGLETON: la instancia del motor persiste entre llamadas (eficiencia).
  - TRAZABLE: loggea fases [3] y [6] para auditoría.
  - SIN RESPUESTA AL USUARIO: solo enriquece el ciclo cognitivo interno.

Creado: 2026-05-20
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.nucleus.total_convergence import ConvergenceRecord

logger = logging.getLogger("vectrax.convergence_hook")


def run_convergence_cycle(
    content: str,
    *,
    source: str = "unknown",
    channel: str = "",
    owner: str = "",
    metadata: Optional[dict] = None,
) -> Optional["ConvergenceRecord"]:
    """
    Ejecuta obligatoriamente el ciclo completo de 7 fases del motor de
    Convergencia Total antes de que el pipeline genere una respuesta.

    Fases garantizadas antes de responder:
      [3] Memoria Estructural  → record.memory_connections > 0 si hay patrones
      [6] Gravitación          → record.gravitational_tier contiene el nivel asignado

    Args:
        content:  Texto del mensaje entrante.
        source:   Origen del mensaje ("telegram", "api", "webhook", etc.).
        channel:  Canal interno de Vectrax ("user" | "creator").
        owner:    user_id del remitente.
        metadata: Metadatos adicionales opcionales.

    Returns:
        ConvergenceRecord con resultado completo del ciclo, o None si el motor
        falló (el pipeline continúa normalmente en ese caso).
    """
    if not content or not content.strip():
        return None

    try:
        from core.nucleus.total_convergence import get_convergence_engine

        engine = get_convergence_engine()

        # Activar el motor si no está activo
        if not engine.is_active:
            try:
                engine.activate()
                logger.info("convergence_hook: motor activado automáticamente")
            except Exception as _ae:
                logger.debug("convergence_hook: activate() falló (non-fatal): %s", _ae)

        record = engine.process(
            content,
            source=source,
            channel=channel,
            owner=owner,
            metadata=metadata or {},
        )

        # Loggear evidencia de las fases críticas
        _log_critical_phases(record, source, owner)

        return record

    except Exception as exc:
        # NON-FATAL: cualquier fallo del motor NO debe romper el pipeline
        logger.warning(
            "convergence_hook SKIPPED (non-fatal) | source=%s owner=%s | %s",
            source, owner[:20] if owner else "?", exc,
        )
        return None


def _log_critical_phases(record: "ConvergenceRecord", source: str, owner: str) -> None:
    """Loggea evidencia de que las fases [3] y [6] se ejecutaron."""
    phase3_ok = "memory" in record.phases_completed
    phase6_ok = "gravitation" in record.phases_completed

    logger.info(
        "CONVERGENCE_CYCLE | source=%s owner=%s "
        "phase[3]_memory=%s connections=%d patterns=%d "
        "phase[6]_gravitation=%s tier=%s "
        "action=%s novel=%s coherence=%.3f time=%.1fms",
        source,
        (owner or "?")[:20],
        "✓" if phase3_ok else "✗",
        record.memory_connections,
        record.prior_patterns_found,
        "✓" if phase6_ok else "✗",
        record.gravitational_tier or "?",
        record.action_recommended,
        record.is_novel,
        record.coherence_score,
        record.convergence_time_ms,
    )

    if not phase3_ok:
        logger.warning("convergence_hook: fase [3] MEMORIA no completada — revisar motor")
    if not phase6_ok:
        logger.warning("convergence_hook: fase [6] GRAVITACIÓN no completada — revisar motor")


def should_block(record: Optional["ConvergenceRecord"]) -> bool:
    """
    True si el record de convergencia indica que el mensaje debe bloquearse.
    Solo bloquea cuando action_recommended == "block" (política explícita del governor).
    En todos los demás casos (proceed, review, learn_only, None) el pipeline continúa.
    """
    if record is None:
        return False
    return record.action_recommended == "block"
