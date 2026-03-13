"""
VX Prompt Engine — Router de Prompts
======================================
Capa de enrutamiento que mapea intenciones del prompt engine a las
capacidades del ModelRouter existente.

Decisiones de routing:
  - razonamiento → REASONING capability
  - redacción/creative → CREATIVE + REASONING
  - código → CODING capability
  - síntesis → SUMMARIZATION capability
  - privacidad local → force local_only
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intent → model preference mapping
# ---------------------------------------------------------------------------

# Maps prompt engine intents to model_router TaskType values
INTENT_TO_TASK_TYPE: Dict[str, str] = {
    "codigo": "code",
    "ventas": "creative",
    "analisis": "reasoning",
    "investigacion": "reasoning",
    "redaccion": "creative",
    "contratos": "reasoning",
    "automatizacion": "code",
    "estrategia": "reasoning",
    "imagen": "creative",
    "sintesis": "summarization",
}

# Intents that benefit from local-only execution (privacy)
LOCAL_PREFERRED_INTENTS = {"contratos", "estrategia"}


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class PromptRoutingResult:
    """Resultado del enrutamiento de un prompt optimizado."""
    model_provider: str
    model_name: str
    task_type: str
    reason: str
    score: float
    local_only: bool = False
    fallback_provider: Optional[str] = None
    fallback_model: Optional[str] = None


# ---------------------------------------------------------------------------
# PromptRouter
# ---------------------------------------------------------------------------

class PromptRouter:
    """
    Enruta prompts optimizados al modelo más adecuado.

    Integra con core.routing.model_router.ModelRouter y añade lógica
    específica del prompt engine (preferencia por historial, privacidad, etc.).
    """

    def __init__(self):
        self._model_router = None

    def _get_model_router(self):
        """Lazy import del ModelRouter para evitar dependencias circulares."""
        if self._model_router is None:
            try:
                from core.routing.model_router import get_model_router
                self._model_router = get_model_router()
            except ImportError:
                logger.warning("ModelRouter not available — using fallback routing")
                self._model_router = None
        return self._model_router

    def select_model(
        self,
        intent: str,
        prompt_text: str,
        force_local: bool = False,
        preferred_model: Optional[str] = None,
    ) -> PromptRoutingResult:
        """
        Selecciona el mejor modelo para un prompt optimizado.

        Pipeline:
          1. Consultar historial (mejor patrón conocido para este intent)
          2. Mapear intent → TaskType
          3. Delegar a ModelRouter con contexto enriquecido
          4. Aplicar restricciones de privacidad si aplica

        Args:
            intent: Intención detectada (codigo, ventas, analisis, etc.)
            prompt_text: El prompt final construido.
            force_local: Forzar ejecución local (privacidad).
            preferred_model: Modelo preferido por historial.

        Returns:
            PromptRoutingResult con modelo seleccionado y razón.
        """
        task_type = INTENT_TO_TASK_TYPE.get(intent, "chat")
        local_only = force_local or intent in LOCAL_PREFERRED_INTENTS

        # Check if env variable forces local
        if os.environ.get("VX_FORCE_LOCAL") == "1":
            local_only = True

        # Try to use the existing ModelRouter
        router = self._get_model_router()

        if router is not None:
            return self._route_via_model_router(
                router, prompt_text, intent, task_type, local_only
            )

        # Fallback: return a sensible default
        return self._fallback_route(intent, task_type, local_only)

    def _route_via_model_router(
        self,
        router,
        prompt_text: str,
        intent: str,
        task_type: str,
        local_only: bool,
    ) -> PromptRoutingResult:
        """Delega al ModelRouter existente con contexto del prompt engine."""
        try:
            context = f"[VX_PROMPT_ENGINE] intent={intent} task_type={task_type}"
            if local_only:
                # Temporarily set env for local-only routing
                old_val = os.environ.get("VX_FORCE_LOCAL")
                os.environ["VX_FORCE_LOCAL"] = "1"

            decision = router.route(prompt_text, context=context)

            if local_only and old_val is None:
                os.environ.pop("VX_FORCE_LOCAL", None)
            elif local_only and old_val is not None:
                os.environ["VX_FORCE_LOCAL"] = old_val

            return PromptRoutingResult(
                model_provider=decision.primary.provider,
                model_name=decision.primary.model,
                task_type=task_type,
                reason=f"intent={intent} | {decision.reason}",
                score=decision.score,
                local_only=local_only,
                fallback_provider=decision.fallback.provider if decision.fallback else None,
                fallback_model=decision.fallback.model if decision.fallback else None,
            )
        except Exception as e:
            logger.warning(f"ModelRouter failed: {e} — using fallback")
            return self._fallback_route(intent, task_type, local_only)

    def _fallback_route(
        self,
        intent: str,
        task_type: str,
        local_only: bool,
    ) -> PromptRoutingResult:
        """
        Enrutamiento de respaldo cuando el ModelRouter no está disponible.

        Usa heurísticas simples basadas en el intent.
        """
        # Default model preferences per category
        FALLBACK_MODELS = {
            "code": ("ollama", "codellama"),
            "reasoning": ("ollama", "llama3"),
            "creative": ("ollama", "llama3"),
            "summarization": ("ollama", "llama3"),
            "chat": ("ollama", "llama3"),
        }

        provider, model = FALLBACK_MODELS.get(task_type, ("ollama", "llama3"))

        return PromptRoutingResult(
            model_provider=provider,
            model_name=model,
            task_type=task_type,
            reason=f"fallback_route | intent={intent} | local_only={local_only}",
            score=0.5,
            local_only=local_only,
        )

    def get_intent_mapping(self) -> Dict[str, str]:
        """Retorna el mapeo completo de intenciones a task types."""
        return dict(INTENT_TO_TASK_TYPE)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_global_router: Optional[PromptRouter] = None


def get_prompt_router() -> PromptRouter:
    """Retorna la instancia global de PromptRouter."""
    global _global_router
    if _global_router is None:
        _global_router = PromptRouter()
    return _global_router
