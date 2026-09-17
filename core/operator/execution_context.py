"""
Vectrax — ExecutionContext (frontera constitucional PRE-ejecución)
=====================================================================
Objeto de transporte ligero que las 4 fronteras de ejecución externa (LLM,
Online, Places, Market) construyen en el punto donde se conoce el origen
real de la operación (USER o SYSTEM), y propagan hacia abajo — SIN
recalcular — hasta el punto semántico inmediatamente anterior al I/O real.

No es un tipo de propuesta paralelo: `.to_proposal()` mapea directamente a
los campos ya existentes de `core.operator.constitutional_filter.ActionProposal`,
reutilizando el evaluador de 7 principios existente sin tocar su firma ni la
del gate `respond` (`external_gateway.py::_constitutional_gate`).

`resolved_decision` es un cache interno keyed por boundary
(`"llm"`, `"online"`, `"places"`, `"market"`). Lo llena
`core.operator.pre_execution_gate.authorize()` la primera vez que resuelve
una operación, y lo consulta antes de re-evaluar — es el mecanismo que
permite "una sola autorización cubre la operación" en cadenas secuenciales
que cruzan varias funciones/archivos (ej. el fallback LLM de 3 pasos en
`external_gateway.py::_generate_cognitive_response`), sin perder la
cobertura estructural de cada executor de bajo nivel para callers no
relacionados.

Creado: 2026-09-17
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - solo para type hints, evita ciclo
    from core.operator.pre_execution_gate import GateDecision
    from core.operator.constitutional_filter import ActionProposal


# ---------------------------------------------------------------------------
# Origin
# ---------------------------------------------------------------------------

ORIGIN_USER = "USER"
ORIGIN_SYSTEM = "SYSTEM"
_VALID_ORIGINS = (ORIGIN_USER, ORIGIN_SYSTEM)


@dataclass
class ExecutionContext:
    """Contexto de ejecución de UNA operación gobernada (LLM/Online/Places/Market).

    Se construye UNA vez en el caller que conoce el origen real de la
    operación, y se propaga sin recalcular hacia las capas bajas (executors).

    Campos:
      origin: ORIGIN_USER | ORIGIN_SYSTEM.
      action: nombre de la acción constitucional — "resolve_llm" |
        "resolve_online" | "resolve_places" | "resolve_market".
      actor_id: user_id (USER) o identificador del disparador (SYSTEM, ej.
        "scheduler"). Puede ir vacío para SYSTEM.
      classification: intención/ruta ya calculada por el pipeline existente
        (SmartRouter, etc.) para origin=USER. Vacío para SYSTEM — no se
        inventa clasificación de texto conversacional inexistente.
      domain: dominio asociado, si aplica (Ley 2 del filtro constitucional).
      correlation_id: id de correlación de la operación completa. Si no se
        provee, se genera uno nuevo. Debe ser EL MISMO a través de los pasos
        de una misma operación secuencial (ej. los 3 intentos LLM) para que
        el cache de `resolved_decision` funcione.
      is_irreversible: siempre False en este PR — las 4 fronteras son
        read-only (búsqueda/consulta), nunca acciones destructivas.
      requested_targets: conjunto de destinos solicitados SOLO para
        operaciones de fan-out (ej. proveedores LLM en query_parallel).
        None para operaciones de destino único (online/places/market/llm
        single-provider).
      operation / trigger: para origin=SYSTEM, describen la operación real
        (ej. operation="MARKET_READ", trigger="SCHEDULED") en vez de una
        clasificación conversacional inexistente.
      resolved_decision: cache interno keyed por boundary. No se construye
        manualmente por los callers — lo gestiona `pre_execution_gate.py`.
    """

    origin: str
    action: str
    actor_id: str = ""
    classification: str = ""
    domain: str = ""
    correlation_id: str = ""
    is_irreversible: bool = False
    requested_targets: Optional[List[str]] = None
    operation: str = ""
    trigger: str = ""
    resolved_decision: Dict[str, "GateDecision"] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.origin not in _VALID_ORIGINS:
            # Fail-safe: un origin inválido nunca debe interpretarse como
            # USER silenciosamente. Se normaliza a SYSTEM (más conservador:
            # nunca se le atribuye un turno de usuario que no existió) y se
            # documenta en metadata implícito vía classification vacío.
            self.origin = ORIGIN_SYSTEM
        if not self.correlation_id:
            self.correlation_id = uuid.uuid4().hex[:12]

    def to_proposal(self) -> "ActionProposal":
        """Mapea este contexto a un `ActionProposal` real — sin tipo paralelo.

        Reutiliza exactamente los campos ya existentes de `ActionProposal`.
        Lo que no cabe (origin, requested_targets, operation, trigger) se
        transporta dentro de `ActionProposal.metadata`, que ya existe como
        catch-all genérico.
        """
        from core.operator.constitutional_filter import ActionProposal

        metadata: Dict[str, Any] = {
            "origin": self.origin,
            "boundary_action": self.action,
        }
        if self.requested_targets is not None:
            metadata["requested_targets"] = list(self.requested_targets)
        if self.operation:
            metadata["operation"] = self.operation
        if self.trigger:
            metadata["trigger"] = self.trigger

        return ActionProposal(
            action=self.action,
            correlation_id=self.correlation_id,
            # Para SYSTEM sin classification explícita, se usa `operation`
            # (ej. "MARKET_READ") como la "intención identificada" (Ley 1) —
            # nunca se inventa una clasificación conversacional.
            classification=self.classification or self.operation,
            is_irreversible=self.is_irreversible,
            # Por el momento en que este gate se consulta, la interacción
            # (mensaje de usuario ya recibido por el bus, o tarea programada
            # ya persistida) ya existe en almacenamiento estructural — mismo
            # criterio que usa hoy `external_gateway._constitutional_gate()`
            # para el gate `respond`.
            interaction_recorded=True,
            domain=self.domain,
            user_id=self.actor_id,
            action_logged=True,
            metadata=metadata,
        )

    def is_valid(self) -> bool:
        """Validación mínima: origin y action deben estar presentes.

        Un `ExecutionContext` "inválido" se trata igual que uno ausente por
        `pre_execution_gate.authorize()` (fail-safe, nunca autoriza
        silenciosamente sobre datos incompletos).
        """
        return bool(self.origin) and bool(self.action)

    def derive(self, action: str) -> "ExecutionContext":
        """Construye un `ExecutionContext` INDEPENDIENTE para una frontera
        distinta anidada dentro de esta operación (ej. la interpretación LLM
        anidada dentro de `resolve_online()`).

        Reutiliza SOLO los campos de transporte (origin, actor_id,
        correlation_id, classification, domain) — nunca el
        `resolved_decision` del padre, porque las fronteras se evalúan y
        promueven de forma independiente: el veredicto de una NUNCA debe
        heredarse a la otra, aunque una esté anidada dentro de la ejecución
        ya autorizada de la primera.
        """
        return ExecutionContext(
            origin=self.origin,
            action=action,
            actor_id=self.actor_id,
            classification=self.classification,
            domain=self.domain,
            correlation_id=self.correlation_id,
            is_irreversible=self.is_irreversible,
        )
