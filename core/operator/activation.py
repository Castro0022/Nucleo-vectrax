"""
Vectrax Core — Operator Activation
======================================
Runtime del ciclo cognitivo continuo del Operador Universal.
Este módulo es el punto de activación que conecta las 12 capas en
un ciclo operativo coherente.

Ciclo cognitivo:
  perceive → prioritize → update_memory → generate_hypotheses
  → evaluate_risk → request_approval → execute → log

Modo inicial: GUIDED (observa, analiza, propone — no ejecuta sin
autorización explícita del creador).

Principio P-004: "Ninguna acción crítica puede ejecutarse sin
autorización explícita del creador."

Capa: 1 — Núcleo Central (coordinación del ciclo)
Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional

from core.operator.identity import (
    IDENTITY,
    OperatorMode,
    verify_identity_integrity,
)
from core.operator.nucleus import Nucleus, LayerStatus, get_nucleus
from core.operator.perception_bridge import PerceptionBridge, PerceptionResult
from core.operator.relevance_engine import (
    RelevanceEngine,
    RelevanceLevel,
    RelevanceScore,
    get_relevance_engine,
)
from core.operator.risk_bridge import (
    OperatorDecision,
    RiskBridge,
    RiskEvaluation,
    get_risk_bridge,
)
from core.operator.approval_gate import (
    ApprovalGate,
    ApprovalRequest,
    ApprovalStatus,
    get_approval_gate,
)
from core.operator.hypothesis_engine import (
    Evidence,
    EvidenceType,
    Hypothesis,
    HypothesisEngine,
    HypothesisStatus,
    get_hypothesis_engine,
)
from core.operator.universal_bus import (
    BusEvent,
    Channels,
    EventPriority,
    UniversalBus,
    get_universal_bus,
)
from core.operator import ledger_bridge as ledger

logger = logging.getLogger("vectrax.operator.activation")

_STATE_FILE = pathlib.Path.home() / ".vectrax" / "operator_runtime_state.json"


# ---------------------------------------------------------------------------
# Dominios de operación
# ---------------------------------------------------------------------------

class OperatorDomain(str, Enum):
    """Dominios operativos del operador."""
    FILESYSTEM = "filesystem"
    CODEBASE = "codebase"
    WORKFLOWS = "workflows"
    LOGS = "logs"


class RestrictedDomain(str, Enum):
    """Dominios restringidos — requieren autorización explícita."""
    CREDENTIALS = "credentials"
    SYSTEM_ENV = "system_env"
    EXTERNAL_APIS = "external_apis_unauthorized"


AUTHORIZED_DOMAINS: FrozenSet[str] = frozenset(d.value for d in OperatorDomain)
RESTRICTED_DOMAINS: FrozenSet[str] = frozenset(d.value for d in RestrictedDomain)


# ---------------------------------------------------------------------------
# Estado de activación
# ---------------------------------------------------------------------------

class RuntimeState(str, Enum):
    """Estado del runtime del operador."""
    INACTIVE = "inactive"         # No activado
    BOOTING = "booting"           # En proceso de activación
    ACTIVE = "active"             # Ciclo operativo corriendo
    PAUSED = "paused"             # Pausado por el creador
    ERROR = "error"               # Error en activación


# ---------------------------------------------------------------------------
# Resultado de un ciclo
# ---------------------------------------------------------------------------

@dataclass
class OperatorCycleResult:
    """Resultado de un ciclo cognitivo completo del operador."""
    cycle_id: str = ""
    cycle_number: int = 0
    mode: str = OperatorMode.GUIDED.value
    state: str = RuntimeState.ACTIVE.value

    # Percepción
    signals_perceived: int = 0
    restricted_signals: int = 0

    # Relevancia
    high_relevance: int = 0
    medium_relevance: int = 0
    low_relevance: int = 0

    # Hipótesis
    hypotheses_proposed: int = 0
    hypotheses_active: int = 0

    # Gobernanza
    risk_evaluations: int = 0
    actions_allowed: int = 0
    actions_blocked: int = 0
    actions_pending: int = 0

    # Bus
    events_emitted: int = 0

    # Timing
    elapsed_ms: float = 0.0
    step_timings: Dict[str, float] = field(default_factory=dict)

    # Resultado
    outcome: str = "observed"     # observed | proposed | executed | error
    proposals: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "cycle_number": self.cycle_number,
            "mode": self.mode,
            "state": self.state,
            "perception": {
                "signals": self.signals_perceived,
                "restricted": self.restricted_signals,
            },
            "relevance": {
                "high": self.high_relevance,
                "medium": self.medium_relevance,
                "low": self.low_relevance,
            },
            "hypotheses": {
                "proposed": self.hypotheses_proposed,
                "active": self.hypotheses_active,
            },
            "governance": {
                "evaluations": self.risk_evaluations,
                "allowed": self.actions_allowed,
                "blocked": self.actions_blocked,
                "pending": self.actions_pending,
            },
            "bus_events": self.events_emitted,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "step_timings": {
                k: round(v, 2) for k, v in self.step_timings.items()
            },
            "outcome": self.outcome,
            "proposals_count": len(self.proposals),
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Operator Runtime
# ---------------------------------------------------------------------------

class OperatorRuntime:
    """
    Runtime del ciclo cognitivo continuo del Operador Universal.

    Conecta y orquesta las 12 capas en un ciclo coherente:
      1. PERCEIVE — observar el entorno via PerceptionBridge
      2. PRIORITIZE — calcular relevancia via RelevanceEngine
      3. UPDATE MEMORY — alimentar memoria gravitacional
      4. GENERATE HYPOTHESES — generar hipótesis via HypothesisEngine
      5. EVALUATE RISK — evaluar riesgo via RiskBridge
      6. REQUEST APPROVAL — solicitar aprobación via ApprovalGate
      7. EXECUTE — ejecutar acciones aprobadas (solo si aprobadas)
      8. LOG — registrar todo en ledger via UniversalBus

    En modo GUIDED: solo observa, analiza y propone.
    No ejecuta sin autorización explícita del creador.
    """

    def __init__(self) -> None:
        self._state = RuntimeState.INACTIVE
        self._mode = OperatorMode.GUIDED
        self._cycle_count: int = 0
        self._boot_time: Optional[float] = None
        self._last_cycle_time: Optional[float] = None
        self._last_cycle_id: Optional[str] = None
        self._last_cycle_outcome: Optional[str] = None
        self._last_cycle_signals: int = 0

        # Componentes (inicializados en activate())
        self._nucleus: Optional[Nucleus] = None
        self._perception: Optional[PerceptionBridge] = None
        self._relevance: Optional[RelevanceEngine] = None
        self._risk: Optional[RiskBridge] = None
        self._gate: Optional[ApprovalGate] = None
        self._hypothesis: Optional[HypothesisEngine] = None
        self._bus: Optional[UniversalBus] = None

        # Dominios
        self._authorized_domains = AUTHORIZED_DOMAINS
        self._restricted_domains = RESTRICTED_DOMAINS

        logger.info("OperatorRuntime created (inactive)")

    # -- Propiedades --------------------------------------------------------

    @property
    def state(self) -> RuntimeState:
        return self._state

    @property
    def mode(self) -> OperatorMode:
        return self._mode

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    @property
    def is_active(self) -> bool:
        return self._state == RuntimeState.ACTIVE

    # -- Activación ---------------------------------------------------------

    def activate(
        self,
        *,
        mode: OperatorMode = OperatorMode.GUIDED,
        authorized_domains: Optional[FrozenSet[str]] = None,
        restricted_domains: Optional[FrozenSet[str]] = None,
    ) -> Dict[str, Any]:
        """
        Activa el ciclo cognitivo del operador.

        1. Verifica integridad de identidad
        2. Inicializa Nucleus
        3. Conecta todas las capas activas
        4. Suscribe al bus universal
        5. Configura modo y dominios
        6. Registra activación en ledger
        """
        self._state = RuntimeState.BOOTING
        self._boot_time = time.time()
        activation_log: List[str] = []

        try:
            # 1. Verificar identidad
            if not verify_identity_integrity():
                self._state = RuntimeState.ERROR
                raise RuntimeError("Identity integrity check FAILED — aborting")
            activation_log.append("Identity verified: OK")

            # 2. Inicializar Nucleus
            self._nucleus = get_nucleus()
            activation_log.append(
                f"Nucleus online: {self._nucleus.check_integrity()['total_layers']} layers"
            )

            # 3. Conectar capas
            self._perception = PerceptionBridge()
            activation_log.append("Layer 2 — Perception: connected")

            self._relevance = get_relevance_engine()
            activation_log.append("Layer 2 — Relevance: connected")

            self._hypothesis = get_hypothesis_engine()
            activation_log.append("Layer 5 — Hypothesis: connected")

            self._risk = get_risk_bridge()
            activation_log.append("Layer 7 — Governor/Risk: connected")

            self._gate = get_approval_gate(operator_mode=mode.value)
            activation_log.append("Layer 7 — Approval Gate: connected")

            self._bus = get_universal_bus()
            activation_log.append("Layer 9 — Universal Bus: connected")

            # 4. Suscribir al bus para logging centralizado
            self._bus.subscribe(
                Channels.BROADCAST,
                self._on_bus_event,
                subscriber_name="operator_runtime",
                min_priority=EventPriority.HIGH,
            )
            activation_log.append("Bus subscriptions: active")

            # 4b. Conectar Builder Bridge (Capa 8) al bus
            try:
                from core.operator.builder.bridge import BuilderBridge
                self._builder_bridge = BuilderBridge()
                self._builder_bridge.subscribe(self._bus)
                activation_log.append("Layer 8 — Builder: bus-connected")
            except Exception as _bb_exc:
                self._builder_bridge = None
                activation_log.append(f"Layer 8 — Builder: unavailable ({_bb_exc})")
                logger.debug("BuilderBridge init failed: %s", _bb_exc)

            # 4c. Conectar PresenciaObserver al bus (solo lectura — modo OBSERVER)
            # Observa todos los motores desde el primer ciclo sin bloquear nada.
            try:
                from core.nucleus.presencia_pura import get_observer
                get_observer().observe()
                activation_log.append("PresenciaObserver: bus-connected (OBSERVER mode)")
                logger.info("PresenciaObserver wired to bus in OBSERVER mode")
            except Exception as _pp_exc:
                activation_log.append(f"PresenciaObserver: unavailable ({_pp_exc})")
                logger.debug("PresenciaObserver bus connection failed: %s", _pp_exc)

            # 5. Configurar modo y dominios
            self._mode = mode
            self._nucleus.set_mode(mode, reason="Operator activation")
            if authorized_domains is not None:
                self._authorized_domains = authorized_domains
            if restricted_domains is not None:
                self._restricted_domains = restricted_domains
            activation_log.append(f"Mode: {mode.value}")
            activation_log.append(
                f"Domains: {len(self._authorized_domains)} authorized, "
                f"{len(self._restricted_domains)} restricted"
            )

            # 6. Marcar activo
            self._state = RuntimeState.ACTIVE

            # 7. Emitir evento de activación en el bus
            self._bus.broadcast(
                "operator_activated",
                source_layer=1,
                priority=EventPriority.HIGH,
                payload={
                    "mode": mode.value,
                    "authorized_domains": sorted(self._authorized_domains),
                    "restricted_domains": sorted(self._restricted_domains),
                    "identity": IDENTITY.name,
                },
            )

            # 8. Registrar en ledger
            ledger.record_event(
                action="operator_activated",
                category=ledger.EventCategory.NUCLEUS,
                risk_zone=ledger.RiskZone.GREEN,
                reason=f"Operator activated in {mode.value} mode",
                details={
                    "mode": mode.value,
                    "authorized_domains": sorted(self._authorized_domains),
                    "restricted_domains": sorted(self._restricted_domains),
                    "layers_active": self._nucleus.check_integrity()["active"],
                    "boot_log": activation_log,
                },
            )

            activation_log.append("Activation COMPLETE")
            self._persist_state()

            logger.info(
                "Operator ACTIVATED | mode=%s | domains=%d/%d | layers=%d",
                mode.value,
                len(self._authorized_domains),
                len(self._restricted_domains),
                self._nucleus.check_integrity()["active"],
            )

            return {
                "status": "active",
                "mode": mode.value,
                "identity": IDENTITY.name,
                "creator": IDENTITY.creator,
                "authorized_domains": sorted(self._authorized_domains),
                "restricted_domains": sorted(self._restricted_domains),
                "layers_active": self._nucleus.check_integrity()["active"],
                "boot_time_ms": round((time.time() - self._boot_time) * 1000, 2),
                "log": activation_log,
            }

        except Exception as exc:
            self._state = RuntimeState.ERROR
            activation_log.append(f"ACTIVATION FAILED: {exc}")
            ledger.record_event(
                action="operator_activation_failed",
                category=ledger.EventCategory.NUCLEUS,
                risk_zone=ledger.RiskZone.RED,
                reason=str(exc),
            )
            logger.error("Operator activation FAILED: %s", exc)
            raise

    # -- Ciclo cognitivo ----------------------------------------------------

    def run_cycle(self) -> OperatorCycleResult:
        """
        Ejecuta UN ciclo cognitivo completo.

        Pipeline:
          1. perceive    — observar señales del entorno
          2. prioritize  — calcular relevancia de señales
          3. memorize    — actualizar memoria gravitacional
          4. hypothesize — generar hipótesis sobre señales relevantes
          5. evaluate    — evaluar riesgo de acciones propuestas
          6. approve     — solicitar aprobación si es necesario
          7. execute     — ejecutar acciones aprobadas (solo si approved)
          8. log         — registrar ciclo completo

        En modo GUIDED: los pasos 6-7 generan propuestas pero
        NO ejecutan sin aprobación explícita.
        """
        if not self.is_active:
            raise RuntimeError(
                f"Operator not active (state={self._state.value}). "
                "Call activate() first."
            )

        self._cycle_count += 1
        cycle_id = f"CYC-{uuid.uuid4().hex[:8].upper()}"
        t0 = time.time()
        self._nucleus.increment_cycle()

        result = OperatorCycleResult(
            cycle_id=cycle_id,
            cycle_number=self._cycle_count,
            mode=self._mode.value,
            state=self._state.value,
        )

        try:
            # ──────────────────────────────────────────────
            # STEP 1: PERCEIVE
            # ──────────────────────────────────────────────
            t_step = time.time()
            perception = self._perception.perceive_cycle(
                operator_mode=self._mode.value,
            )
            result.signals_perceived = perception.total_signals
            result.restricted_signals = perception.restricted_signals
            result.step_timings["perceive"] = (time.time() - t_step) * 1000

            self._bus.emit(
                Channels.PERCEPTION,
                "cycle_perceived",
                source_layer=2,
                payload={
                    "signals": perception.total_signals,
                    "restricted": perception.restricted_signals,
                    "top_priority": perception.top_priority,
                },
            )
            result.events_emitted += 1

            # ──────────────────────────────────────────────
            # STEP 2: PRIORITIZE (relevance scoring)
            # ──────────────────────────────────────────────
            t_step = time.time()
            relevance_scores = self._relevance.score_cognitive_signals(
                perception.signals,
            )
            high = self._relevance.filter_by_level(
                relevance_scores, RelevanceLevel.HIGH,
            )
            medium = self._relevance.filter_by_level(
                relevance_scores, RelevanceLevel.MEDIUM,
            )
            low = self._relevance.filter_by_level(
                relevance_scores, RelevanceLevel.LOW,
            )
            result.high_relevance = len(high)
            result.medium_relevance = len(medium)
            result.low_relevance = len(low)
            result.step_timings["prioritize"] = (time.time() - t_step) * 1000

            # ──────────────────────────────────────────────
            # STEP 3: UPDATE MEMORY
            # ──────────────────────────────────────────────
            t_step = time.time()
            try:
                from cognition.memory.memory_engine import MemoryEngine
                memory = MemoryEngine()
                memory.store_signals(perception.signals)
            except Exception as exc:
                logger.debug("Memory update skipped: %s", exc)
            result.step_timings["memory"] = (time.time() - t_step) * 1000

            # ──────────────────────────────────────────────
            # STEP 4: GENERATE HYPOTHESES
            # ──────────────────────────────────────────────
            t_step = time.time()
            hypotheses_proposed = 0

            # Generar hipótesis para señales de alta relevancia
            for score in high:
                hyp = self._hypothesis.propose(
                    description=(
                        f"High-relevance signal detected: {score.source_summary}"
                    ),
                    context=f"cycle={cycle_id} relevance={score.relevance:.2f}",
                    initial_confidence=score.relevance * 0.6,
                    tags=["auto", "high_relevance", f"cycle_{self._cycle_count}"],
                )
                hypotheses_proposed += 1

            result.hypotheses_proposed = hypotheses_proposed
            result.hypotheses_active = len(self._hypothesis.get_active())
            result.step_timings["hypothesize"] = (time.time() - t_step) * 1000

            if hypotheses_proposed > 0:
                self._bus.emit(
                    Channels.HYPOTHESIS,
                    "hypotheses_generated",
                    source_layer=5,
                    payload={
                        "proposed": hypotheses_proposed,
                        "active": result.hypotheses_active,
                    },
                )
                result.events_emitted += 1

            # ──────────────────────────────────────────────
            # STEP 5: EVALUATE RISK
            # ──────────────────────────────────────────────
            t_step = time.time()
            actionable = high + medium
            risk_results: List[RiskEvaluation] = []

            for score in actionable:
                risk_eval = self._risk.evaluate_action(
                    path="",
                    op_type="analysis",
                    topic=score.source_summary[:50],
                    operator_mode=self._mode.value,
                )
                risk_results.append(risk_eval)
                result.risk_evaluations += 1

                if risk_eval.decision == OperatorDecision.ALLOW:
                    result.actions_allowed += 1
                elif risk_eval.decision == OperatorDecision.BLOCK:
                    result.actions_blocked += 1
                elif risk_eval.decision in (
                    OperatorDecision.ALLOW_WITH_REVIEW,
                    OperatorDecision.ESCALATE,
                ):
                    result.actions_pending += 1

            result.step_timings["evaluate_risk"] = (time.time() - t_step) * 1000

            # ──────────────────────────────────────────────
            # STEP 6: REQUEST APPROVAL (GUIDED mode)
            # ──────────────────────────────────────────────
            t_step = time.time()
            proposals: List[Dict[str, Any]] = []

            if self._mode == OperatorMode.GUIDED:
                # En modo GUIDED: generar propuestas para señales
                # relevantes, pero NO ejecutar
                for i, score in enumerate(high[:5]):  # Max 5 propuestas/ciclo
                    req = self._gate.submit(
                        action=f"investigate_signal_{cycle_id}_{i}",
                        description=(
                            f"High-relevance signal: {score.source_summary}"
                        ),
                        op_type="analysis",
                        topic="observation",
                        metadata={
                            "cycle_id": cycle_id,
                            "relevance": score.relevance,
                            "level": score.level.value,
                        },
                    )
                    proposals.append(req.to_dict())

            result.proposals = proposals
            result.step_timings["approval"] = (time.time() - t_step) * 1000

            # ──────────────────────────────────────────────
            # STEP 7: EXECUTE (only if approved)
            # ──────────────────────────────────────────────
            t_step = time.time()
            # En modo GUIDED: no auto-ejecutar. Las acciones aprobadas
            # por el creador se ejecutan a través de approve() en el gate.
            # Aquí solo verificamos si hay aprobaciones previas pendientes
            # de ejecución.
            executed_count = 0
            # (Reservado para modos SUPERVISED/AUTONOMOUS)
            result.step_timings["execute"] = (time.time() - t_step) * 1000

            # ──────────────────────────────────────────────
            # STEP 8: LOG
            # ──────────────────────────────────────────────
            t_step = time.time()

            # Determinar outcome
            if proposals:
                result.outcome = "proposed"
            elif result.signals_perceived > 0:
                result.outcome = "observed"
            else:
                result.outcome = "idle"

            # Registrar ciclo en ledger
            ledger.record_event(
                action=f"cycle_complete:{cycle_id}",
                category=ledger.EventCategory.NUCLEUS,
                risk_zone=ledger.RiskZone.GREEN,
                reason=f"Cycle #{self._cycle_count}: {result.outcome}",
                details={
                    "cycle_id": cycle_id,
                    "cycle_number": self._cycle_count,
                    "signals": result.signals_perceived,
                    "high_relevance": result.high_relevance,
                    "hypotheses": result.hypotheses_proposed,
                    "proposals": len(proposals),
                    "outcome": result.outcome,
                },
            )

            # Emitir evento de ciclo completo en bus
            self._bus.emit(
                Channels.NUCLEUS,
                "cycle_complete",
                source_layer=1,
                payload={"cycle_id": cycle_id, "outcome": result.outcome},
            )
            result.events_emitted += 1
            result.step_timings["log"] = (time.time() - t_step) * 1000

        except Exception as exc:
            result.outcome = "error"
            result.errors.append(str(exc)[:200])
            logger.error("Cycle %s error: %s", cycle_id, exc)

            ledger.record_event(
                action=f"cycle_error:{cycle_id}",
                category=ledger.EventCategory.NUCLEUS,
                risk_zone=ledger.RiskZone.YELLOW,
                reason=str(exc)[:200],
            )

        result.elapsed_ms = (time.time() - t0) * 1000
        self._last_cycle_time = time.time()
        self._last_cycle_id = cycle_id
        self._last_cycle_outcome = result.outcome
        self._last_cycle_signals = result.signals_perceived
        self._persist_state()

        logger.info(
            "Cycle %s (#%d): %s | %d signals, %d high, %d hyp, %d proposals | %.1fms",
            cycle_id, self._cycle_count, result.outcome,
            result.signals_perceived, result.high_relevance,
            result.hypotheses_proposed, len(result.proposals),
            result.elapsed_ms,
        )

        return result

    # -- Control del operador -----------------------------------------------

    def pause(self, *, reason: str = "") -> None:
        """Pausa el ciclo operativo."""
        self._state = RuntimeState.PAUSED
        ledger.record_event(
            action="operator_paused",
            category=ledger.EventCategory.NUCLEUS,
            risk_zone=ledger.RiskZone.YELLOW,
            reason=reason,
        )
        self._bus.broadcast(
            "operator_paused", source_layer=1,
            priority=EventPriority.HIGH,
            payload={"reason": reason},
        )
        logger.info("Operator PAUSED: %s", reason)

    def resume(self, *, reason: str = "") -> None:
        """Reanuda el ciclo operativo."""
        if self._state != RuntimeState.PAUSED:
            raise RuntimeError(f"Cannot resume from state {self._state.value}")
        self._state = RuntimeState.ACTIVE
        ledger.record_event(
            action="operator_resumed",
            category=ledger.EventCategory.NUCLEUS,
            risk_zone=ledger.RiskZone.GREEN,
            reason=reason,
        )
        self._bus.broadcast(
            "operator_resumed", source_layer=1,
            priority=EventPriority.HIGH,
        )
        logger.info("Operator RESUMED: %s", reason)

    def set_mode(self, mode: OperatorMode, *, reason: str = "") -> None:
        """Cambia el modo operativo (requiere registro en ledger)."""
        old = self._mode
        self._mode = mode
        if self._nucleus:
            self._nucleus.set_mode(mode, reason=reason)
        if self._gate:
            self._gate.operator_mode = mode.value

        ledger.record_mode_change(old.value, mode.value, reason)
        self._bus.broadcast(
            "mode_changed", source_layer=1,
            priority=EventPriority.HIGH,
            payload={"old": old.value, "new": mode.value, "reason": reason},
        )
        logger.info("Mode changed: %s → %s | %s", old.value, mode.value, reason)

    # -- Verificación de dominio --------------------------------------------

    def is_domain_authorized(self, domain: str) -> bool:
        """Verifica si un dominio está autorizado."""
        if domain in self._restricted_domains:
            return False
        return domain in self._authorized_domains

    # -- State persistence --------------------------------------------------

    def _persist_state(self) -> None:
        """Write current runtime state to disk for cross-process consumption."""
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "state": self._state.value,
                "mode": self._mode.value,
                "cycles_completed": self._cycle_count,
                "boot_time": self._boot_time,
                "last_cycle_time": self._last_cycle_time,
                "last_cycle_id": self._last_cycle_id,
                "last_cycle_outcome": self._last_cycle_outcome,
                "last_cycle_signals": self._last_cycle_signals,
                "pid": os.getpid(),
                "written_at": time.time(),
            }
            tmp = _STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload))
            tmp.replace(_STATE_FILE)
        except Exception as exc:
            logger.debug("Failed to persist runtime state: %s", exc)

    # -- Estado / Introspección ---------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Estado completo del runtime."""
        integrity = self._nucleus.check_integrity() if self._nucleus else {}
        return {
            "identity": IDENTITY.name,
            "creator": IDENTITY.creator,
            "state": self._state.value,
            "mode": self._mode.value,
            "cycles_completed": self._cycle_count,
            "boot_time": self._boot_time,
            "last_cycle_time": self._last_cycle_time,
            "authorized_domains": sorted(self._authorized_domains),
            "restricted_domains": sorted(self._restricted_domains),
            "integrity": integrity,
            "components": {
                "nucleus": self._nucleus is not None,
                "perception": self._perception is not None,
                "relevance": self._relevance is not None,
                "hypothesis": self._hypothesis is not None,
                "risk": self._risk is not None,
                "gate": self._gate is not None,
                "bus": self._bus is not None,
            },
            "bus_stats": self._bus.get_stats() if self._bus else {},
            "hypothesis_stats": self._hypothesis.stats() if self._hypothesis else {},
            "gate_status": self._gate.status() if self._gate else {},
        }

    # -- Bus event handler --------------------------------------------------

    def _on_bus_event(self, event: BusEvent) -> None:
        """Handler para eventos HIGH/CRITICAL del bus."""
        logger.debug(
            "Bus event received: %s [%s] on %s",
            event.event_type, event.priority.value, event.channel,
        )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_runtime: Optional[OperatorRuntime] = None


def get_runtime() -> OperatorRuntime:
    """Obtener la instancia singleton del runtime."""
    global _runtime
    if _runtime is None:
        _runtime = OperatorRuntime()
    return _runtime


def activate_operator(
    *,
    mode: OperatorMode = OperatorMode.GUIDED,
    authorized_domains: Optional[FrozenSet[str]] = None,
    restricted_domains: Optional[FrozenSet[str]] = None,
) -> Dict[str, Any]:
    """
    Función de conveniencia para activar el operador.

    Usage::

        from core.operator.activation import activate_operator
        result = activate_operator()
        print(result)
    """
    runtime = get_runtime()
    return runtime.activate(
        mode=mode,
        authorized_domains=authorized_domains,
        restricted_domains=restricted_domains,
    )


def reset_runtime() -> None:
    """Reset para testing. NO usar en producción."""
    global _runtime
    _runtime = None


def get_operator_status() -> Dict[str, Any]:
    """
    Canonical status snapshot of the operator runtime.

    This is the SINGLE source of truth consumed by:
      - vx watch
      - any monitoring or introspection tool

    Never activates, never mutates state.
    """
    from datetime import datetime, timezone

    data: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "identity": {},
        "runtime": {},
        "integrity": {},
        "layers": [],
        "domains": {},
        "ledger": {},
        "hypotheses": {},
        "risk": {},
        "approvals": {},
        "bus": {},
    }

    # Identity
    try:
        from core.operator.identity import IDENTITY, verify_identity_integrity
        data["identity"] = {
            "name": IDENTITY.name,
            "creator": IDENTITY.creator,
            "nature": IDENTITY.nature,
            "mode": IDENTITY.initial_mode,
            "version": IDENTITY.version,
            "integrity": verify_identity_integrity(),
        }
    except Exception as exc:
        data["identity"] = {"error": str(exc)}

    # Runtime (use existing singleton — never creates a new one)
    try:
        runtime = get_runtime()
        data["runtime"] = {
            "state": runtime.state.value,
            "mode": runtime.mode.value,
            "is_active": runtime.is_active,
            "cycles_completed": runtime.cycle_count,
            "boot_time": runtime._boot_time,
            "last_cycle_time": runtime._last_cycle_time,
            "last_cycle_id": runtime._last_cycle_id,
            "last_cycle_outcome": runtime._last_cycle_outcome,
            "last_cycle_signals": runtime._last_cycle_signals,
        }
        # Cross-process fallback: if this process has no active runtime,
        # read state persisted by the vx run process.
        if not runtime.is_active and _STATE_FILE.exists():
            try:
                persisted = json.loads(_STATE_FILE.read_text())
                if persisted.get("state") == "active":
                    data["runtime"].update({
                        "state": persisted["state"],
                        "mode": persisted.get("mode", "guided"),
                        "is_active": True,
                        "cycles_completed": persisted.get("cycles_completed", 0),
                        "boot_time": persisted.get("boot_time"),
                        "last_cycle_time": persisted.get("last_cycle_time"),
                        "last_cycle_id": persisted.get("last_cycle_id"),
                        "last_cycle_outcome": persisted.get("last_cycle_outcome"),
                        "last_cycle_signals": persisted.get("last_cycle_signals", 0),
                        "pid": persisted.get("pid"),
                        "state_source": "persisted",
                    })
            except Exception as pexc:
                logger.debug("Could not read persisted runtime state: %s", pexc)
    except Exception as exc:
        data["runtime"] = {"state": "not_initialized", "error": str(exc)}

    # Nucleus + Integrity + Layers
    try:
        from core.operator.nucleus import get_nucleus
        nucleus = get_nucleus()
        integrity = nucleus.check_integrity()
        data["integrity"] = integrity
        data["layers"] = [la.to_dict() for la in nucleus.get_all_layers()]
    except Exception as exc:
        data["integrity"] = {"error": str(exc)}

    # Domains (static constants)
    data["domains"] = {
        "authorized": sorted(AUTHORIZED_DOMAINS),
        "restricted": sorted(RESTRICTED_DOMAINS),
    }

    # Ledger
    try:
        stats = ledger.get_ledger_stats()
        events = ledger.get_operator_events(limit=8)
        data["ledger"] = {
            "total_entries": stats.get("total_entries", 0),
            "operator_events": stats.get("recent_operator_events", 0),
            "recent": [
                {
                    "id": e.get("id", "?"),
                    "action": e.get("action", "?"),
                    "actor": e.get("actor", "?"),
                    "timestamp": e.get("timestamp", "?"),
                }
                for e in events
            ],
        }
    except Exception as exc:
        data["ledger"] = {"error": str(exc)}

    # Hypotheses
    try:
        from core.operator.hypothesis_engine import get_hypothesis_engine
        hyp_engine = get_hypothesis_engine()
        hyp_stats = hyp_engine.stats()
        active_hyps = hyp_engine.get_active()
        data["hypotheses"] = {
            "total": hyp_stats.get("total", 0),
            "active": hyp_stats.get("active", 0),
            "resolved": hyp_stats.get("resolved", 0),
            "avg_confidence": hyp_stats.get("avg_active_confidence", 0),
            "by_status": hyp_stats.get("by_status", {}),
            "active_list": [
                {
                    "id": h.id,
                    "description": h.description[:80],
                    "confidence": round(h.confidence, 2),
                    "status": h.status.value,
                    "evidence": len(h.evidence),
                }
                for h in active_hyps[:5]
            ],
        }
    except Exception as exc:
        data["hypotheses"] = {"error": str(exc)}

    # Risk / Governor
    try:
        from core.operator.risk_bridge import get_risk_bridge
        rb = get_risk_bridge()
        gov = rb.get_governor_status()
        data["risk"] = {
            "governor_mode": gov.get("mode", "unknown"),
            "autopatch_allowed": gov.get("autopatch_allowed", False),
        }
    except Exception as exc:
        data["risk"] = {"error": str(exc)}

    # Approvals
    try:
        from core.operator.approval_gate import get_approval_gate
        gate = get_approval_gate()
        pending = gate.get_pending()
        data["approvals"] = {
            "pending_count": gate.pending_count(),
            "total_history": len(gate.get_history(limit=100)),
            "pending": [
                {
                    "id": r.id,
                    "action": r.action,
                    "status": r.status.value,
                    "risk": round(r.risk_evaluation.risk_score, 2) if r.risk_evaluation else 0,
                }
                for r in pending[:5]
            ],
        }
    except Exception as exc:
        data["approvals"] = {"error": str(exc)}

    # Bus
    try:
        from core.operator.universal_bus import get_universal_bus
        bus = get_universal_bus()
        bus_stats = bus.get_stats()
        data["bus"] = {
            "total_published": bus_stats.get("total_published", 0),
            "total_delivered": bus_stats.get("total_delivered", 0),
            "channels_active": bus_stats.get("channels_active", 0),
            "subscriptions": bus_stats.get("subscriptions", 0),
            "history_size": bus_stats.get("history_size", 0),
        }
    except Exception as exc:
        data["bus"] = {"error": str(exc)}

    return data


def run_operator_forever(
    interval: float = 5.0,
    on_cycle: Optional[Any] = None,
) -> None:
    """
    Sovereign run loop for the cognitive operator.

    Activates the operator if not already active, then executes
    continuous cognitive cycles in GUIDED mode until interrupted.

    Lifecycle:
      1. Activate (if inactive)
      2. Loop: run_cycle() → sleep(interval) → repeat
      3. On SIGINT/SIGTERM: pause operator, log shutdown to ledger

    Args:
        interval: Seconds between cycles (default 5).
        on_cycle: Optional callable(OperatorCycleResult) called after
                  each cycle — used by the CLI for display.
    """
    import signal
    import threading

    runtime = get_runtime()

    if not runtime.is_active:
        activate_operator(mode=OperatorMode.GUIDED)

    stop_event = threading.Event()

    def _handle_stop(sig: int, frame: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    logger.info(
        "run_operator_forever: starting | interval=%.1fs | cycles_so_far=%d",
        interval, runtime.cycle_count,
    )

    try:
        while not stop_event.is_set():
            cycle_result = runtime.run_cycle()
            if on_cycle is not None:
                try:
                    on_cycle(cycle_result)
                except Exception as cb_exc:
                    logger.warning("on_cycle callback error: %s", cb_exc)
            stop_event.wait(timeout=interval)
    finally:
        if runtime.is_active:
            runtime.pause(reason="run_operator_forever stopped")
        ledger.record_event(
            action="run_operator_forever_stopped",
            category=ledger.EventCategory.NUCLEUS,
            risk_zone=ledger.RiskZone.GREEN,
            reason=f"Stopped after {runtime.cycle_count} cycles",
        )
        logger.info(
            "run_operator_forever: stopped after %d cycles",
            runtime.cycle_count,
        )
