"""
Vectrax — NucleusDecision
=====================================================================
Estructura de transporte para una acción operativa CANDIDATA producida
por `TotalConvergenceEngine._phase_synthesis()` a partir de lo que el
Núcleo ya observa (evidencia real de memoria, Capability Context,
IntentDecision) — sin duplicar identidad/correlación/clasificación, que
ya viajan por su propio canal (`user_id`/`correlation_id`/`classification`
en `ExecutionContext` y en la cadena `pipeline_worker.py` → ... →
`SmartRouter.route()`).

Vocabulario destino: `candidate_strategy` usa el mismo enum `Strategy`
de `core.smart_router` (incluyendo el nuevo valor `ANSWER_FROM_EVIDENCE`)
— es el vocabulario que ya consume directamente el dispatcher de
ejecución en `external_gateway.py::_resolve_via_pipeline()`, evitando que
`SmartRouter.select_strategy()` tenga que volver a derivar una estrategia
desde texto cuando el Núcleo ya cerró una candidata.

NO es un tipo de propuesta paralelo a `ExecutionContext`/`ActionProposal`:
transporta exclusivamente lo que esos dos NO cargan (evidencia real,
snapshot de capability, confianza/razón de la decisión del Núcleo).

Creado: 2026-09-17
Creador: Mario Bravo Castro
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - solo para type hints, evita ciclo
    from core.smart_router import Strategy


@dataclass
class NucleusDecision:
    """Acción operativa candidata propuesta por el Núcleo.

    Campos mínimos (deliberadamente NO incluye user_id/correlation_id/
    classification — esos ya viajan por su propio canal existente):

      candidate_strategy: `Strategy` propuesta, o `None` si el Núcleo no
        tiene evidencia/capacidad suficiente para proponer una — en ese
        caso `SmartRouter.route()` debe comportarse exactamente igual que
        hoy (ver contrato en `SmartRouter.route()`).
      evidence: evidencia real retenida (no colapsada a enteros) que
        respalda la candidata — para `ANSWER_FROM_EVIDENCE`, es la fuente
        de la que `_resolve_via_pipeline()` construye la respuesta
        directamente, sin invocar ningún resolver externo.
      capability: snapshot mínimo de qué capacidad(es) del Capability
        Context habilitaron la candidata (p.ej. qué entrada de
        `CapabilityContext.fallback_sources` se usó), para trazabilidad.
      confidence: confianza del Núcleo en la candidata (0.0–1.0).
      reason: texto corto, legible, de por qué se propuso (o no) una
        candidata — mismo espíritu que `SmartRoute.reason`.
      source: de qué motor/fase vino la decisión (default
        "total_convergence"); permite distinguir un futuro segundo emisor
        sin cambiar el contrato.
    """

    candidate_strategy: Optional["Strategy"] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    capability: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""
    source: str = "total_convergence"
    # Victoria C (2026-09-17): el MISMO `ConvergenceRecord.input_fingerprint`
    # de este ciclo — nunca recalculado. Viaja aquí ADEMÁS de propagarse por
    # su propio canal independiente (`input_fingerprint` en la cadena
    # `pipeline_worker.py` → ... → `ExternalGateway`), para que cualquier
    # consumidor de `NucleusDecision` (p.ej. `smart_route.metadata`) tenga
    # acceso directo sin tener que threadear un segundo parámetro. Vacío
    # ("") preserva el comportamiento actual sin cambios.
    fingerprint: str = ""

    @property
    def has_candidate(self) -> bool:
        """True si hay una `candidate_strategy` real que SmartRouter deba
        respetar sin volver a seleccionar desde texto."""
        return self.candidate_strategy is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_strategy": (
                self.candidate_strategy.value
                if self.candidate_strategy is not None else None
            ),
            "evidence": self.evidence,
            "capability": self.capability,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            "source": self.source,
            "fingerprint": self.fingerprint,
        }
