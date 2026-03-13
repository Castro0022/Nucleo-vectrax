"""
VX Prompt Engine — API de Alto Nivel
======================================
Funciones públicas para integración con el resto de Vectrax.

Uso típico::

    from core.prompt_engine.vx_prompt_api import optimize, evaluate_result

    pkg = optimize("Hazme un pipeline ETL en Python")
    print(pkg.prompt_final)
    # ... ejecutar prompt ...
    evaluate_result(pkg.prompt_id, score=0.9)
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

from core.prompt_engine.vx_prompt_engine import (
    PromptPackage,
    PromptSpec,
    VXPromptEngine,
    get_engine,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def optimize(
    text: str,
    context: str = "",
    language: str = "es",
    force_local: bool = False,
    variant: str = "medium",
    mode: str = "manual",
) -> PromptPackage:
    """
    Optimiza una instrucción humana y genera un PromptPackage completo.

    Args:
        text: Instrucción original del usuario.
        context: Contexto adicional.
        language: Idioma de salida.
        force_local: Forzar modelo local.
        variant: 'short', 'medium' o 'deep'.
        mode: 'manual' o 'auto'.

    Returns:
        PromptPackage con prompt_final listo para enviar.
    """
    engine = get_engine(mode=mode)
    package = engine.optimize(
        user_input=text,
        context=context,
        language=language,
        force_local=force_local,
        variant=variant,
    )

    # Audit log (best-effort)
    _audit_optimize(package)

    return package


def optimize_auto(text: str, **kwargs) -> PromptPackage:
    """
    Optimización automática — intercepta y mejora cualquier tarea.

    Shortcut para optimize(..., mode='auto').
    """
    kwargs.setdefault("mode", "auto")
    return optimize(text, **kwargs)


def evaluate_result(
    prompt_id: str,
    score: float,
    execution_ms: Optional[int] = None,
) -> bool:
    """
    Registra la calidad del resultado post-ejecución.

    Alimenta el sistema de aprendizaje para mejorar futuros prompts.

    Args:
        prompt_id: ID del prompt (PromptPackage.prompt_id).
        score: Calidad (0.0 - 1.0).
        execution_ms: Tiempo de ejecución.

    Returns:
        True si se registró.
    """
    engine = get_engine()
    result = engine.evaluate_result(prompt_id, score, execution_ms)

    if result:
        _audit_evaluate(prompt_id, score)

    return result


def get_stats() -> Dict:
    """Retorna estadísticas del motor y memoria."""
    engine = get_engine()
    return engine.get_stats()


def get_supported_intents():
    """Lista de intenciones soportadas."""
    engine = get_engine()
    return engine.get_supported_intents()


def detect_intent(text: str) -> tuple:
    """
    Detecta la intención sin optimizar.

    Returns:
        (intent: str, confidence: float)
    """
    engine = get_engine()
    return engine._detect_intent(text)


# ---------------------------------------------------------------------------
# Audit integration (best-effort)
# ---------------------------------------------------------------------------

def _audit_optimize(package: PromptPackage) -> None:
    """Registra la optimización en el audit ledger."""
    try:
        from core.audit_ledger import record

        record(
            action="prompt_optimize",
            actor="vx_prompt_engine",
            decision="optimized",
            reason=f"intent={package.spec.intent} model={package.model_provider}/{package.model_name}",
            metadata={
                "prompt_id": package.prompt_id,
                "intent": package.spec.intent,
                "confidence": package.spec.confidence,
                "model_provider": package.model_provider,
                "model_name": package.model_name,
                "variant": "medium",
            },
        )
    except Exception as e:
        logger.debug(f"Audit log for optimize failed (non-blocking): {e}")


def _audit_evaluate(prompt_id: str, score: float) -> None:
    """Registra la evaluación en el audit ledger."""
    try:
        from core.audit_ledger import record

        record(
            action="prompt_evaluate",
            actor="vx_prompt_engine",
            decision="evaluated",
            reason=f"prompt_id={prompt_id} score={score:.2f}",
            metadata={
                "prompt_id": prompt_id,
                "score": score,
            },
        )
    except Exception as e:
        logger.debug(f"Audit log for evaluate failed (non-blocking): {e}")
