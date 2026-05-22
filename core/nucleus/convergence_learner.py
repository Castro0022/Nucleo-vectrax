"""
Vectrax — ConvergenceLearner
==============================
Cierra el ciclo de conciencia operacional.

    observar → aprender → recomendar → (autorización) → ajustar

ConvergenceLearner no reemplaza PresenciaPura.
La entrena.

Fases:
  OBSERVE   — registra decisiones y resultados posteriores
  LEARN     — detecta patrones por motor y condición
  RECOMMEND — propone ajustes de umbrales con evidencia
  APPLY     — ajusta solo con autorización explícita del creador

Regla absoluta:
  Ningún umbral se modifica sin autorización.
  El sistema propone. El creador decide.

Principio de operación:
  PresenciaPura no debe volver a Vectrax tímido ni bloqueado.
  Si hay convergencia clara, soberanía suficiente y bajo ruido: EJECUTA.
  Intervenir solo cuando: no hay convergencia, hay ruido, hay pérdida
  de soberanía, el origen es externo, o la acción puede degradar el núcleo.
  ConvergenceLearner optimiza los umbrales para que este principio se cumpla.

Prioridad operacional:
  1. Ejecutar cuando está claro.
  2. Pausar solo cuando hay duda real.
  3. Silenciar cuando no hay convergencia.
  4. Bloquear solo cuando hay riesgo o pérdida de soberanía.

Capa: 1 — Núcleo Central
Creado: 2026-05-22
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import uuid
import logging
import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger("vectrax.convergence_learner")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class LearnerPhase(str, Enum):
    """Fases del ciclo de aprendizaje."""
    OBSERVE   = "observe"    # solo registra — fase inicial
    LEARN     = "learn"      # detecta patrones
    RECOMMEND = "recommend"  # propone ajustes
    APPLY     = "apply"      # ajusta con autorización


class OutcomeQuality(str, Enum):
    """Calidad del resultado posterior a una decisión de PresenciaPura."""
    IMPROVED = "improved"   # el sistema mejoró tras la decisión
    NEUTRAL  = "neutral"    # sin cambio detectable
    DEGRADED = "degraded"   # el sistema degradó tras la decisión
    UNKNOWN  = "unknown"    # resultado aún no registrado


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DecisionOutcome:
    """
    Registro de una decisión de PresenciaPura y su resultado posterior.
    Es la unidad base de aprendizaje del ConvergenceLearner.
    """
    outcome_id:         str
    timestamp:          str
    motor_name:         str
    origin:             str            # EmissionOrigin.value
    convergence_score:  float          # 0.0 – 1.0
    sovereignty_score:  float          # 0.0 – 1.0
    noise_level:        float          # 0.0 – 1.0
    decision:           str            # InhibitionDecision.value

    # Resultado — se actualiza con record_outcome()
    outcome:            OutcomeQuality  = OutcomeQuality.UNKNOWN
    outcome_timestamp:  Optional[str]   = None
    outcome_detail:     str             = ""

    # Métricas del sistema después de la decisión
    post_coherence_score: float = 0.0
    post_latency_ms:      float = 0.0
    post_error_count:     int   = 0

    def __post_init__(self) -> None:
        for attr in ("convergence_score", "sovereignty_score", "noise_level"):
            val = getattr(self, attr)
            if not (0.0 <= val <= 1.0):
                raise ValueError(
                    f"{attr} debe estar entre 0.0 y 1.0, recibido: {val}"
                )


@dataclass
class MotorPattern:
    """
    Patrón detectado para un motor específico bajo ciertas condiciones.
    Se genera únicamente cuando hay muestras suficientes y degradación
    significativa — nunca de forma especulativa.
    """
    pattern_id:        str
    motor_name:        str
    condition:         str    # descripción legible de la condición
    sample_size:       int    # observaciones que respaldan este patrón
    degradation_rate:  float  # fracción de veces que el sistema degradó
    improvement_rate:  float  # fracción de veces que el sistema mejoró
    avg_convergence:   float
    avg_sovereignty:   float
    avg_noise:         float
    dominant_decision: str    # decisión más frecuente bajo esta condición
    confidence:        float  # 0.0 – 1.0
    detected_at:       str

    def summary(self) -> str:
        return (
            f"Motor: {self.motor_name} | Condicion: {self.condition}\n"
            f"  Muestras: {self.sample_size} | Confianza: {self.confidence:.2f}\n"
            f"  Degradacion: {self.degradation_rate:.1%} | "
            f"Mejora: {self.improvement_rate:.1%}\n"
            f"  Scores — conv:{self.avg_convergence:.2f} "
            f"sov:{self.avg_sovereignty:.2f} noise:{self.avg_noise:.2f}"
        )


@dataclass
class ThresholdRecommendation:
    """
    Propuesta de ajuste de umbral para PresenciaPura.
    No se aplica hasta autorización explícita del creador.
    ConvergenceLearner nunca toca PresenciaPura directamente.
    """
    rec_id:              str
    timestamp:           str
    motor_name:          Optional[str]  # None = umbral global
    threshold_name:      str            # nombre del umbral en PresenciaObserver
    current_value:       float
    recommended_value:   float
    direction:           str            # "up" | "down"
    evidence_pattern_id: str
    sample_size:         int
    confidence:          float
    reasoning:           str

    status:     str           = "pending"   # pending | approved | rejected
    applied_at: Optional[str] = None
    applied_by: Optional[str] = None

    def report(self) -> str:
        arrow = "↑" if self.direction == "up" else "↓"
        return (
            f"[RECOMENDACION {self.rec_id}]\n"
            f"  Motor       : {self.motor_name or 'GLOBAL'}\n"
            f"  Umbral      : {self.threshold_name}\n"
            f"  Cambio      : {self.current_value:.2f} {arrow} {self.recommended_value:.2f}\n"
            f"  Confianza   : {self.confidence:.2f} ({self.sample_size} muestras)\n"
            f"  Razonamiento: {self.reasoning}\n"
            f"  Estado      : {self.status.upper()}"
        )


# ---------------------------------------------------------------------------
# ConvergenceLearner
# ---------------------------------------------------------------------------

class ConvergenceLearner:
    """
    Observa las decisiones de PresenciaPura, detecta patrones y propone
    ajustes de umbrales con evidencia sólida.

    No ajusta nada sin autorización del creador.

    Ciclo:
        1. record_decision()              — registrar al momento de decidir
        2. record_outcome()               — registrar resultado posterior
        3. analyze()                      — detectar patrones
        4. generate_recommendations()     — proponer ajustes
        5. approve_recommendation()       — el creador aprueba
        6. reject_recommendation()        — el creador rechaza
    """

    # Mínimo de muestras para generar un patrón fiable
    MIN_SAMPLES_FOR_PATTERN = 5

    # Tasa de degradación mínima para que un patrón sea relevante
    DEGRADATION_THRESHOLD = 0.40

    # Mínimo de outcomes conocidos para avanzar a LEARN
    MIN_OUTCOMES_FOR_LEARN = 10

    def __init__(self, phase: LearnerPhase = LearnerPhase.OBSERVE) -> None:
        self.phase = phase
        self.outcomes:        List[DecisionOutcome]        = []
        self.patterns:        List[MotorPattern]           = []
        self.recommendations: List[ThresholdRecommendation] = []
        self._session_start = _now_iso()
        logger.info(
            "ConvergenceLearner init | fase=%s | observando decisiones de PresenciaPura",
            phase.value,
        )

    # ── Fase 1 — Observar ───────────────────────────────────────────────────

    def record_decision(
        self,
        motor_name: str,
        origin: str,
        convergence_score: float,
        sovereignty_score: float,
        noise_level: float,
        decision: str,
    ) -> str:
        """
        Registra una decisión de PresenciaPura en el momento en que ocurre.
        Retorna outcome_id para llamar a record_outcome() cuando el resultado
        sea conocido.
        """
        outcome = DecisionOutcome(
            outcome_id=uuid.uuid4().hex[:8],
            timestamp=_now_iso(),
            motor_name=motor_name,
            origin=origin,
            convergence_score=convergence_score,
            sovereignty_score=sovereignty_score,
            noise_level=noise_level,
            decision=decision,
        )
        self.outcomes.append(outcome)
        logger.debug(
            "Learner.record: %s | %s | conv=%.2f sov=%.2f noise=%.2f",
            motor_name, decision, convergence_score, sovereignty_score, noise_level,
        )
        return outcome.outcome_id

    def record_outcome(
        self,
        outcome_id: str,
        quality: OutcomeQuality,
        post_coherence_score: float = 0.0,
        post_latency_ms: float = 0.0,
        post_error_count: int = 0,
        detail: str = "",
    ) -> bool:
        """
        Registra el resultado posterior a una decisión.
        Llamar cuando el sistema tiene datos de cómo afectó la decisión.
        Retorna True si encontró y actualizó el outcome.
        """
        for o in self.outcomes:
            if o.outcome_id == outcome_id:
                o.outcome              = quality
                o.outcome_timestamp    = _now_iso()
                o.outcome_detail       = detail
                o.post_coherence_score = post_coherence_score
                o.post_latency_ms      = post_latency_ms
                o.post_error_count     = post_error_count
                logger.info(
                    "Learner.outcome: %s | %s | %s | %s",
                    outcome_id, o.motor_name, quality.value, detail,
                )
                return True
        logger.warning("Learner: outcome_id no encontrado: %s", outcome_id)
        return False

    # ── Fase 2 — Aprender ───────────────────────────────────────────────────

    def analyze(self) -> List[MotorPattern]:
        """
        Analiza los outcomes registrados y detecta patrones por motor.

        - Solo opera con outcomes que tienen resultado conocido.
        - Requiere mínimo MIN_SAMPLES_FOR_PATTERN muestras por condición.
        - Disponible en todas las fases, pero solo genera recomendaciones
          a partir de RECOMMEND.
        """
        known = [o for o in self.outcomes if o.outcome != OutcomeQuality.UNKNOWN]
        if not known:
            logger.info("Learner.analyze: sin outcomes conocidos.")
            return []

        by_motor: Dict[str, List[DecisionOutcome]] = defaultdict(list)
        for o in known:
            by_motor[o.motor_name].append(o)

        new_patterns: List[MotorPattern] = []
        for motor, records in by_motor.items():
            if len(records) < self.MIN_SAMPLES_FOR_PATTERN:
                continue
            new_patterns.extend(self._detect_motor_patterns(motor, records))

        self.patterns = new_patterns
        logger.info("Learner.analyze: %d patrones detectados.", len(new_patterns))
        return new_patterns

    def _detect_motor_patterns(
        self, motor: str, records: List[DecisionOutcome]
    ) -> List[MotorPattern]:
        patterns: List[MotorPattern] = []

        # Condición A: sovereignty bajo + noise alto
        cond_a = [
            r for r in records
            if r.sovereignty_score < 0.50 and r.noise_level > 0.40
        ]
        if len(cond_a) >= self.MIN_SAMPLES_FOR_PATTERN:
            p = self._build_pattern(
                motor, cond_a, "sovereignty < 0.50 y noise > 0.40"
            )
            if p:
                patterns.append(p)

        # Condición B: convergencia baja general
        cond_b = [r for r in records if r.convergence_score < 0.50]
        if len(cond_b) >= self.MIN_SAMPLES_FOR_PATTERN:
            p = self._build_pattern(
                motor, cond_b, "convergence < 0.50"
            )
            if p:
                patterns.append(p)

        # Condición C: origen externo con sovereignty medio
        cond_c = [
            r for r in records
            if "external" in r.origin and r.sovereignty_score < 0.70
        ]
        if len(cond_c) >= self.MIN_SAMPLES_FOR_PATTERN:
            p = self._build_pattern(
                motor, cond_c, "origin=external y sovereignty < 0.70"
            )
            if p:
                patterns.append(p)

        return patterns

    def _build_pattern(
        self, motor: str, records: List[DecisionOutcome], condition: str
    ) -> Optional[MotorPattern]:
        if not records:
            return None

        total    = len(records)
        degraded = sum(1 for r in records if r.outcome == OutcomeQuality.DEGRADED)
        improved = sum(1 for r in records if r.outcome == OutcomeQuality.IMPROVED)
        degradation_rate = degraded / total
        improvement_rate = improved / total

        # Solo crear patrón si hay degradación significativa
        if degradation_rate < self.DEGRADATION_THRESHOLD:
            return None

        decisions = [r.decision for r in records]
        dominant  = max(set(decisions), key=decisions.count)

        # Confianza: escala con muestra y consistencia, máximo 0.95
        confidence = min(0.95, (total / 20) * degradation_rate)

        return MotorPattern(
            pattern_id=uuid.uuid4().hex[:8],
            motor_name=motor,
            condition=condition,
            sample_size=total,
            degradation_rate=degradation_rate,
            improvement_rate=improvement_rate,
            avg_convergence=sum(r.convergence_score for r in records) / total,
            avg_sovereignty=sum(r.sovereignty_score for r in records) / total,
            avg_noise=sum(r.noise_level for r in records) / total,
            dominant_decision=dominant,
            confidence=confidence,
            detected_at=_now_iso(),
        )

    # ── Fase 3 — Recomendar ─────────────────────────────────────────────────

    def generate_recommendations(
        self, current_thresholds: dict
    ) -> List[ThresholdRecommendation]:
        """
        Genera recomendaciones de ajuste basadas en patrones detectados.
        No modifica nada. Solo propone.

        current_thresholds: valores actuales de los umbrales de PresenciaObserver.
        Ej: {"THRESHOLD_SOVEREIGNTY_BLOCK": 0.30, "THRESHOLD_CONVERGENCE_SILENCE": 0.30, ...}
        """
        if not self.patterns:
            logger.info("Learner: sin patrones para recomendar.")
            return []

        # Índice de recomendaciones pendientes para evitar duplicados
        existing: set = {
            (r.motor_name, r.threshold_name)
            for r in self.recommendations
            if r.status == "pending"
        }

        for pattern in self.patterns:
            for rec in self._recommend_for_pattern(pattern, current_thresholds):
                key = (rec.motor_name, rec.threshold_name)
                if key not in existing:
                    self.recommendations.append(rec)
                    existing.add(key)
                    logger.info(
                        "Learner.recommend: %s | %s %.2f→%.2f",
                        rec.motor_name or "GLOBAL",
                        rec.threshold_name,
                        rec.current_value,
                        rec.recommended_value,
                    )

        return [r for r in self.recommendations if r.status == "pending"]

    def _recommend_for_pattern(
        self, pattern: MotorPattern, current: dict
    ) -> List[ThresholdRecommendation]:
        recs: List[ThresholdRecommendation] = []

        # Sovereignty promedio bajo + degrada → subir THRESHOLD_SOVEREIGNTY_BLOCK
        if pattern.avg_sovereignty < 0.60 and pattern.degradation_rate > 0.50:
            key = "THRESHOLD_SOVEREIGNTY_BLOCK"
            cur = current.get(key, 0.30)
            new = min(0.95, round(cur + 0.08, 2))
            if new != cur:
                recs.append(ThresholdRecommendation(
                    rec_id=uuid.uuid4().hex[:8],
                    timestamp=_now_iso(),
                    motor_name=pattern.motor_name,
                    threshold_name=key,
                    current_value=cur,
                    recommended_value=new,
                    direction="up",
                    evidence_pattern_id=pattern.pattern_id,
                    sample_size=pattern.sample_size,
                    confidence=pattern.confidence,
                    reasoning=(
                        f"Motor '{pattern.motor_name}' degrada el sistema en "
                        f"{pattern.degradation_rate:.1%} cuando {pattern.condition}. "
                        f"Soberania promedio: {pattern.avg_sovereignty:.2f}. "
                        f"Subir umbral filtraria estas emisiones sin afectar el nucleo."
                    ),
                ))

        # Noise promedio alto + degrada → bajar THRESHOLD_NOISE_PAUSE
        if pattern.avg_noise > 0.45 and pattern.degradation_rate > 0.40:
            key = "THRESHOLD_NOISE_PAUSE"
            cur = current.get(key, 0.80)
            new = max(0.50, round(cur - 0.05, 2))
            if new != cur:
                recs.append(ThresholdRecommendation(
                    rec_id=uuid.uuid4().hex[:8],
                    timestamp=_now_iso(),
                    motor_name=pattern.motor_name,
                    threshold_name=key,
                    current_value=cur,
                    recommended_value=new,
                    direction="down",
                    evidence_pattern_id=pattern.pattern_id,
                    sample_size=pattern.sample_size,
                    confidence=pattern.confidence,
                    reasoning=(
                        f"Motor '{pattern.motor_name}' tiene noise promedio "
                        f"{pattern.avg_noise:.2f} bajo '{pattern.condition}'. "
                        f"Degradacion: {pattern.degradation_rate:.1%}. "
                        f"Reducir umbral de ruido aumentaria sensibilidad preventiva."
                    ),
                ))

        return recs

    # ── Fase 4 — Autorización y aplicación ─────────────────────────────────

    def approve_recommendation(
        self, rec_id: str, approved_by: str = "creator"
    ) -> Optional[dict]:
        """
        Aprueba una recomendación. Retorna el ajuste a aplicar.

        La aplicación real es responsabilidad del creador o un sistema externo.
        ConvergenceLearner NO toca PresenciaPura directamente.
        """
        for rec in self.recommendations:
            if rec.rec_id == rec_id and rec.status == "pending":
                rec.status     = "approved"
                rec.applied_at = _now_iso()
                rec.applied_by = approved_by
                logger.warning(
                    "Learner.APPROVED by %s: %s | %s %.2f→%.2f",
                    approved_by, rec.motor_name or "GLOBAL",
                    rec.threshold_name, rec.current_value, rec.recommended_value,
                )
                return {
                    "threshold_name":  rec.threshold_name,
                    "motor_name":      rec.motor_name,
                    "new_value":       rec.recommended_value,
                    "approved_by":     approved_by,
                    "applied_at":      rec.applied_at,
                }
        logger.warning("Learner.approve: rec_id no encontrado o no pendiente: %s", rec_id)
        return None

    def reject_recommendation(self, rec_id: str, reason: str = "") -> bool:
        """Rechaza una recomendación. Retorna True si fue encontrada."""
        for rec in self.recommendations:
            if rec.rec_id == rec_id and rec.status == "pending":
                rec.status = "rejected"
                logger.info("Learner.REJECTED: %s | %s", rec_id, reason)
                return True
        logger.warning("Learner.reject: rec_id no encontrado: %s", rec_id)
        return False

    # ── Control de fase ─────────────────────────────────────────────────────

    def advance_phase(self) -> LearnerPhase:
        """
        Avanza a la siguiente fase si hay datos suficientes.
        Nunca retrocede — cada fase incluye la anterior.
        """
        order = [
            LearnerPhase.OBSERVE,
            LearnerPhase.LEARN,
            LearnerPhase.RECOMMEND,
            LearnerPhase.APPLY,
        ]
        current_idx = order.index(self.phase)
        if current_idx >= len(order) - 1:
            logger.info("Learner: ya en fase maxima APPLY.")
            return self.phase

        next_phase = order[current_idx + 1]
        known_count = sum(
            1 for o in self.outcomes if o.outcome != OutcomeQuality.UNKNOWN
        )

        if next_phase == LearnerPhase.LEARN:
            if known_count < self.MIN_OUTCOMES_FOR_LEARN:
                logger.warning(
                    "Learner: necesita %d outcomes para LEARN, tiene %d.",
                    self.MIN_OUTCOMES_FOR_LEARN, known_count,
                )
                return self.phase

        if next_phase == LearnerPhase.RECOMMEND:
            if not self.patterns:
                logger.warning("Learner: necesita patrones detectados para RECOMMEND.")
                return self.phase

        if next_phase == LearnerPhase.APPLY:
            pending = [r for r in self.recommendations if r.status == "pending"]
            if not pending:
                logger.warning("Learner: sin recomendaciones pendientes para APPLY.")
                return self.phase

        old = self.phase
        self.phase = next_phase
        logger.warning("Learner.phase: %s → %s", old.value, self.phase.value)
        return self.phase

    # ── Consultas ────────────────────────────────────────────────────────────

    def get_pending_recommendations(self) -> List[ThresholdRecommendation]:
        return [r for r in self.recommendations if r.status == "pending"]

    def get_stats(self) -> dict:
        known = [o for o in self.outcomes if o.outcome != OutcomeQuality.UNKNOWN]
        return {
            "phase":            self.phase.value,
            "total_outcomes":   len(self.outcomes),
            "known_outcomes":   len(known),
            "patterns":         len(self.patterns),
            "recommendations":  len(self.recommendations),
            "pending":          sum(1 for r in self.recommendations if r.status == "pending"),
            "approved":         sum(1 for r in self.recommendations if r.status == "approved"),
            "rejected":         sum(1 for r in self.recommendations if r.status == "rejected"),
        }

    def report(self) -> str:
        known   = [o for o in self.outcomes if o.outcome != OutcomeQuality.UNKNOWN]
        pending = self.get_pending_recommendations()

        lines = [
            "═" * 60,
            "  VECTRAX — ConvergenceLearner",
            f"  Fase     : {self.phase.value.upper()}",
            f"  Sesion   : {self._session_start}",
            "═" * 60,
            "",
            "[ OBSERVACIONES ]",
            f"  Total registradas    : {len(self.outcomes)}",
            f"  Con outcome conocido : {len(known)}",
            f"  Sin outcome aun      : {len(self.outcomes) - len(known)}",
        ]
        if known:
            degraded = sum(1 for o in known if o.outcome == OutcomeQuality.DEGRADED)
            improved = sum(1 for o in known if o.outcome == OutcomeQuality.IMPROVED)
            neutral  = sum(1 for o in known if o.outcome == OutcomeQuality.NEUTRAL)
            lines += [
                f"  Degradaciones        : {degraded} ({degraded/len(known):.1%})",
                f"  Mejoras              : {improved} ({improved/len(known):.1%})",
                f"  Neutrales            : {neutral}",
            ]

        lines += [
            "",
            "[ PATRONES DETECTADOS ]",
            f"  Total : {len(self.patterns)}",
        ]
        for p in self.patterns:
            lines.append(f"\n  {p.summary()}")

        lines += [
            "",
            "[ RECOMENDACIONES ]",
            f"  Total     : {len(self.recommendations)}",
            f"  Pendientes: {len(pending)}",
            f"  Aprobadas : {sum(1 for r in self.recommendations if r.status == 'approved')}",
            f"  Rechazadas: {sum(1 for r in self.recommendations if r.status == 'rejected')}",
        ]
        for rec in pending:
            lines.append(f"\n  {rec.report()}")

        lines += ["", "═" * 60]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_learner: Optional[ConvergenceLearner] = None


def get_learner() -> ConvergenceLearner:
    """
    Singleton del ConvergenceLearner.
    Inicia siempre en fase OBSERVE (solo registra, nunca modifica).
    """
    global _learner
    if _learner is None:
        _learner = ConvergenceLearner(phase=LearnerPhase.OBSERVE)
    return _learner


def reset_learner() -> None:
    """Resetea el singleton. SOLO para testing. NO usar en producción."""
    global _learner
    _learner = None
