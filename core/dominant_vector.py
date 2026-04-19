"""
Vectrax Dominant Vector — Un foco por ciclo
===============================================
El sistema puede ser integral en visión, pero siempre actúa
en un vector dominante a la vez.

Cada ciclo evalúa el estado actual y selecciona UN vector:
  RESPOND   — hay pregunta activa, generar respuesta
  LEARN     — hay datos nuevos, registrar en memoria
  GROW      — sin input, buscar conexiones entre lo existente
  MONETIZE  — usuario activo sin pagar, mostrar valor
  REPAIR    — error o módulo caído, estabilizar

El vector con mayor urgencia × impacto gana. Solo uno a la vez.
Los demás quedan en cola.

Creado: 2026-03-30
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger("vectrax.dominant_vector")


# ---------------------------------------------------------------------------
# Vectores
# ---------------------------------------------------------------------------

class Vector(str, Enum):
    RESPOND = "respond"      # Pregunta activa → generar respuesta
    LEARN = "learn"          # Datos nuevos → registrar en memoria
    GROW = "grow"            # Sin input → buscar conexiones internas
    MONETIZE = "monetize"    # Usuario free activo → mostrar valor PRO
    REPAIR = "repair"        # Error detectado → estabilizar


# Peso base de cada vector (urgencia × impacto)
_BASE_WEIGHTS = {
    Vector.REPAIR: 100,      # Siempre máxima prioridad
    Vector.RESPOND: 80,      # Responder al usuario es urgente
    Vector.LEARN: 40,        # Aprender es importante pero no urgente
    Vector.MONETIZE: 30,     # Monetizar es impacto alto, urgencia baja
    Vector.GROW: 10,         # Crecer es background
}


# ---------------------------------------------------------------------------
# Estado del sistema
# ---------------------------------------------------------------------------

@dataclass
class SystemState:
    """Snapshot del estado actual del sistema para evaluación."""
    has_pending_message: bool = False
    has_new_data: bool = False
    has_error: bool = False
    error_module: str = ""
    user_is_free: bool = True
    user_message_count: int = 0      # Mensajes del usuario en esta sesión
    seconds_since_last_input: float = 0.0
    active_modules: list = field(default_factory=list)


@dataclass
class VectorDecision:
    """Resultado de la evaluación del vector dominante."""
    vector: Vector
    weight: float
    reason: str
    timestamp: float = 0.0
    suppressed: list = field(default_factory=list)  # Vectores que quedaron en cola

    def to_dict(self) -> dict:
        return {
            "vector": self.vector.value,
            "weight": round(self.weight, 2),
            "reason": self.reason,
            "suppressed": [v.value for v in self.suppressed],
        }


# ---------------------------------------------------------------------------
# Evaluador
# ---------------------------------------------------------------------------

class DominantVectorEvaluator:
    """
    Evalúa el estado del sistema y selecciona UN vector dominante.

    No ejecuta nada — solo decide. El pipeline usa la decisión
    para saber qué hacer en este ciclo.
    """

    def __init__(self) -> None:
        self._current: Optional[VectorDecision] = None
        self._history: list[VectorDecision] = []
        self._max_history = 100

    def evaluate(self, state: SystemState) -> VectorDecision:
        """
        Evalúa el estado actual y retorna el vector dominante.

        Reglas de selección (en orden de peso):
          1. REPAIR si hay error activo
          2. RESPOND si hay mensaje pendiente
          3. LEARN si hay datos nuevos sin pregunta
          4. MONETIZE si usuario free con >5 mensajes
          5. GROW si no hay input reciente (>30s)
        """
        candidates: dict[Vector, float] = {}
        reasons: dict[Vector, str] = {}

        # REPAIR — error activo
        if state.has_error:
            candidates[Vector.REPAIR] = _BASE_WEIGHTS[Vector.REPAIR]
            reasons[Vector.REPAIR] = f"error in {state.error_module or 'unknown'}"

        # RESPOND — mensaje pendiente
        if state.has_pending_message:
            candidates[Vector.RESPOND] = _BASE_WEIGHTS[Vector.RESPOND]
            reasons[Vector.RESPOND] = "pending_message"

        # LEARN — datos nuevos (sin pregunta pendiente)
        if state.has_new_data and not state.has_pending_message:
            candidates[Vector.LEARN] = _BASE_WEIGHTS[Vector.LEARN]
            reasons[Vector.LEARN] = "new_data_to_store"

        # MONETIZE — usuario free con actividad
        if state.user_is_free and state.user_message_count > 5:
            # Peso crece con uso — más mensajes = más urgencia de monetizar
            weight = _BASE_WEIGHTS[Vector.MONETIZE] + min(
                state.user_message_count * 2, 40,
            )
            candidates[Vector.MONETIZE] = weight
            reasons[Vector.MONETIZE] = f"free_user_{state.user_message_count}_msgs"

        # GROW — sin input reciente
        if state.seconds_since_last_input > 30 and not state.has_pending_message:
            candidates[Vector.GROW] = _BASE_WEIGHTS[Vector.GROW]
            reasons[Vector.GROW] = f"idle_{state.seconds_since_last_input:.0f}s"

        # Si no hay candidatos, default a GROW
        if not candidates:
            candidates[Vector.GROW] = _BASE_WEIGHTS[Vector.GROW]
            reasons[Vector.GROW] = "no_active_signals"

        # Seleccionar el de mayor peso
        winner = max(candidates, key=candidates.get)
        suppressed = [v for v in candidates if v != winner]

        decision = VectorDecision(
            vector=winner,
            weight=candidates[winner],
            reason=reasons[winner],
            timestamp=time.time(),
            suppressed=suppressed,
        )

        self._current = decision
        self._history.append(decision)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        logger.info(
            "VECTOR: %s (weight=%.0f, reason=%s, suppressed=%s)",
            winner.value, candidates[winner], reasons[winner],
            [v.value for v in suppressed],
        )

        return decision

    @property
    def current(self) -> Optional[VectorDecision]:
        """Vector dominante actual."""
        return self._current

    @property
    def history(self) -> list[dict]:
        """Últimas N decisiones."""
        return [d.to_dict() for d in self._history[-20:]]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_evaluator: Optional[DominantVectorEvaluator] = None


def get_vector_evaluator() -> DominantVectorEvaluator:
    global _evaluator
    if _evaluator is None:
        _evaluator = DominantVectorEvaluator()
    return _evaluator


def evaluate_vector(state: SystemState) -> VectorDecision:
    """Shortcut: evalúa y retorna el vector dominante."""
    return get_vector_evaluator().evaluate(state)


# ---------------------------------------------------------------------------
# Helper: construir estado desde contexto del pipeline
# ---------------------------------------------------------------------------

def build_state_from_context(
    has_pending_message: bool = False,
    has_new_data: bool = False,
    has_error: bool = False,
    error_module: str = "",
    user_tier: str = "free",
    user_message_count: int = 0,
    last_input_time: float = 0.0,
) -> SystemState:
    """Construir SystemState desde datos del pipeline."""
    now = time.time()
    return SystemState(
        has_pending_message=has_pending_message,
        has_new_data=has_new_data,
        has_error=has_error,
        error_module=error_module,
        user_is_free=(user_tier == "free"),
        user_message_count=user_message_count,
        seconds_since_last_input=(now - last_input_time) if last_input_time else 0.0,
    )
