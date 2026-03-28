"""
Vectrax Core — Núcleo Central del Operador
=============================================
Punto de coordinación central. Registra las 12 capas de la arquitectura,
mantiene el estado operativo global, y sirve como punto de entrada para
la introspección del sistema.

El núcleo NO ejecuta — coordina. Cada capa tiene su propio motor.
El núcleo sabe qué capas existen, cuál es su estado, y cómo se conectan.

Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from core.operator.identity import (
    IDENTITY,
    OperatorMode,
    verify_identity_integrity,
)

logger = logging.getLogger("vectrax.operator.nucleus")


# ---------------------------------------------------------------------------
# Estado de una capa
# ---------------------------------------------------------------------------

class LayerStatus(str, Enum):
    """Estado de una capa del operador."""
    ACTIVE = "active"           # Funcionando con módulos implementados
    PARTIAL = "partial"         # Algunos módulos implementados
    PLANNED = "planned"         # Diseñada pero no implementada
    INACTIVE = "inactive"       # Desactivada temporalmente


@dataclass
class LayerDefinition:
    """Definición de una capa de la arquitectura del operador."""
    id: int
    name: str
    description: str
    status: LayerStatus
    modules: List[str] = field(default_factory=list)
    dependencies: List[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "modules": self.modules,
            "dependencies": self.dependencies,
        }


# ---------------------------------------------------------------------------
# Registro de las 12 capas
# ---------------------------------------------------------------------------

LAYERS: List[LayerDefinition] = [
    LayerDefinition(
        id=1,
        name="Núcleo Central",
        description="Punto de coordinación, registro de capas, estado global, convergencia total",
        status=LayerStatus.ACTIVE,
        modules=[
            "core.operator.nucleus",
            "core.operator.activation",
            "core.nucleus.total_convergence",
        ],
        dependencies=[],
    ),
    LayerDefinition(
        id=2,
        name="Percepción",
        description="Observación del entorno: archivos, señales, cambios",
        status=LayerStatus.ACTIVE,
        modules=[
            "cognition.perception.perception_engine",
            "cognition.perception.event_classifier",
            "cognition.perception.signal_intake",
            "cognition.perception.change_detector",
            "cognition.perception.observation_buffer",
            "cognition.perception.context_builder",
        ],
        dependencies=[1],
    ),
    LayerDefinition(
        id=3,
        name="Memoria Gravitacional",
        description="Registro permanente por gravedad: HOT→WARM→COLD→DEEP. Nada se borra.",
        status=LayerStatus.ACTIVE,
        modules=[
            "core.learn.gravity_engine",
            "core.learn.gravity_decay",
            "vectrax.gravity",
            "vectrax.memory",
            "cognition.memory.memory_engine",
            "cognition.memory.knowledge_graph",
            "cognition.memory.stores",
        ],
        dependencies=[1],
    ),
    LayerDefinition(
        id=4,
        name="Comprensión",
        description="Razonamiento sobre contexto percibido y memoria recuperada",
        status=LayerStatus.ACTIVE,
        modules=[
            "cognition.reasoning.reasoning_engine",
            "cognition.orchestrator.cognitive_loop",
            "cognition.reasoning.impact_analyzer",
            "cognition.reasoning.contradiction_detector",
        ],
        dependencies=[2, 3],
    ),
    LayerDefinition(
        id=5,
        name="Hipótesis e Imaginación Controlada",
        description="Generación de escenarios alternativos sin tratar como verdad",
        status=LayerStatus.ACTIVE,
        modules=[
            "cognition.reasoning.scenario_engine",
            "core.operator.hypothesis_engine",
        ],
        dependencies=[4],
    ),
    LayerDefinition(
        id=6,
        name="Acción",
        description="Ejecución controlada de propuestas aprobadas",
        status=LayerStatus.ACTIVE,
        modules=[
            "core.action_executor",
            "core.action_sandbox",
            "core.proposal_engine",
        ],
        dependencies=[4, 7],
    ),
    LayerDefinition(
        id=7,
        name="Gobernador de Riesgo",
        description="Evaluación probabilística de riesgo y control de autonomía",
        status=LayerStatus.ACTIVE,
        modules=[
            "core.governor",
            "core.risk_engine",
            "core.autonomy_policy",
            "core.policy_gates",
        ],
        dependencies=[1],
    ),
    LayerDefinition(
        id=8,
        name="Autoconstrucción de Módulos",
        description="Lectura de código, extracción de patrones, generación y validación de módulos",
        status=LayerStatus.ACTIVE,
        modules=[
            "core.operator.builder.code_reader",
            "core.operator.builder.pattern_extractor",
            "core.operator.builder.module_generator",
            "core.operator.builder.sandbox_validator",
            "core.operator.builder.integrator",
        ],
        dependencies=[4, 6, 7],
    ),
    LayerDefinition(
        id=9,
        name="Bus Universal de Integración",
        description="Canal centralizado de eventos entre todas las capas",
        status=LayerStatus.ACTIVE,
        modules=[
            "core.operator.universal_bus",
        ],
        dependencies=[1],
    ),
    LayerDefinition(
        id=10,
        name="Sistema de Contratos",
        description="Definición, validación y routing de contratos de conectores",
        status=LayerStatus.ACTIVE,
        modules=[
            "connectors.contracts.schema",
            "connectors.contracts.validator",
            "connectors.contracts.families",
            "core.operator.contracts_reader",
        ],
        dependencies=[9],
    ),
    LayerDefinition(
        id=11,
        name="Identidad Permanente",
        description="Manifiesto inmutable del operador: nombre, misión, principios",
        status=LayerStatus.ACTIVE,
        modules=["core.operator.identity"],
        dependencies=[],
    ),
    LayerDefinition(
        id=12,
        name="Ledger Visible",
        description="Registro append-only de todas las acciones del sistema",
        status=LayerStatus.ACTIVE,
        modules=[
            "core.audit_ledger",
            "core.causal_ledger",
        ],
        dependencies=[1],
    ),
]


# ---------------------------------------------------------------------------
# Núcleo del Operador
# ---------------------------------------------------------------------------

class Nucleus:
    """
    Núcleo central del operador Vectrax Core.

    Responsabilidades:
    - Mantener el registro de capas y su estado
    - Proveer introspección del sistema completo
    - Coordinar inicialización y verificación de integridad
    - Mantener el modo operativo actual
    """

    def __init__(self) -> None:
        self._layers: Dict[int, LayerDefinition] = {l.id: l for l in LAYERS}
        self._mode: OperatorMode = OperatorMode(IDENTITY.initial_mode)
        self._boot_time: str = datetime.now(timezone.utc).isoformat()
        self._cycle_count: int = 0
        self._last_integrity_check: Optional[str] = None
        logger.info(
            "Nucleus initialized | mode=%s | layers=%d | identity=%s",
            self._mode.value,
            len(self._layers),
            IDENTITY.name,
        )

    # -- Propiedades --------------------------------------------------------

    @property
    def mode(self) -> OperatorMode:
        return self._mode

    @property
    def boot_time(self) -> str:
        return self._boot_time

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    # -- Capas --------------------------------------------------------------

    def get_layer(self, layer_id: int) -> Optional[LayerDefinition]:
        return self._layers.get(layer_id)

    def get_all_layers(self) -> List[LayerDefinition]:
        return sorted(self._layers.values(), key=lambda l: l.id)

    def get_layers_by_status(self, status: LayerStatus) -> List[LayerDefinition]:
        return [l for l in self._layers.values() if l.status == status]

    def update_layer_status(self, layer_id: int, status: LayerStatus) -> bool:
        """Actualiza el estado de una capa. Retorna False si no existe."""
        layer = self._layers.get(layer_id)
        if layer is None:
            return False
        layer.status = status
        logger.info("Layer %d (%s) → %s", layer_id, layer.name, status.value)
        return True

    def register_module(self, layer_id: int, module_path: str) -> bool:
        """Registra un módulo en una capa. Retorna False si la capa no existe."""
        layer = self._layers.get(layer_id)
        if layer is None:
            return False
        if module_path not in layer.modules:
            layer.modules.append(module_path)
            logger.info("Module registered: %s → layer %d", module_path, layer_id)
        return True

    # -- Modo operativo -----------------------------------------------------

    def set_mode(self, mode: OperatorMode, *, reason: str = "") -> None:
        """
        Cambiar el modo operativo.
        ZONA AMARILLA: requiere registro en ledger.
        """
        old = self._mode
        self._mode = mode
        logger.warning(
            "Mode change: %s → %s | reason: %s",
            old.value, mode.value, reason or "not specified",
        )

    # -- Ciclo --------------------------------------------------------------

    def increment_cycle(self) -> int:
        self._cycle_count += 1
        return self._cycle_count

    # -- Integridad ---------------------------------------------------------

    def check_integrity(self) -> Dict[str, Any]:
        """
        Verificación de integridad del sistema completo.
        Retorna un dict con el resultado de cada verificación.
        """
        now = datetime.now(timezone.utc).isoformat()
        self._last_integrity_check = now

        identity_ok = verify_identity_integrity()

        # Verificar que capas críticas estén activas
        critical_layers = [1, 7, 11, 12]  # núcleo, gobernador, identidad, ledger
        critical_ok = all(
            self._layers.get(lid, LayerDefinition(
                id=lid, name="?", description="?", status=LayerStatus.INACTIVE
            )).status in (LayerStatus.ACTIVE, LayerStatus.PARTIAL)
            for lid in critical_layers
        )

        # Verificar dependencias
        dep_violations = []
        for layer in self._layers.values():
            for dep_id in layer.dependencies:
                dep = self._layers.get(dep_id)
                if dep and dep.status == LayerStatus.INACTIVE:
                    dep_violations.append(
                        f"Layer {layer.id} ({layer.name}) depends on "
                        f"inactive layer {dep_id} ({dep.name})"
                    )

        result = {
            "timestamp": now,
            "identity_intact": identity_ok,
            "critical_layers_ok": critical_ok,
            "dependency_violations": dep_violations,
            "total_layers": len(self._layers),
            "active": len(self.get_layers_by_status(LayerStatus.ACTIVE)),
            "partial": len(self.get_layers_by_status(LayerStatus.PARTIAL)),
            "planned": len(self.get_layers_by_status(LayerStatus.PLANNED)),
            "mode": self._mode.value,
            "cycles": self._cycle_count,
            "healthy": identity_ok and critical_ok and len(dep_violations) == 0,
        }

        logger.info("Integrity check: healthy=%s", result["healthy"])
        return result

    # -- Introspección ------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Retorna el estado completo del operador para display."""
        return {
            "identity": IDENTITY.to_dict(),
            "mode": self._mode.value,
            "boot_time": self._boot_time,
            "cycles": self._cycle_count,
            "last_integrity_check": self._last_integrity_check,
            "layers": [l.to_dict() for l in self.get_all_layers()],
            "summary": {
                "total": len(self._layers),
                "active": len(self.get_layers_by_status(LayerStatus.ACTIVE)),
                "partial": len(self.get_layers_by_status(LayerStatus.PARTIAL)),
                "planned": len(self.get_layers_by_status(LayerStatus.PLANNED)),
            },
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_nucleus: Optional[Nucleus] = None


def get_nucleus() -> Nucleus:
    """Obtener la instancia singleton del núcleo."""
    global _nucleus
    if _nucleus is None:
        _nucleus = Nucleus()
    return _nucleus


def reset_nucleus() -> None:
    """Reset para testing. NO usar en producción."""
    global _nucleus
    _nucleus = None
