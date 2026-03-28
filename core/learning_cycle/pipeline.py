"""
Vectrax Learning Pipeline
============================
Orquesta el ciclo completo de aprendizaje continuo:
  SEÑAL → INVESTIGACIÓN → VERIFICACIÓN → INTEGRACIÓN

Ejecutable como ciclo único o continuo con intervalo configurable.
Respeta Governor policy (pausa en modo recover).
Registra métricas de cada fase para observabilidad.

Creado: 2026-03-28
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from core.learning_cycle.anomaly_detector import (
    AnomalyDetector,
    AnomalySignal,
    InputEvent,
    get_anomaly_detector,
)
from core.learning_cycle.investigation_engine import (
    InvestigationEngine,
    InvestigationResult,
    get_investigation_engine,
)
from core.learning_cycle.verification_engine import (
    VerificationEngine,
    VerificationResult,
    get_verification_engine,
)
from core.learning_cycle.learning_integrator import (
    LearningIntegrator,
    IntegrationResult,
    get_learning_integrator,
)

logger = logging.getLogger("vectrax.learning_cycle.pipeline")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PipelineCycleResult:
    """Resultado completo de un ciclo del pipeline."""
    cycle_number: int = 0
    skipped: bool = False
    skip_reason: str = ""

    # Phase results
    anomalies_detected: int = 0
    investigations_completed: int = 0
    hypotheses_generated: int = 0
    verifications_completed: int = 0
    hypotheses_confirmed: int = 0
    hypotheses_refuted: int = 0
    rules_integrated: int = 0

    # Timing
    elapsed_ms: float = 0.0
    phase_times: Dict[str, float] = field(default_factory=dict)

    # Details
    anomaly_signals: List[Dict[str, Any]] = field(default_factory=list)
    integration_results: List[Dict[str, Any]] = field(default_factory=list)

    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Learning Pipeline
# ---------------------------------------------------------------------------

class LearningPipeline:
    """
    Pipeline principal del ciclo de aprendizaje continuo.

    Orquesta los 4 módulos en secuencia:
      1. AnomalyDetector.analyze() → señales
      2. InvestigationEngine.investigate() → hipótesis
      3. VerificationEngine.verify() → confirmación/rechazo
      4. LearningIntegrator.integrate() → reglas

    Usage::

        pipeline = LearningPipeline()

        # Ciclo único con evento
        result = pipeline.process_event(InputEvent(
            text="mensaje", intent="ASK_MEMORY", source="telegram",
        ))

        # Ciclo único con anomalías ya detectadas
        result = pipeline.run_cycle(anomalies=[signal1, signal2])

        # Status
        status = pipeline.status()
    """

    def __init__(
        self,
        detector: Optional[AnomalyDetector] = None,
        investigator: Optional[InvestigationEngine] = None,
        verifier: Optional[VerificationEngine] = None,
        integrator: Optional[LearningIntegrator] = None,
    ) -> None:
        self._detector = detector or get_anomaly_detector()
        self._investigator = investigator or get_investigation_engine()
        self._verifier = verifier or get_verification_engine()
        self._integrator = integrator or get_learning_integrator()
        self._cycle_count: int = 0
        self._total_anomalies: int = 0
        self._total_rules: int = 0
        logger.info("LearningPipeline initialized")

    # =====================================================================
    # Main API
    # =====================================================================

    def process_event(self, event: InputEvent) -> PipelineCycleResult:
        """
        Procesar un evento completo a través del pipeline.

        Fase 1: Detectar anomalías en el evento
        Si hay anomalías → ejecutar ciclo completo
        Si no hay → retornar resultado vacío
        """
        # --- Governor check ---
        gov_check = self._check_governor()
        if gov_check:
            return gov_check

        # --- Phase 1: Detect ---
        t_detect = time.time()
        anomalies = self._detector.analyze(event)
        detect_time = (time.time() - t_detect) * 1000

        if not anomalies:
            return PipelineCycleResult(
                cycle_number=self._cycle_count,
                phase_times={"detect_ms": round(detect_time, 2)},
            )

        # --- Execute full cycle ---
        result = self._execute_cycle(anomalies)
        result.phase_times["detect_ms"] = round(detect_time, 2)
        return result

    def process_batch(self, events: List[InputEvent]) -> PipelineCycleResult:
        """Procesar un lote de eventos. Acumula anomalías y ejecuta un ciclo."""
        gov_check = self._check_governor()
        if gov_check:
            return gov_check

        t_detect = time.time()
        all_anomalies: List[AnomalySignal] = []
        for event in events:
            anomalies = self._detector.analyze(event)
            all_anomalies.extend(anomalies)
        detect_time = (time.time() - t_detect) * 1000

        if not all_anomalies:
            return PipelineCycleResult(
                cycle_number=self._cycle_count,
                phase_times={"detect_ms": round(detect_time, 2)},
            )

        result = self._execute_cycle(all_anomalies)
        result.phase_times["detect_ms"] = round(detect_time, 2)
        return result

    def run_cycle(
        self, anomalies: Optional[List[AnomalySignal]] = None,
    ) -> PipelineCycleResult:
        """
        Ejecutar un ciclo del pipeline con anomalías ya detectadas.
        Si no se proveen anomalías, retorna resultado vacío.
        """
        gov_check = self._check_governor()
        if gov_check:
            return gov_check

        if not anomalies:
            return PipelineCycleResult(cycle_number=self._cycle_count)

        return self._execute_cycle(anomalies)

    # =====================================================================
    # Internal cycle execution
    # =====================================================================

    def _execute_cycle(
        self, anomalies: List[AnomalySignal],
    ) -> PipelineCycleResult:
        """Ejecuta el ciclo completo para las anomalías dadas."""
        t0 = time.time()
        self._cycle_count += 1

        result = PipelineCycleResult(
            cycle_number=self._cycle_count,
            anomalies_detected=len(anomalies),
            anomaly_signals=[a.to_dict() for a in anomalies[:10]],
        )
        self._total_anomalies += len(anomalies)

        # --- Phase 2: Investigate ---
        t_inv = time.time()
        all_hypothesis_ids: List[str] = []

        for anomaly in anomalies:
            inv_result = self._investigator.investigate(anomaly)
            result.investigations_completed += 1
            all_hypothesis_ids.extend(inv_result.hypothesis_ids)

        result.hypotheses_generated = len(all_hypothesis_ids)
        result.phase_times["investigate_ms"] = round(
            (time.time() - t_inv) * 1000, 2,
        )

        if not all_hypothesis_ids:
            result.elapsed_ms = round((time.time() - t0) * 1000, 2)
            self._record_cycle(result)
            return result

        # --- Phase 3: Verify ---
        t_ver = time.time()
        verification_results = self._verifier.verify_batch(all_hypothesis_ids)
        result.verifications_completed = len(verification_results)

        confirmed_ids: List[str] = []
        for vr in verification_results:
            if vr.final_status == "confirmed":
                result.hypotheses_confirmed += 1
                confirmed_ids.append(vr.hypothesis_id)
            elif vr.final_status == "refuted":
                result.hypotheses_refuted += 1

        result.phase_times["verify_ms"] = round(
            (time.time() - t_ver) * 1000, 2,
        )

        if not confirmed_ids:
            result.elapsed_ms = round((time.time() - t0) * 1000, 2)
            self._record_cycle(result)
            return result

        # --- Phase 4: Integrate ---
        t_int = time.time()
        integration_results = self._integrator.integrate_batch(confirmed_ids)

        for ir in integration_results:
            if ir.integrated:
                result.rules_integrated += 1
                self._total_rules += 1
            result.integration_results.append(ir.to_dict())

        result.phase_times["integrate_ms"] = round(
            (time.time() - t_int) * 1000, 2,
        )

        result.elapsed_ms = round((time.time() - t0) * 1000, 2)
        self._record_cycle(result)

        logger.info(
            "Pipeline cycle #%d: anomalies=%d hypotheses=%d "
            "confirmed=%d rules=%d (%.1fms)",
            self._cycle_count, len(anomalies), len(all_hypothesis_ids),
            result.hypotheses_confirmed, result.rules_integrated,
            result.elapsed_ms,
        )

        return result

    # =====================================================================
    # Governor check
    # =====================================================================

    def _check_governor(self) -> Optional[PipelineCycleResult]:
        """Verifica Governor policy. Retorna resultado si debe pausar."""
        try:
            from core.governor import get_current_policy
            policy = get_current_policy()
            if policy.get("mode") == "recover":
                return PipelineCycleResult(
                    cycle_number=self._cycle_count,
                    skipped=True,
                    skip_reason="Governor en modo RECOVER — pipeline pausado",
                )
        except Exception:
            pass  # Si no hay Governor, continuar normalmente
        return None

    # =====================================================================
    # Ledger recording
    # =====================================================================

    def _record_cycle(self, result: PipelineCycleResult) -> None:
        """Registra el ciclo en el EpisodicLedger."""
        try:
            from core.learn.episodic import get_ledger, EpisodicEvent
            ledger = get_ledger()
            ledger.append(EpisodicEvent(
                event_type="LEARNING_PIPELINE_COMPLETED",
                summary=(
                    f"Pipeline cycle #{result.cycle_number}: "
                    f"{result.anomalies_detected} anomalías, "
                    f"{result.hypotheses_generated} hipótesis, "
                    f"{result.hypotheses_confirmed} confirmadas, "
                    f"{result.rules_integrated} reglas"
                ),
                metadata={
                    "cycle_number": result.cycle_number,
                    "anomalies": result.anomalies_detected,
                    "hypotheses": result.hypotheses_generated,
                    "confirmed": result.hypotheses_confirmed,
                    "refuted": result.hypotheses_refuted,
                    "rules": result.rules_integrated,
                    "elapsed_ms": result.elapsed_ms,
                },
            ))
        except Exception as exc:
            logger.warning("Failed to record pipeline cycle: %s", exc)

    # =====================================================================
    # Status
    # =====================================================================

    def status(self) -> Dict[str, Any]:
        """Estado completo del pipeline y sus módulos."""
        return {
            "pipeline": {
                "cycles_completed": self._cycle_count,
                "total_anomalies_processed": self._total_anomalies,
                "total_rules_created": self._total_rules,
            },
            "detector": self._detector.stats(),
            "investigator": self._investigator.stats(),
            "verifier": self._verifier.stats(),
            "integrator": self._integrator.stats(),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_pipeline: Optional[LearningPipeline] = None


def get_learning_pipeline() -> LearningPipeline:
    """Obtener la instancia singleton del LearningPipeline."""
    global _pipeline
    if _pipeline is None:
        _pipeline = LearningPipeline()
    return _pipeline


def reset_learning_pipeline() -> None:
    """Reset para testing."""
    global _pipeline
    _pipeline = None
