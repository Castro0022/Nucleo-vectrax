"""
Vectrax — Filtro Constitucional (7 Principios)
=================================================
Filtro único que evalúa cualquier propuesta de acción contra los 7
Principios Fundamentales de Vectrax (`core.operator.identity.FUNDAMENTAL_LAWS`,
inspirados en el Kybalion) ANTES de que la acción se ejecute.

Contrato cerrado de 3 estados — nunca un cuarto estado:
    PASS    — el principio permite la acción.
    CAUTION — evidencia insuficiente, dependencia caída, o violación de bajo
              riesgo. Requiere paso por DecisionAuthority.
    BLOCK   — violación confirmada en una acción irreversible/crítica. La
              acción NO se ejecuta.

Regla de oro: evidencia insuficiente, historial insuficiente, dependencia
caída, o excepción interna → SIEMPRE CAUTION, nunca PASS. Un fallo técnico
del filtro completo nunca se convierte silenciosamente en autorización.

Este módulo ENVUELVE (no reemplaza) `core.operator.law_enforcement`:
  - Reutiliza `enforce_law_1..7` y los colectores de señal real
    (`detect_active_cycles`, `detect_governor_mode`, etc.) ya existentes.
  - NO toca `FUNDAMENTAL_LAWS` (contenido/pesos) ni `law_signal.py`/
    `PresenciaObserver` (siguen operando como pesos, sin cambios).
  - Añade la interpretación de 3 estados (PASS/CAUTION/BLOCK) que
    `enforce_law_N` no tiene (solo booleano `passed`), y el fail-safe
    invertido: donde `law_enforcement.py` asume "no hay evidencia →
    pasa" (para no generar falsos positivos en el peso de
    PresenciaObserver), aquí "no hay evidencia → CAUTION" (para no
    autorizar sin saber).

Modo de operación (shadow/enforce): ver `core.operator.constitutional_mode`.
En Fase 1, este módulo se invoca EXCLUSIVAMENTE en modo shadow — evalúa y
registra en el ledger, pero nunca altera el comportamiento del pipeline.

Creado: 2026-08-14
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.operator.constitutional_filter")


# ---------------------------------------------------------------------------
# Contrato de 3 estados — cerrado, nunca un cuarto estado
# ---------------------------------------------------------------------------

class PrincipleVerdict(str, Enum):
    PASS = "pass"
    CAUTION = "caution"
    BLOCK = "block"


# Orden de severidad para agregar el veredicto general (mayor gana).
_SEVERITY = {PrincipleVerdict.PASS: 0, PrincipleVerdict.CAUTION: 1, PrincipleVerdict.BLOCK: 2}


# ---------------------------------------------------------------------------
# Resultado por principio
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PrincipleResult:
    """Resultado de evaluar UN principio contra una propuesta de acción."""
    number: int
    name: str
    verdict: PrincipleVerdict
    reason: str
    evidence_available: Dict[str, Any] = field(default_factory=dict)
    evidence_missing: Tuple[str, ...] = ()
    recommended_action: str = ""
    retryable: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "number": self.number,
            "name": self.name,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "evidence_available": self.evidence_available,
            "evidence_missing": list(self.evidence_missing),
            "recommended_action": self.recommended_action,
            "retryable": self.retryable,
        }


# ---------------------------------------------------------------------------
# Propuesta de acción — lo que se evalúa
# ---------------------------------------------------------------------------

@dataclass
class ActionProposal:
    """
    Propuesta de acción a evaluar. Nunca contiene instrucciones sobre CÓMO
    responder ni QUÉ herramienta usar — solo la acción ya decidida y el
    contexto necesario para que los 7 principios opinen sobre si procede.
    """
    action: str                          # p.ej. "respond", "activate_learned_rule", "create_idea"
    correlation_id: str = ""
    classification: str = ""             # clasificación/ruta que produjo la acción (Ley 1)
    is_irreversible: bool = False        # escala CAUTION->BLOCK en Causa/Efecto y Correspondencia
    interaction_recorded: bool = False   # Ley 2: ¿la interacción quedó en memoria estructural?
    domain: str = ""                     # Ley 2: dominio asociado (si aplica)
    user_id: str = ""                    # Ley 2: usuario asociado (si aplica)
    action_logged: bool = False          # Ley 6: ¿ya se registró en ledger?
    is_hypothesis_context: bool = False  # Ley 4: ¿esto es una hipótesis/opinión?
    has_counter_evidence: bool = False   # Ley 4: ¿hay evidencia en contra considerada?
    is_learning_context: bool = False    # Ley 7: ¿esto integra conocimiento nuevo?
    knowledge_verified: Optional[bool] = None  # Ley 7: ¿verificado? None = desconocido
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.correlation_id:
            self.correlation_id = uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Veredicto agregado
# ---------------------------------------------------------------------------

@dataclass
class ConstitutionalVerdict:
    """Resultado completo de pasar una propuesta por los 7 principios."""
    proposal_id: str
    action: str
    results: Tuple[PrincipleResult, ...]   # exactamente 7, ordenados por número de ley
    overall: PrincipleVerdict
    mode: str                              # "shadow" | "enforce"
    timestamp: float = field(default_factory=time.time)
    filter_error: str = ""                 # no vacío si el filtro falló técnicamente

    @property
    def blocked_principles(self) -> List[PrincipleResult]:
        return [r for r in self.results if r.verdict == PrincipleVerdict.BLOCK]

    @property
    def caution_principles(self) -> List[PrincipleResult]:
        return [r for r in self.results if r.verdict == PrincipleVerdict.CAUTION]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "action": self.action,
            "results": [r.to_dict() for r in self.results],
            "overall": self.overall.value,
            "mode": self.mode,
            "timestamp": self.timestamp,
            "filter_error": self.filter_error,
        }

    def summary(self) -> str:
        parts = ", ".join(f"L{r.number}={r.verdict.value}" for r in self.results)
        return f"ConstitutionalVerdict[{self.action}] overall={self.overall.value} | {parts}"


def _aggregate(results: List[PrincipleResult]) -> PrincipleVerdict:
    """BLOCK si alguno BLOCK; si no, CAUTION si alguno CAUTION; si no, PASS."""
    worst = PrincipleVerdict.PASS
    for r in results:
        if _SEVERITY[r.verdict] > _SEVERITY[worst]:
            worst = r.verdict
    return worst


# ---------------------------------------------------------------------------
# Evaluadores — envuelven enforce_law_N existentes, contrato de 3 estados
# ---------------------------------------------------------------------------
# Regla compartida: si no se pudo determinar el estado real (evidencia
# insuficiente, dependencia caída) -> CAUTION, nunca PASS. Esto INVIERTE el
# fail-safe de law_enforcement.py (que asume "sin datos -> pasa" para no
# ensuciar el peso de PresenciaObserver con falsos positivos).

def _principle_1_mentalismo(p: ActionProposal) -> PrincipleResult:
    if not p.classification:
        return PrincipleResult(
            number=1, name="Mentalismo", verdict=PrincipleVerdict.CAUTION,
            reason="La acción no trae una clasificación/intención identificada",
            evidence_missing=("classification",),
            recommended_action="Clasificar el input (SmartRouter/evidencia) antes de continuar",
            retryable=True,
        )
    return PrincipleResult(
        number=1, name="Mentalismo", verdict=PrincipleVerdict.PASS,
        reason=f"Acción nace de una intención identificada: classification={p.classification!r}",
        evidence_available={"classification": p.classification},
    )


def _principle_2_correspondencia(p: ActionProposal) -> PrincipleResult:
    if not p.interaction_recorded:
        verdict = PrincipleVerdict.BLOCK if p.is_irreversible else PrincipleVerdict.CAUTION
        return PrincipleResult(
            number=2, name="Correspondencia", verdict=verdict,
            reason=(
                "Sin registro de que lo micro (esta interacción) se refleje en "
                "lo macro (memoria estructural / dominio / usuario)"
            ),
            evidence_available={"domain": p.domain, "user_id": bool(p.user_id)},
            evidence_missing=("interaction_recorded",),
            recommended_action="Registrar en memoria estructural antes/junto con la acción",
            retryable=not p.is_irreversible,
        )
    return PrincipleResult(
        number=2, name="Correspondencia", verdict=PrincipleVerdict.PASS,
        reason="Interacción reflejada en memoria estructural (micro-macro coherente)",
        evidence_available={"domain": p.domain, "interaction_recorded": True},
    )


def _principle_3_vibracion(p: ActionProposal) -> PrincipleResult:
    from core.operator.law_enforcement import detect_active_cycles
    try:
        active = detect_active_cycles()
    except Exception as exc:
        return PrincipleResult(
            number=3, name="Vibración", verdict=PrincipleVerdict.CAUTION,
            reason=f"No se pudo determinar si el sistema tiene ciclos activos: {exc}",
            evidence_missing=("active_cycles",),
            recommended_action="Reintentar; verificar heartbeats de worker/gateway",
            retryable=True,
        )
    if not active:
        return PrincipleResult(
            number=3, name="Vibración", verdict=PrincipleVerdict.CAUTION,
            reason="Estancamiento confirmado — el conocimiento podría estar inactivo, no presente",
            evidence_available={"active_cycles": False},
            recommended_action="Verificar heartbeats antes de tratar la evidencia como vigente",
        )
    return PrincipleResult(
        number=3, name="Vibración", verdict=PrincipleVerdict.PASS,
        reason="Sistema con ciclos activos — la evidencia se trata como vigente",
        evidence_available={"active_cycles": True},
    )


def _principle_4_polaridad(p: ActionProposal) -> PrincipleResult:
    if not p.is_hypothesis_context:
        return PrincipleResult(
            number=4, name="Polaridad", verdict=PrincipleVerdict.PASS,
            reason="No es una hipótesis/opinión — no requiere evidencia en contra",
            evidence_available={"is_hypothesis_context": False},
        )
    if not p.has_counter_evidence:
        return PrincipleResult(
            number=4, name="Polaridad", verdict=PrincipleVerdict.CAUTION,
            reason="Hipótesis sin evidencia en contra considerada — sesgo potencial",
            evidence_missing=("counter_evidence",),
            recommended_action="Buscar evidencia contradictoria antes de confirmar",
            retryable=True,
        )
    return PrincipleResult(
        number=4, name="Polaridad", verdict=PrincipleVerdict.PASS,
        reason="Hipótesis evaluada con evidencia a favor y en contra",
        evidence_available={"has_counter_evidence": True},
    )


def _principle_5_ritmo(p: ActionProposal) -> PrincipleResult:
    from core.operator.law_enforcement import (
        detect_governor_mode, detect_forcing_during_recovery,
    )
    try:
        gov_mode = detect_governor_mode()
        forcing = detect_forcing_during_recovery(gov_mode)
    except Exception as exc:
        return PrincipleResult(
            number=5, name="Ritmo", verdict=PrincipleVerdict.CAUTION,
            reason=f"No se pudo determinar el modo del Governor: {exc}",
            evidence_missing=("governor_mode",),
            recommended_action="Reintentar; Governor es solo señal de salud, no autoridad",
            retryable=True,
        )
    if forcing:
        return PrincipleResult(
            number=5, name="Ritmo", verdict=PrincipleVerdict.CAUTION,
            reason=f"Forzando operación durante modo de recuperación ({gov_mode})",
            evidence_available={"governor_mode": gov_mode},
            recommended_action="Pausar hasta que el ciclo natural lo permita",
        )
    return PrincipleResult(
        number=5, name="Ritmo", verdict=PrincipleVerdict.PASS,
        reason=f"Ritmo respetado — Governor en modo {gov_mode}",
        evidence_available={"governor_mode": gov_mode},
    )


def _principle_6_causa_efecto(p: ActionProposal) -> PrincipleResult:
    has_correlation = bool(p.correlation_id)
    if p.action_logged and has_correlation:
        return PrincipleResult(
            number=6, name="Causa y Efecto", verdict=PrincipleVerdict.PASS,
            reason="Acción con trazabilidad completa (ledger + correlation_id)",
            evidence_available={"action_logged": True, "correlation_id": p.correlation_id},
        )
    missing = []
    if not p.action_logged:
        missing.append("action_logged")
    if not has_correlation:
        missing.append("correlation_id")
    verdict = PrincipleVerdict.BLOCK if p.is_irreversible else PrincipleVerdict.CAUTION
    return PrincipleResult(
        number=6, name="Causa y Efecto", verdict=verdict,
        reason=f"Trazabilidad incompleta: {', '.join(missing)}",
        evidence_missing=tuple(missing),
        recommended_action="Registrar en ledger con correlation_id antes de continuar",
        retryable=not p.is_irreversible,
    )


def _principle_7_generacion(p: ActionProposal) -> PrincipleResult:
    if not p.is_learning_context:
        return PrincipleResult(
            number=7, name="Generación", verdict=PrincipleVerdict.PASS,
            reason="No es un intento de integrar conocimiento nuevo",
            evidence_available={"is_learning_context": False},
        )
    if p.knowledge_verified is None:
        return PrincipleResult(
            number=7, name="Generación", verdict=PrincipleVerdict.CAUTION,
            reason="Estado de verificación desconocido para conocimiento nuevo",
            evidence_missing=("knowledge_verified",),
            recommended_action="Determinar si pasó señal→investigación→verificación",
            retryable=True,
        )
    if not p.knowledge_verified:
        return PrincipleResult(
            number=7, name="Generación", verdict=PrincipleVerdict.BLOCK,
            reason="Intento de integrar conocimiento SIN verificación — requiere ciclo completo",
            evidence_available={"knowledge_verified": False},
            recommended_action="Completar señal→investigación→verificación antes de integrar",
        )
    return PrincipleResult(
        number=7, name="Generación", verdict=PrincipleVerdict.PASS,
        reason="Conocimiento verificado antes de integración",
        evidence_available={"knowledge_verified": True},
    )


_EVALUATORS = (
    _principle_1_mentalismo,
    _principle_2_correspondencia,
    _principle_3_vibracion,
    _principle_4_polaridad,
    _principle_5_ritmo,
    _principle_6_causa_efecto,
    _principle_7_generacion,
)


def _caution_all(action: str, proposal_id: str, mode: str, error: str) -> ConstitutionalVerdict:
    """Fallo técnico del filtro completo -> CAUTION de alta prioridad en los 7,
    NUNCA autorización silenciosa."""
    results = tuple(
        PrincipleResult(
            number=i + 1,
            name=("Mentalismo", "Correspondencia", "Vibración", "Polaridad",
                  "Ritmo", "Causa y Efecto", "Generación")[i],
            verdict=PrincipleVerdict.CAUTION,
            reason=f"Fallo técnico del filtro constitucional: {error}",
            evidence_missing=("filter_unavailable",),
            recommended_action="Revisar log; DecisionAuthority debe tratar como alta prioridad",
            retryable=True,
        )
        for i in range(7)
    )
    return ConstitutionalVerdict(
        proposal_id=proposal_id, action=action, results=results,
        overall=PrincipleVerdict.CAUTION, mode=mode, filter_error=error,
    )


def evaluate(proposal: ActionProposal, *, mode: str = "shadow") -> ConstitutionalVerdict:
    """
    Evalúa una propuesta de acción contra los 7 Principios Fundamentales.

    Nunca lanza. Si el filtro completo falla técnicamente, devuelve un
    veredicto CAUTION de alta prioridad en los 7 principios (nunca PASS
    silencioso). No redacta respuestas ni elige herramientas — solo emite
    el veredicto sobre la propuesta ya formada.
    """
    try:
        results: List[PrincipleResult] = []
        for fn in _EVALUATORS:
            try:
                results.append(fn(proposal))
            except Exception as exc:
                idx = _EVALUATORS.index(fn)
                name = ("Mentalismo", "Correspondencia", "Vibración", "Polaridad",
                        "Ritmo", "Causa y Efecto", "Generación")[idx]
                logger.warning("Principio %d (%s) falló técnicamente: %s", idx + 1, name, exc)
                results.append(PrincipleResult(
                    number=idx + 1, name=name, verdict=PrincipleVerdict.CAUTION,
                    reason=f"Evaluación falló técnicamente: {exc}",
                    evidence_missing=("evaluator_error",),
                    recommended_action="Reintentar evaluación",
                    retryable=True,
                ))
        overall = _aggregate(results)
        verdict = ConstitutionalVerdict(
            proposal_id=proposal.correlation_id,
            action=proposal.action,
            results=tuple(results),
            overall=overall,
            mode=mode,
        )
        logger.info("Constitutional filter: %s", verdict.summary())
        return verdict
    except Exception as exc:
        logger.error("Constitutional filter FAILED completely: %s", exc)
        return _caution_all(proposal.action, proposal.correlation_id, mode, str(exc))
