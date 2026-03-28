"""
Vectrax LEARNING_INTEGRATOR
===============================
Solo almacena patrones verificados. Convierte hipótesis confirmadas
en reglas reutilizables y actualiza memoria estructural.

Principios:
  - Solo acepta hipótesis con status CONFIRMED
  - Convierte en LearnedRule via LearnedRulesStore existente
  - Registra en EpisodicLedger y audit_ledger
  - Genera propuestas sin aplicar automáticamente (modo guiado)

Privacy: solo descriptores abstractos — nunca raw data ni PII.

Creado: 2026-03-28
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from core.learning_cycle import MIN_CONFIDENCE_FOR_INTEGRATION

logger = logging.getLogger("vectrax.learning_cycle.integrator")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class IntegrationResult:
    """Resultado de la integración de una hipótesis como regla."""
    hypothesis_id: str = ""
    integrated: bool = False
    rule_id: str = ""
    rejection_reason: str = ""
    auto_activated: bool = False
    category: str = ""
    confidence: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Learning Integrator
# ---------------------------------------------------------------------------

class LearningIntegrator:
    """
    Integrador de aprendizaje: convierte hipótesis confirmadas en reglas.

    REGLA FUNDAMENTAL: solo acepta hipótesis con status CONFIRMED
    y confianza >= MIN_CONFIDENCE_FOR_INTEGRATION.

    Usage::

        integrator = LearningIntegrator()
        result = integrator.integrate("HYP-ABC12345")
        # result.integrated indica si se creó una regla
    """

    # Mapeo de tags de anomalía a categorías de regla
    CATEGORY_MAP = {
        "frequency_spike": "usage_pattern",
        "new_pattern": "capability_gap",
        "distribution_shift": "behavioral_trend",
        "repetition_burst": "interaction_pattern",
        "outlier_input": "input_anomaly",
    }

    def __init__(self) -> None:
        self._total_integrations: int = 0
        self._total_rules_created: int = 0
        self._total_rejected: int = 0
        logger.info("LearningIntegrator initialized")

    # =====================================================================
    # Main API
    # =====================================================================

    def integrate(self, hypothesis_id: str) -> IntegrationResult:
        """
        Integrar una hipótesis confirmada como regla aprendida.

        Pasos:
          1. Validar que la hipótesis existe y está CONFIRMED
          2. Verificar confianza mínima
          3. Verificar que no exista regla duplicada
          4. Crear LearnedRule
          5. Registrar en EpisodicLedger y audit_ledger
        """
        result = IntegrationResult(hypothesis_id=hypothesis_id)

        # --- 1. Obtener hipótesis ---
        try:
            from core.operator.hypothesis_engine import (
                get_hypothesis_engine, HypothesisStatus,
            )
            hyp_engine = get_hypothesis_engine()
        except Exception as exc:
            result.rejection_reason = f"Cannot access HypothesisEngine: {exc}"
            return result

        hyp = hyp_engine.get(hypothesis_id)
        if hyp is None:
            result.rejection_reason = f"Hypothesis {hypothesis_id} not found"
            self._total_rejected += 1
            return result

        # --- 2. Validar status ---
        if hyp.status != HypothesisStatus.CONFIRMED:
            result.rejection_reason = (
                f"Solo se integran hipótesis CONFIRMED, "
                f"actual: {hyp.status.value}"
            )
            self._total_rejected += 1
            return result

        # --- 3. Validar confianza ---
        if hyp.confidence < MIN_CONFIDENCE_FOR_INTEGRATION:
            result.rejection_reason = (
                f"Confianza insuficiente: {hyp.confidence:.2f} "
                f"(mínimo={MIN_CONFIDENCE_FOR_INTEGRATION})"
            )
            self._total_rejected += 1
            return result

        # --- 4. Verificar duplicados ---
        try:
            from core.learn.learned_rules import get_rules_store, LearnedRule
            store = get_rules_store()

            existing = store.list_all()
            already_exists = any(
                r.get("source_hypothesis_id") == hypothesis_id
                for r in existing
            )
            if already_exists:
                result.rejection_reason = (
                    f"Ya existe una regla para hipótesis {hypothesis_id}"
                )
                return result
        except Exception as exc:
            result.rejection_reason = f"Cannot access LearnedRulesStore: {exc}"
            return result

        # --- 5. Determinar categoría ---
        category = "general"
        for tag in hyp.tags:
            if tag in self.CATEGORY_MAP:
                category = self.CATEGORY_MAP[tag]
                break

        result.category = category
        result.confidence = hyp.confidence

        # --- 6. Crear regla ---
        try:
            rule = LearnedRule(
                description=hyp.description,
                source_hypothesis_id=hyp.id,
                confidence=hyp.confidence,
                pattern=", ".join(
                    t for t in hyp.tags if t != "learning_cycle"
                ),
                action=(
                    f"Patrón aprendido del ciclo de aprendizaje: "
                    f"{hyp.description[:120]}"
                ),
                category=category,
                governor_mode_at_creation="learning_cycle",
                metadata={
                    "evidence_count": len(hyp.evidence),
                    "context": hyp.context[:200] if hyp.context else "",
                    "anomaly_type": hyp.metadata.get("anomaly_type", ""),
                    "severity": hyp.metadata.get("severity", ""),
                    "source_pipeline": "learning_cycle",
                },
            )

            rule_id = store.add_rule(rule)
            result.rule_id = rule_id
            result.integrated = True
            self._total_rules_created += 1

            logger.info(
                "Rule created: %s from hypothesis %s (confidence=%.2f, category=%s)",
                rule_id, hypothesis_id, hyp.confidence, category,
            )
        except Exception as exc:
            result.rejection_reason = f"Failed to create rule: {exc}"
            self._total_rejected += 1
            return result

        # --- 7. Decision Authority: ¿auto-activar o pedir autorización? ---
        result.auto_activated = self._try_activate_rule(
            rule_id, store, hyp, category,
        )

        # --- 8. Registrar en ledgers ---
        self._record_in_episodic_ledger(hyp, rule_id, category)
        self._record_in_audit_ledger(hyp, rule_id, category)

        self._total_integrations += 1
        return result

    def integrate_batch(
        self, hypothesis_ids: List[str],
    ) -> List[IntegrationResult]:
        """Integrar múltiples hipótesis."""
        return [self.integrate(hid) for hid in hypothesis_ids]

    # =====================================================================
    # Rule activation via Decision Authority
    # =====================================================================

    def _try_activate_rule(
        self, rule_id: str, store, hyp, category: str,
    ) -> bool:
        """
        Consulta Decision Authority para decidir si auto-activar la regla.

        - Governor en modo 'act' + riesgo bajo → auto-activa
        - Cualquier otro caso → queda pending + notifica al creador
        """
        try:
            from core.operator.decision_authority import check_authority

            gov_mode = "observe"
            try:
                from core.governor import get_current_policy
                gov_mode = get_current_policy().get("mode", "observe")
            except Exception:
                pass

            decision = check_authority(
                "activate_learned_rule",
                governor_mode=gov_mode,
                risk_level="LOW",
            )

            if decision.auto_approved:
                # Auto-activar
                store.activate_rule(rule_id)
                logger.info(
                    "Rule %s AUTO-ACTIVATED (governor=%s, decision=%s)",
                    rule_id, gov_mode, decision.reason[:60],
                )
                return True
            else:
                # Queda pending — notificar al creador
                logger.info(
                    "Rule %s PENDING approval (governor=%s, reason=%s)",
                    rule_id, gov_mode, decision.reason[:60],
                )
                self._notify_creator_for_approval(
                    rule_id, hyp, category, decision.reason,
                )
                return False

        except Exception as exc:
            logger.warning("Decision authority check failed: %s", exc)
            return False

    @staticmethod
    def _notify_creator_for_approval(
        rule_id: str, hyp, category: str, reason: str,
    ) -> None:
        """
        Notifica al creador via Telegram que hay una regla pendiente.
        """
        try:
            import os
            import httpx

            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            creator_chat_id = os.environ.get("TELEGRAM_CREATOR_CHAT_ID", "")

            if not token or not creator_chat_id:
                # Intentar cargar desde .env
                try:
                    from dotenv import load_dotenv
                    from pathlib import Path
                    for candidate in [
                        Path(__file__).resolve().parent.parent.parent / ".env",
                        Path.home() / "Vectrax" / ".env",
                    ]:
                        if candidate.exists():
                            load_dotenv(candidate, override=False)
                            break
                    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                    creator_chat_id = os.environ.get("TELEGRAM_CREATOR_CHAT_ID", "")
                except Exception:
                    pass

            if not token or not creator_chat_id:
                logger.debug("Cannot notify: no Telegram credentials")
                return

            message = (
                f"🟡 REGLA PENDIENTE DE APROBACIÓN\n"
                f"\n"
                f"Regla: {rule_id}\n"
                f"Categoría: {category}\n"
                f"Confianza: {hyp.confidence:.0%}\n"
                f"Evidencias: {len(hyp.evidence)}\n"
                f"\n"
                f"{hyp.description[:200]}\n"
                f"\n"
                f"Razón: {reason[:100]}\n"
                f"\n"
                f"Responde 'aprobar {rule_id}' para activar."
            )

            url = f"https://api.telegram.org/bot{token}/sendMessage"
            httpx.post(
                url,
                json={"chat_id": creator_chat_id, "text": message},
                timeout=10,
            )
            logger.info("Creator notified for rule approval: %s", rule_id)

        except Exception as exc:
            logger.warning("Creator notification failed: %s", exc)

    # =====================================================================
    # Ledger recording
    # =====================================================================

    def _record_in_episodic_ledger(
        self, hyp, rule_id: str, category: str,
    ) -> None:
        """Registra en el EpisodicLedger."""
        try:
            from core.learn.episodic import get_ledger, EpisodicEvent
            ledger = get_ledger()
            ledger.append(EpisodicEvent(
                event_type="INTEGRATION_COMPLETED",
                summary=(
                    f"Regla {rule_id} creada desde hipótesis {hyp.id}: "
                    f"{hyp.description[:80]}"
                ),
                metadata={
                    "rule_id": rule_id,
                    "hypothesis_id": hyp.id,
                    "confidence": hyp.confidence,
                    "category": category,
                    "evidence_count": len(hyp.evidence),
                    "pipeline": "learning_cycle",
                },
            ))
        except Exception as exc:
            logger.warning("EpisodicLedger recording error: %s", exc)

    def _record_in_audit_ledger(
        self, hyp, rule_id: str, category: str,
    ) -> None:
        """Registra en el audit_ledger."""
        try:
            from core import audit_ledger
            audit_ledger.record(
                action="learning_cycle:rule_integrated",
                actor="learning_cycle",
                role="system",
                decision="approved",
                reason=(
                    f"Hipótesis {hyp.id} confirmada con "
                    f"confianza {hyp.confidence:.2f}"
                ),
                metadata={
                    "rule_id": rule_id,
                    "hypothesis_id": hyp.id,
                    "category": category,
                    "confidence": hyp.confidence,
                },
            )
        except Exception as exc:
            logger.warning("audit_ledger recording error: %s", exc)

    # =====================================================================
    # Status
    # =====================================================================

    def stats(self) -> Dict[str, Any]:
        return {
            "total_integrations": self._total_integrations,
            "total_rules_created": self._total_rules_created,
            "total_rejected": self._total_rejected,
            "success_rate": (
                round(
                    self._total_rules_created
                    / max(self._total_integrations + self._total_rejected, 1),
                    4,
                )
            ),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_integrator: Optional[LearningIntegrator] = None


def get_learning_integrator() -> LearningIntegrator:
    """Obtener la instancia singleton del LearningIntegrator."""
    global _integrator
    if _integrator is None:
        _integrator = LearningIntegrator()
    return _integrator


def reset_learning_integrator() -> None:
    """Reset para testing."""
    global _integrator
    _integrator = None
