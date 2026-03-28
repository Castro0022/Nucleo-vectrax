"""
Vectrax VERIFICATION_ENGINE
==============================
Valida hipótesis con evidencia cruzada de múltiples fuentes.
Asigna nivel de confianza y rechaza conclusiones sin datos suficientes.

Estrategias de verificación:
  - temporal_consistency: el patrón se mantiene en el tiempo
  - cross_source: confirmado por múltiples fuentes independientes
  - statistical_significance: pasa umbrales estadísticos
  - precedent_match: coincide con patrones previos confirmados

Principios:
  - Nunca asumir sin verificación
  - Rechazar datos insuficientes explícitamente
  - La confianza se calcula, nunca se asume

Privacy: solo opera con descriptores abstractos.

Creado: 2026-03-28
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from core.learning_cycle import (
    MIN_EVIDENCE_FOR_VERIFICATION,
    MIN_CONFIDENCE_FOR_INTEGRATION,
    VERIFICATION_STRATEGIES,
)

logger = logging.getLogger("vectrax.learning_cycle.verification")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class VerificationResult:
    """Resultado de verificación de una hipótesis."""
    hypothesis_id: str = ""
    verified: bool = False
    confidence: float = 0.0
    strategies_applied: List[str] = field(default_factory=list)
    evidence_added: int = 0
    rejection_reason: str = ""
    final_status: str = ""      # "confirmed" | "refuted" | "inconclusive" | "insufficient_data"
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Verification Engine
# ---------------------------------------------------------------------------

class VerificationEngine:
    """
    Motor de verificación de hipótesis.

    Aplica múltiples estrategias de verificación y agrega evidencia
    al HypothesisEngine. Solo marca como verificada si hay suficiente
    evidencia y la confianza supera los umbrales.

    REGLA: rechaza explícitamente cuando los datos son insuficientes.

    Usage::

        engine = VerificationEngine()
        result = engine.verify("HYP-ABC12345")
        # result.verified indica si la hipótesis fue verificada
    """

    def __init__(self) -> None:
        self._total_verifications: int = 0
        self._total_confirmed: int = 0
        self._total_refuted: int = 0
        self._total_insufficient: int = 0
        logger.info("VerificationEngine initialized")

    # =====================================================================
    # Main API
    # =====================================================================

    def verify(self, hypothesis_id: str) -> VerificationResult:
        """
        Verificar una hipótesis aplicando todas las estrategias disponibles.

        Pasos:
          1. Obtener hipótesis del HypothesisEngine
          2. Aplicar cada estrategia de verificación
          3. Agregar evidencia resultante
          4. Intentar resolver la hipótesis
          5. Retornar resultado
        """
        result = VerificationResult(hypothesis_id=hypothesis_id)

        # --- Obtener hipótesis ---
        try:
            from core.operator.hypothesis_engine import get_hypothesis_engine
            hyp_engine = get_hypothesis_engine()
        except Exception as exc:
            result.rejection_reason = f"Cannot access HypothesisEngine: {exc}"
            result.final_status = "error"
            return result

        hyp = hyp_engine.get(hypothesis_id)
        if hyp is None:
            result.rejection_reason = f"Hypothesis {hypothesis_id} not found"
            result.final_status = "not_found"
            return result

        # --- Verificar que la hipótesis es verificable ---
        from core.operator.hypothesis_engine import HypothesisStatus
        if hyp.status not in (HypothesisStatus.PROPOSED, HypothesisStatus.TESTING):
            result.rejection_reason = f"Hypothesis already resolved: {hyp.status.value}"
            result.final_status = hyp.status.value
            result.confidence = hyp.confidence
            return result

        # --- Aplicar estrategias ---
        evidence_count = 0

        # 1. Temporal consistency
        ev_temporal = self._strategy_temporal_consistency(hyp, hyp_engine)
        evidence_count += ev_temporal
        if ev_temporal > 0:
            result.strategies_applied.append("temporal_consistency")

        # 2. Cross source
        ev_cross = self._strategy_cross_source(hyp, hyp_engine)
        evidence_count += ev_cross
        if ev_cross > 0:
            result.strategies_applied.append("cross_source")

        # 3. Statistical significance
        ev_stats = self._strategy_statistical_significance(hyp, hyp_engine)
        evidence_count += ev_stats
        if ev_stats > 0:
            result.strategies_applied.append("statistical_significance")

        # 4. Precedent match
        ev_precedent = self._strategy_precedent_match(hyp, hyp_engine)
        evidence_count += ev_precedent
        if ev_precedent > 0:
            result.strategies_applied.append("precedent_match")

        result.evidence_added = evidence_count

        # --- Check minimum evidence ---
        # Re-fetch after adding evidence
        hyp = hyp_engine.get(hypothesis_id)

        if len(hyp.evidence) < MIN_EVIDENCE_FOR_VERIFICATION:
            result.verified = False
            result.rejection_reason = (
                f"Datos insuficientes: {len(hyp.evidence)} evidencias "
                f"(mínimo={MIN_EVIDENCE_FOR_VERIFICATION})"
            )
            result.final_status = "insufficient_data"
            result.confidence = hyp.confidence
            self._total_insufficient += 1
            self._total_verifications += 1

            logger.info(
                "Verification insufficient: %s (%d evidence, need %d)",
                hypothesis_id, len(hyp.evidence), MIN_EVIDENCE_FOR_VERIFICATION,
            )
            return result

        # --- Intentar resolver ---
        resolved = hyp_engine.resolve(hypothesis_id)
        if resolved is None:
            result.final_status = "error"
            result.rejection_reason = "Resolution failed"
            return result

        result.confidence = resolved.confidence

        if resolved.status == HypothesisStatus.CONFIRMED:
            result.verified = True
            result.final_status = "confirmed"
            self._total_confirmed += 1
        elif resolved.status == HypothesisStatus.REFUTED:
            result.verified = False
            result.final_status = "refuted"
            result.rejection_reason = (
                f"Evidencia en contra: confidence={resolved.confidence:.2f}"
            )
            self._total_refuted += 1
        else:
            result.verified = False
            result.final_status = "inconclusive"
            result.rejection_reason = (
                f"Confianza insuficiente para confirmar o refutar: "
                f"{resolved.confidence:.2f}"
            )

        result.details = {
            "total_evidence": len(resolved.evidence),
            "strategies_count": len(result.strategies_applied),
        }

        self._total_verifications += 1

        logger.info(
            "Verification complete: %s → %s (confidence=%.2f, evidence=%d)",
            hypothesis_id, result.final_status, result.confidence,
            len(resolved.evidence),
        )

        return result

    def verify_batch(self, hypothesis_ids: List[str]) -> List[VerificationResult]:
        """Verificar múltiples hipótesis."""
        return [self.verify(hid) for hid in hypothesis_ids]

    # =====================================================================
    # Verification strategies
    # =====================================================================

    def _strategy_temporal_consistency(self, hyp, hyp_engine) -> int:
        """
        Verifica si el patrón se mantiene en el tiempo.

        Busca en el ledger si eventos similares aparecen
        consistentemente en las últimas horas.
        """
        evidence_added = 0

        try:
            from core.learn.episodic import get_ledger
            from core.operator.hypothesis_engine import Evidence, EvidenceType

            ledger = get_ledger()
            recent_events = ledger.since(hours=24.0)

            # Buscar eventos que coincidan con los tags de la hipótesis
            matching = 0
            for ev in recent_events:
                summary = ev.get("summary", "")
                for tag in hyp.tags:
                    if tag in summary and tag != "learning_cycle":
                        matching += 1
                        break

            if matching >= 3:
                hyp_engine.add_evidence(
                    hyp.id,
                    Evidence(
                        evidence_type=EvidenceType.OBSERVATION,
                        description=(
                            f"Consistencia temporal: {matching} eventos "
                            f"relacionados en últimas 24h"
                        ),
                        weight=min(0.7, matching * 0.1),
                        supports=True,
                        source="verification:temporal_consistency",
                    ),
                )
                evidence_added += 1
            elif matching == 0 and len(recent_events) > 10:
                # Sin soporte temporal — evidencia en contra
                hyp_engine.add_evidence(
                    hyp.id,
                    Evidence(
                        evidence_type=EvidenceType.CONTRADICTION,
                        description=(
                            f"Sin consistencia temporal: 0 eventos "
                            f"relacionados en {len(recent_events)} recientes"
                        ),
                        weight=0.3,
                        supports=False,
                        source="verification:temporal_consistency",
                    ),
                )
                evidence_added += 1

        except Exception as exc:
            logger.warning("temporal_consistency error: %s", exc)

        return evidence_added

    def _strategy_cross_source(self, hyp, hyp_engine) -> int:
        """
        Verifica si múltiples fuentes independientes soportan la hipótesis.

        Revisa si la evidencia existente proviene de fuentes distintas.
        """
        evidence_added = 0

        try:
            from core.operator.hypothesis_engine import Evidence, EvidenceType

            existing_sources = set()
            for ev in hyp.evidence:
                if ev.source:
                    existing_sources.add(ev.source.split(":")[0])

            if len(existing_sources) >= 2:
                hyp_engine.add_evidence(
                    hyp.id,
                    Evidence(
                        evidence_type=EvidenceType.CONFIRMATION,
                        description=(
                            f"Cross-source: {len(existing_sources)} fuentes "
                            f"independientes ({', '.join(existing_sources)})"
                        ),
                        weight=min(0.8, len(existing_sources) * 0.2),
                        supports=True,
                        source="verification:cross_source",
                    ),
                )
                evidence_added += 1

        except Exception as exc:
            logger.warning("cross_source error: %s", exc)

        return evidence_added

    def _strategy_statistical_significance(self, hyp, hyp_engine) -> int:
        """
        Verifica si el patrón es estadísticamente significativo.

        Usa el conteo de observaciones y la confianza acumulada.
        """
        evidence_added = 0

        try:
            from core.operator.hypothesis_engine import Evidence, EvidenceType

            supporting = [e for e in hyp.evidence if e.supports]
            contradicting = [e for e in hyp.evidence if not e.supports]

            total = len(supporting) + len(contradicting)
            if total < 2:
                return 0

            support_ratio = len(supporting) / total

            if support_ratio >= 0.75 and total >= 3:
                hyp_engine.add_evidence(
                    hyp.id,
                    Evidence(
                        evidence_type=EvidenceType.METRIC,
                        description=(
                            f"Significancia estadística: {support_ratio:.0%} "
                            f"de soporte ({len(supporting)}/{total})"
                        ),
                        weight=min(0.8, support_ratio),
                        supports=True,
                        source="verification:statistical_significance",
                    ),
                )
                evidence_added += 1
            elif support_ratio <= 0.25 and total >= 3:
                hyp_engine.add_evidence(
                    hyp.id,
                    Evidence(
                        evidence_type=EvidenceType.METRIC,
                        description=(
                            f"Significancia negativa: solo {support_ratio:.0%} "
                            f"de soporte ({len(supporting)}/{total})"
                        ),
                        weight=0.6,
                        supports=False,
                        source="verification:statistical_significance",
                    ),
                )
                evidence_added += 1

        except Exception as exc:
            logger.warning("statistical_significance error: %s", exc)

        return evidence_added

    def _strategy_precedent_match(self, hyp, hyp_engine) -> int:
        """
        Verifica si existe un precedente (regla o hipótesis confirmada)
        que coincida con el patrón.
        """
        evidence_added = 0

        try:
            from core.operator.hypothesis_engine import (
                Evidence, EvidenceType, HypothesisStatus,
            )

            # Buscar hipótesis previas confirmadas con tags similares
            confirmed = hyp_engine.get_by_status(HypothesisStatus.CONFIRMED)
            matching_precedents = []

            for prev in confirmed:
                if prev.id == hyp.id:
                    continue
                common_tags = set(prev.tags) & set(hyp.tags)
                common_tags.discard("learning_cycle")
                if common_tags:
                    matching_precedents.append(prev.id)

            if matching_precedents:
                hyp_engine.add_evidence(
                    hyp.id,
                    Evidence(
                        evidence_type=EvidenceType.PRECEDENT,
                        description=(
                            f"Precedente encontrado: {len(matching_precedents)} "
                            f"hipótesis confirmadas con patrón similar"
                        ),
                        weight=min(0.7, len(matching_precedents) * 0.25),
                        supports=True,
                        source="verification:precedent_match",
                    ),
                )
                evidence_added += 1

            # Buscar en reglas aprendidas
            try:
                from core.learn.learned_rules import get_rules_store
                store = get_rules_store()
                active_rules = store.get_active_rules()

                matching_rules = [
                    r for r in active_rules
                    if any(tag in r.get("pattern", "") for tag in hyp.tags if tag != "learning_cycle")
                ]
                if matching_rules:
                    hyp_engine.add_evidence(
                        hyp.id,
                        Evidence(
                            evidence_type=EvidenceType.PRECEDENT,
                            description=(
                                f"Regla existente coincidente: {len(matching_rules)} "
                                f"reglas activas con patrón similar"
                            ),
                            weight=min(0.6, len(matching_rules) * 0.2),
                            supports=True,
                            source="verification:precedent_match_rules",
                        ),
                    )
                    evidence_added += 1
            except Exception:
                pass

        except Exception as exc:
            logger.warning("precedent_match error: %s", exc)

        return evidence_added

    # =====================================================================
    # Status
    # =====================================================================

    def stats(self) -> Dict[str, Any]:
        return {
            "total_verifications": self._total_verifications,
            "total_confirmed": self._total_confirmed,
            "total_refuted": self._total_refuted,
            "total_insufficient": self._total_insufficient,
            "confirmation_rate": (
                round(self._total_confirmed / max(self._total_verifications, 1), 4)
            ),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_engine: Optional[VerificationEngine] = None


def get_verification_engine() -> VerificationEngine:
    """Obtener la instancia singleton del VerificationEngine."""
    global _engine
    if _engine is None:
        _engine = VerificationEngine()
    return _engine


def reset_verification_engine() -> None:
    """Reset para testing."""
    global _engine
    _engine = None
