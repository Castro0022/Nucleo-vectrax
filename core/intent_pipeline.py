"""
Vectrax Intent Pipeline — Pipeline de Ejecución por Intención
================================================================
Orquesta el flujo completo desde texto del usuario hasta ejecución:

  texto → IntentEngine → SmartRouter → ExecutionPlanner → AutoBuilder → Memoria

Etapas:
  1. Parse:    IntentEngine.parse(text)           → IntentPackage
  2. Route:    SmartRouter.route(text)             → SmartRoute
  3. Policy:   Evaluar SmartRoute.policy_action    → continuar / propuesta / bloquear
  4. Plan:     ExecutionPlanner.plan(intent)       → ExecutionPlan
  5. Build:    AutoBuilder.build(plan, dry_run)    → BuildResult
  6. Memory:   CausalLedger + AuditLedger + SmartRouter.record_outcome

Si la política es REQUIERE_REVISION → genera CognitiveProposal y se detiene.
Si la política es BLOCKED → aborta inmediatamente.

dry_run=True por defecto (modo supervisado).
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from core.intent_engine import IntentEngine, IntentPackage, ActionIntent
from core.smart_router import SmartRouter, SmartRoute, PolicyAction
from core.execution_planner import ExecutionPlanner, ExecutionPlan
from core.auto_builder import AutoBuilder, BuildResult

logger = logging.getLogger("vectrax.intent_pipeline")


# ---------------------------------------------------------------------------
# Pipeline Status
# ---------------------------------------------------------------------------

class PipelineStatus(str, Enum):
    """Estado final del pipeline."""
    SUCCESS          = "success"            # todo OK
    DRY_RUN_OK       = "dry_run_ok"         # dry_run completado sin errores
    PROPOSAL_CREATED = "proposal_created"   # detenido → CognitiveProposal creada
    BLOCKED          = "blocked"            # política bloqueó la ejecución
    PLAN_FAILED      = "plan_failed"        # falló la planificación
    BUILD_FAILED     = "build_failed"       # falló el build
    ERROR            = "error"              # error inesperado


# ---------------------------------------------------------------------------
# IntentResult
# ---------------------------------------------------------------------------

@dataclass
class IntentResult:
    """Resultado completo de ejecutar el pipeline de intención."""
    pipeline_id: str = field(default_factory=lambda: f"pip-{uuid.uuid4().hex[:8]}")
    status: PipelineStatus = PipelineStatus.ERROR
    intent_package: Optional[IntentPackage] = None
    smart_route: Optional[SmartRoute] = None
    execution_plan: Optional[ExecutionPlan] = None
    build_result: Optional[BuildResult] = None
    proposal: Optional[Any] = None          # CognitiveProposal si aplica
    dry_run: bool = True
    error: str = ""
    stages_completed: List[str] = field(default_factory=list)
    pipeline_time_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "status": self.status.value,
            "intent": self.intent_package.to_dict() if self.intent_package else None,
            "route": self.smart_route.to_dict() if self.smart_route else None,
            "plan_summary": self.execution_plan.summary() if self.execution_plan else None,
            "build_summary": self.build_result.summary() if self.build_result else None,
            "proposal_id": self.proposal.id if self.proposal else None,
            "dry_run": self.dry_run,
            "stages_completed": self.stages_completed,
            "pipeline_time_ms": round(self.pipeline_time_ms, 2),
            "error": self.error[:300],
        }

    def summary(self) -> str:
        mode = "DRY-RUN" if self.dry_run else "LIVE"
        action = self.intent_package.action.value if self.intent_package else "?"
        stages = len(self.stages_completed)
        return (
            f"[{mode}] {self.status.value} — action={action}, "
            f"stages={stages}/6, {self.pipeline_time_ms:.0f}ms"
        )


# ---------------------------------------------------------------------------
# Intent Pipeline
# ---------------------------------------------------------------------------

class IntentPipeline:
    """
    Orquestador del flujo completo de ejecución por intención.

    Usage::

        pipeline = IntentPipeline()
        result = pipeline.execute("crea un módulo de auth con JWT")
        print(result.summary())

        # Modo live (requiere aprobación previa):
        result = pipeline.execute("crea un módulo de auth", dry_run=False)
    """

    def __init__(
        self,
        intent_engine: Optional[IntentEngine] = None,
        smart_router: Optional[SmartRouter] = None,
        planner: Optional[ExecutionPlanner] = None,
        builder: Optional[AutoBuilder] = None,
    ) -> None:
        self._intent_engine = intent_engine or IntentEngine()
        self._smart_router = smart_router or SmartRouter()
        self._planner = planner or ExecutionPlanner()
        self._builder = builder or AutoBuilder()

    # -- Execute principal --------------------------------------------------

    def execute(
        self,
        text: str,
        channel: str = "creator",
        owner: str = "",
        dry_run: bool = True,
    ) -> IntentResult:
        """
        Ejecuta el pipeline completo de intención.

        Args:
            text: Mensaje del usuario en lenguaje natural.
            channel: Canal de entrada.
            owner: Identidad del propietario.
            dry_run: Si True (default), solo planifica sin ejecutar.

        Returns:
            IntentResult con el estado completo del pipeline.
        """
        t0 = time.time()
        result = IntentResult(dry_run=dry_run)

        try:
            # ── Stage 1: Parse Intent ─────────────────────────
            intent_pkg = self._intent_engine.parse(text, channel=channel, owner=owner)
            result.intent_package = intent_pkg
            result.stages_completed.append("intent_parse")
            logger.info("Pipeline[%s] intent: %s", result.pipeline_id, intent_pkg.summary())

            # ── Stage 2: Smart Route ──────────────────────────
            route = self._smart_router.route(text, channel=channel, owner=owner)
            result.smart_route = route
            result.stages_completed.append("smart_route")
            logger.info("Pipeline[%s] route: %s", result.pipeline_id, route.summary())

            # ── Stage 3: Policy Check ─────────────────────────
            policy = route.policy_action

            if policy == PolicyAction.BLOCKED:
                result.status = PipelineStatus.BLOCKED
                result.error = f"Bloqueado por política: {route.reason}"
                result.stages_completed.append("policy_blocked")
                self._record_memory(result, success=False)
                result.pipeline_time_ms = (time.time() - t0) * 1000
                return result

            if policy == PolicyAction.REQUIERE_REVISION and not dry_run:
                # Generar CognitiveProposal y detener
                proposal = self._create_proposal(intent_pkg, route)
                result.proposal = proposal
                result.status = PipelineStatus.PROPOSAL_CREATED
                result.stages_completed.append("policy_proposal")
                self._record_memory(result, success=True)
                result.pipeline_time_ms = (time.time() - t0) * 1000
                logger.info(
                    "Pipeline[%s] detenido → propuesta %s",
                    result.pipeline_id, proposal.id,
                )
                return result

            result.stages_completed.append("policy_ok")

            # ── Stage 4: Execution Plan ───────────────────────
            plan = self._planner.plan(intent_pkg)
            result.execution_plan = plan
            result.stages_completed.append("execution_plan")
            logger.info("Pipeline[%s] plan: %s", result.pipeline_id, plan.summary())

            if not plan.steps:
                result.status = PipelineStatus.PLAN_FAILED
                result.error = "Planner generó un plan vacío"
                self._record_memory(result, success=False)
                result.pipeline_time_ms = (time.time() - t0) * 1000
                return result

            # ── Stage 5: Build ────────────────────────────────
            build = self._builder.build(plan, dry_run=dry_run)
            result.build_result = build
            result.stages_completed.append("build")
            logger.info("Pipeline[%s] build: %s", result.pipeline_id, build.summary())

            if not build.success:
                result.status = PipelineStatus.BUILD_FAILED
                result.error = build.error
                self._record_memory(result, success=False)
                result.pipeline_time_ms = (time.time() - t0) * 1000
                return result

            # ── Stage 6: Memory ───────────────────────────────
            self._record_memory(result, success=True)
            result.stages_completed.append("memory")

            result.status = (
                PipelineStatus.DRY_RUN_OK if dry_run else PipelineStatus.SUCCESS
            )

        except Exception as exc:
            result.status = PipelineStatus.ERROR
            result.error = str(exc)
            logger.exception("Pipeline[%s] error: %s", result.pipeline_id, exc)

        result.pipeline_time_ms = (time.time() - t0) * 1000
        logger.info("Pipeline[%s] done: %s", result.pipeline_id, result.summary())
        return result

    # -- Crear CognitiveProposal --------------------------------------------

    def _create_proposal(
        self,
        intent: IntentPackage,
        route: SmartRoute,
    ) -> Any:
        """Genera una CognitiveProposal cuando la política requiere revisión."""
        try:
            from cognition.types import CognitiveProposal, ProposalStatus

            proposal = CognitiveProposal(
                status=ProposalStatus.PENDING,
                description=(
                    f"Pipeline detenido por política: acción={intent.action.value}, "
                    f"target={intent.target}, riesgo={route.risk_level.value}"
                ),
                context_id=f"pipeline-{intent.action.value}-{intent.target[:30]}",
                actions=[{
                    "type": "intent_execution",
                    "action": intent.action.value,
                    "target": intent.target,
                    "scope": intent.scope.value,
                    "strategy": route.strategy.value,
                }],
            )
            return proposal
        except ImportError:
            # Si cognition.types no está disponible, devolver dict
            return {
                "type": "cognitive_proposal",
                "status": "pending",
                "action": intent.action.value,
                "target": intent.target,
                "reason": "policy_revision_required",
            }

    # -- Registro en memoria ------------------------------------------------

    def _record_memory(self, result: IntentResult, success: bool) -> None:
        """
        Registra el resultado en los ledgers y métricas.

        Almacena solo métricas abstractas:
        - Categoría de acción (no contenido)
        - Estado del pipeline
        - Éxito/fallo
        - Latencia
        """
        action_category = (
            result.intent_package.action.value
            if result.intent_package else "unknown"
        )
        plan_id = (
            result.execution_plan.plan_id
            if result.execution_plan else result.pipeline_id
        )

        # ── CausalLedger (JSONL append-only) ──
        try:
            from core.causal_ledger import append as causal_append, CausalEventType

            event_type = (
                CausalEventType.ACTION_EXECUTED if success
                else CausalEventType.ACTION_FAILED
            )
            causal_append(
                event_type=event_type,
                plan_id=plan_id,
                action_type=action_category,
                routing_decision=(
                    result.smart_route.strategy.value
                    if result.smart_route else ""
                ),
                risk_score=(
                    result.execution_plan.risk.score
                    if result.execution_plan else 0.0
                ),
                details={
                    "pipeline_id": result.pipeline_id,
                    "status": result.status.value,
                    "stages": len(result.stages_completed),
                    "dry_run": result.dry_run,
                },
            )
        except Exception as exc:
            logger.debug("CausalLedger write failed: %s", exc)

        # ── AuditLedger (SQLite) ──
        try:
            from core.audit_ledger import record as audit_record

            audit_record(
                action=f"intent_pipeline:{action_category}",
                decision="approved" if success else "failed",
                reason=result.status.value,
                metadata={
                    "pipeline_id": result.pipeline_id,
                    "stages": result.stages_completed,
                    "dry_run": result.dry_run,
                },
            )
        except Exception as exc:
            logger.debug("AuditLedger write failed: %s", exc)

        # ── SmartRouter metrics ──
        if result.smart_route:
            try:
                self._smart_router.record_outcome(
                    route=result.smart_route,
                    success=success,
                    latency_ms=result.pipeline_time_ms,
                )
            except Exception as exc:
                logger.debug("SmartRouter metric failed: %s", exc)

    # -- Repr ---------------------------------------------------------------

    def __repr__(self) -> str:
        return "IntentPipeline()"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_pipeline: Optional[IntentPipeline] = None


def get_intent_pipeline() -> IntentPipeline:
    """Return the global IntentPipeline singleton."""
    global _pipeline
    if _pipeline is None:
        _pipeline = IntentPipeline()
    return _pipeline
