"""
Motor 4 — Cognitive Orchestrator (DEPRECATED).

Este módulo implementaba el primer ciclo cognitivo de Vectrax.
Fue superado por la Arquitectura C (core/operator/) que implementa
las 12 capas del operador universal con runtime soberano, bus
universal, ledger, y ciclo GUIDED con aprobación del creador.

RUTA ACTIVA:
  - Runtime:     core.operator.activation.OperatorRuntime
  - Ciclo:       core.operator.activation.run_operator_forever()
  - CLI:         vx run  /  vx activate  /  vx watch
  - Status:      core.operator.activation.get_operator_status()

Este módulo se mantiene para compatibilidad con tests históricos.
NO usar para nuevas integraciones.
"""
import warnings as _warnings

_DEPRECATED = True
_SUCCESSOR = "core.operator.activation"

_warnings.warn(
    "cognition.orchestrator is deprecated. Use core.operator instead.",
    DeprecationWarning,
    stacklevel=2,
)

from cognition.orchestrator.orchestrator import CognitiveOrchestrator

__all__ = ["CognitiveOrchestrator", "_DEPRECATED", "_SUCCESSOR"]
