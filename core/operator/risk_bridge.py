"""
Vectrax Core — Risk Bridge
==============================
Unifica los tres motores de gobernanza existentes bajo una interfaz
coherente para el operador:

  - core.governor       → modo operativo (observe/act/cautious/recover)
  - core.risk_engine    → evaluación probabilística de 6 señales
  - core.autonomy_policy → clasificación de zonas (SACRED/SEMI/FLEXIBLE)

El bridge NO reimplementa — delega, combina y enriquece con contexto
del operador (modo, identidad, principios).

Principio P-004: "Ninguna acción crítica puede ejecutarse sin
autorización explícita del creador."

Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from core.operator.identity import IDENTITY, OperatorMode

logger = logging.getLogger("vectrax.operator.risk_bridge")


# ---------------------------------------------------------------------------
# Decisión unificada de riesgo
# ---------------------------------------------------------------------------

class OperatorDecision(str, Enum):
    """Decisión del operador sobre una acción propuesta."""
    ALLOW = "allow"                 # Permitir ejecución
    ALLOW_WITH_REVIEW = "review"    # Permitir pero requiere revisión humana
    BLOCK = "block"                 # Bloquear — riesgo demasiado alto
    ESCALATE = "escalate"           # Escalar al creador — zona amarilla/roja


@dataclass
class RiskEvaluation:
    """Evaluación de riesgo unificada del operador."""
    # Decisión
    decision: OperatorDecision
    reason: str

    # Componentes
    governor_mode: str = "unknown"
    governor_autopatch: bool = False
    risk_score: float = 0.0
    risk_level: str = "UNKNOWN"
    zone: str = "UNKNOWN"
    is_hard_limited: bool = False

    # Contexto del operador
    operator_mode: str = "guided"
    requires_creator_approval: bool = True

    # Trazabilidad
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    signals: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "governor_mode": self.governor_mode,
            "governor_autopatch": self.governor_autopatch,
            "risk_score": round(self.risk_score, 4),
            "risk_level": self.risk_level,
            "zone": self.zone,
            "is_hard_limited": self.is_hard_limited,
            "operator_mode": self.operator_mode,
            "requires_creator_approval": self.requires_creator_approval,
            "timestamp": self.timestamp,
            "signal_count": len(self.signals),
        }


# ---------------------------------------------------------------------------
# Risk Bridge
# ---------------------------------------------------------------------------

class RiskBridge:
    """
    Puente unificado entre el operador y los motores de gobernanza.

    Usage::

        bridge = RiskBridge()
        eval = bridge.evaluate_action(
            path="core/governor.py",
            op_type="autopatch",
            topic="code",
        )
        if eval.decision == OperatorDecision.BLOCK:
            # No ejecutar
            ...
    """

    def __init__(self) -> None:
        self._governor = None
        self._risk_engine = None
        self._autonomy = None

    # -- Lazy loaders (evitar imports circulares) --------------------------

    def _get_governor(self):
        if self._governor is None:
            try:
                from core import governor
                self._governor = governor
            except ImportError:
                logger.warning("Governor not available")
        return self._governor

    def _get_risk_engine(self):
        if self._risk_engine is None:
            try:
                from core.risk_engine import get_risk_engine
                self._risk_engine = get_risk_engine()
            except ImportError:
                logger.warning("RiskEngine not available")
        return self._risk_engine

    def _get_autonomy(self):
        if self._autonomy is None:
            try:
                from core import autonomy_policy
                self._autonomy = autonomy_policy
            except ImportError:
                logger.warning("AutonomyPolicy not available")
        return self._autonomy

    # ------------------------------------------------------------------
    # Evaluación principal
    # ------------------------------------------------------------------

    def evaluate_action(
        self,
        *,
        path: str = "",
        op_type: str = "read_only",
        topic: str = "general",
        operator_mode: Optional[str] = None,
    ) -> RiskEvaluation:
        """
        Evalúa una acción propuesta combinando los tres motores.

        Pipeline:
          1. Verificar hard limits (autonomy_policy)
          2. Clasificar zona (autonomy_policy)
          3. Evaluar riesgo probabilístico (risk_engine)
          4. Consultar modo del gobernador (governor)
          5. Aplicar reglas del operador según modo
          6. Emitir decisión unificada

        Args:
            path: Ruta del archivo afectado (relativa al proyecto)
            op_type: Tipo de operación (autopatch, workflow, etc.)
            topic: Tema de la operación (code, health, etc.)
            operator_mode: Override del modo del operador

        Returns:
            RiskEvaluation con decisión, razón y componentes.
        """
        op_mode = operator_mode or IDENTITY.initial_mode
        eval_result = RiskEvaluation(
            decision=OperatorDecision.BLOCK,
            reason="",
            operator_mode=op_mode,
        )

        # 1. Hard limits
        autonomy = self._get_autonomy()
        if autonomy and path:
            if autonomy.is_hard_limited(path):
                eval_result.is_hard_limited = True
                eval_result.decision = OperatorDecision.BLOCK
                eval_result.reason = f"Hard limited path: {path}"
                eval_result.requires_creator_approval = True
                logger.info("BLOCKED (hard limit): %s", path)
                return eval_result

        # 2. Zona de autonomía
        if autonomy and path:
            zone = autonomy.classify_path(path)
            eval_result.zone = zone.value
        else:
            eval_result.zone = "UNKNOWN"

        # 3. Riesgo probabilístico
        risk_engine = self._get_risk_engine()
        if risk_engine:
            try:
                from core.risk_engine import OperationContext
                ctx = OperationContext(op_type=op_type, topic=topic)
                assessment = risk_engine.assess(ctx)
                eval_result.risk_score = assessment.risk_score
                eval_result.risk_level = assessment.risk_level.value
                eval_result.signals = [
                    {"name": s.name, "value": round(s.value, 4), "weight": round(s.weight, 4)}
                    for s in assessment.signals
                ]
            except Exception as exc:
                logger.debug("Risk engine error: %s", exc)

        # 4. Gobernador
        governor = self._get_governor()
        if governor:
            try:
                policy = governor.get_current_policy()
                eval_result.governor_mode = policy.get("mode", "unknown")
                eval_result.governor_autopatch = policy.get("autopatch_allowed", False)
            except Exception as exc:
                logger.debug("Governor error: %s", exc)

        # 5. Decisión unificada
        eval_result.decision, eval_result.reason = self._decide(eval_result, op_mode)

        # 6. En modo GUIDED, TODO requiere aprobación del creador
        if op_mode == OperatorMode.GUIDED.value:
            eval_result.requires_creator_approval = True
            if eval_result.decision == OperatorDecision.ALLOW:
                eval_result.decision = OperatorDecision.ALLOW_WITH_REVIEW
                eval_result.reason += " | modo guiado: requiere revisión"

        logger.info(
            "Risk evaluation: %s | path=%s | risk=%.2f | zone=%s | decision=%s",
            op_type, path or "(none)", eval_result.risk_score,
            eval_result.zone, eval_result.decision.value,
        )

        return eval_result

    # ------------------------------------------------------------------
    # Consultas rápidas
    # ------------------------------------------------------------------

    def get_governor_status(self) -> Dict[str, Any]:
        """Estado actual del gobernador."""
        governor = self._get_governor()
        if governor:
            try:
                return governor.get_current_policy()
            except Exception:
                pass
        return {"mode": "unknown", "error": "governor not available"}

    def is_action_safe(self, path: str, op_type: str = "read_only") -> bool:
        """Quick check: ¿es segura esta acción?"""
        eval_result = self.evaluate_action(path=path, op_type=op_type)
        return eval_result.decision in (
            OperatorDecision.ALLOW,
            OperatorDecision.ALLOW_WITH_REVIEW,
        )

    # ------------------------------------------------------------------
    # Lógica de decisión interna
    # ------------------------------------------------------------------

    @staticmethod
    def _decide(
        eval_result: RiskEvaluation,
        operator_mode: str,
    ) -> tuple:
        """
        Aplica la matriz de decisión:

        SACRED_CORE → siempre ESCALATE
        risk ≥ 0.85 (CRITICAL) → BLOCK
        risk ≥ 0.60 (HIGH) → ESCALATE
        governor=recover → BLOCK (excepto read_only)
        SEMI_SAFE → ALLOW_WITH_REVIEW
        FLEXIBLE + risk < 0.30 → ALLOW
        Resto → ALLOW_WITH_REVIEW
        """
        zone = eval_result.zone
        risk = eval_result.risk_score
        risk_level = eval_result.risk_level
        gov_mode = eval_result.governor_mode

        # SACRED_CORE siempre escala al creador
        if zone == "SACRED_CORE":
            return (
                OperatorDecision.ESCALATE,
                f"Sacred core zone — creator approval required",
            )

        # CRITICAL risk → bloquear
        if risk >= 0.85 or risk_level == "CRITICAL":
            return (
                OperatorDecision.BLOCK,
                f"Critical risk ({risk:.2f}) — blocked",
            )

        # HIGH risk → escalar
        if risk >= 0.60 or risk_level == "HIGH":
            return (
                OperatorDecision.ESCALATE,
                f"High risk ({risk:.2f}) — escalated to creator",
            )

        # Governor en recover → bloquear acciones no-lectura
        if gov_mode == "recover":
            return (
                OperatorDecision.BLOCK,
                "Governor in recover mode — actions blocked",
            )

        # SEMI_SAFE → revisar
        if zone == "SEMI_SAFE":
            return (
                OperatorDecision.ALLOW_WITH_REVIEW,
                f"Semi-safe zone, risk={risk:.2f} — review required",
            )

        # FLEXIBLE + bajo riesgo → permitir
        if zone == "FLEXIBLE" and risk < 0.30:
            return (
                OperatorDecision.ALLOW,
                f"Flexible zone, low risk ({risk:.2f}) — allowed",
            )

        # Default → revisar
        return (
            OperatorDecision.ALLOW_WITH_REVIEW,
            f"Default policy: risk={risk:.2f}, zone={zone} — review",
        )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_bridge: Optional[RiskBridge] = None


def get_risk_bridge() -> RiskBridge:
    """Obtener la instancia singleton del risk bridge."""
    global _bridge
    if _bridge is None:
        _bridge = RiskBridge()
    return _bridge
