"""
VX Prompt Engine
=================
Módulo de optimización de prompts para Vectrax.

Convierte instrucciones humanas simples en prompts estructurados,
precisos y optimizados para el modelo de IA más adecuado.

API pública::

    from core.prompt_engine import optimize, optimize_auto, evaluate_result

    pkg = optimize("Hazme un script que descargue datos de una API")
    print(pkg.prompt_final)

    # Post-ejecución:
    evaluate_result(pkg.prompt_id, score=0.85)
"""

from core.prompt_engine.vx_prompt_api import (
    optimize,
    optimize_auto,
    evaluate_result,
    detect_intent,
    get_stats,
    get_supported_intents,
)
from core.prompt_engine.vx_prompt_engine import (
    PromptPackage,
    PromptSpec,
    VXPromptEngine,
    get_engine,
)

__all__ = [
    "optimize",
    "optimize_auto",
    "evaluate_result",
    "detect_intent",
    "get_stats",
    "get_supported_intents",
    "PromptPackage",
    "PromptSpec",
    "VXPromptEngine",
    "get_engine",
]
