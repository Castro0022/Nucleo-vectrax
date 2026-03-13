"""
Vectrax Core — Approval Gate
================================
Puerta centralizada de aprobación del operador. Unifica los dos
mecanismos existentes:

  - cognition.reasoning.human_review_gate (propuestas cognitivas SQLite)
  - interface_layer.human_confirmation (confirmación interactiva de riesgo)

bajo una interfaz única que integra el RiskBridge y el ledger.

Flujo:
  1. Recibe propuesta de acción
  2. Evalúa riesgo via RiskBridge
  3. Según decisión:
     - ALLOW → auto-aprobar (solo en modos supervised/autonomous)
     - ALLOW_WITH_REVIEW → encolar para revisión del creador
     - ESCALATE → encolar con prioridad alta
     - BLOCK → rechazar y registrar

Principio P-004: "Ninguna acción crítica puede ejecutarse sin
autorización explícita del creador."

Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from core.operator.identity import OperatorMode
from core.operator.risk_bridge import (
    OperatorDecision,
    RiskBridge,
    RiskEvaluation,
    get_risk_bridge,
)

logger = logging.getLogger("vectrax.operator.approval_gate")


# ---------------------------------------------------------------------------
# Estado de aprobación
# ---------------------------------------------------------------------------

class ApprovalStatus(str, Enum):
    """Estado de una solicitud de aprobación."""
    AUTO_APPROVED = "auto_approved"   # Bajo riesgo, aprobado por política
    PENDING = "pending"               # Esperando decisión del creador
    APPROVED = "approved"             # Aprobado por el creador
    REJECTED = "rejected"             # Rechazado por el creador
    BLOCKED = "blocked"               # Bloqueado por política de riesgo
    EXPIRED = "expired"               # Expiró sin decisión


# ---------------------------------------------------------------------------
# Solicitud de aprobación
# ---------------------------------------------------------------------------

@dataclass
class ApprovalRequest:
    """Solicitud de aprobación para una acción."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    action: str = ""
    description: str = ""
    path: str = ""
    op_type: str = "read_only"
    topic: str = "general"
    status: ApprovalStatus = ApprovalStatus.PENDING
    risk_evaluation: Optional[RiskEvaluation] = None
    created_at: float = field(default_factory=time.time)
    decided_at: Optional[float] = None
    decided_by: str = ""
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "description": self.description,
            "path": self.path,
            "op_type": self.op_type,
            "status": self.status.value,
            "risk_evaluation": self.risk_evaluation.to_dict() if self.risk_evaluation else None,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Approval Gate
# ---------------------------------------------------------------------------

class ApprovalGate:
    """
    Puerta centralizada de aprobación del operador.

    Usage::

        gate = ApprovalGate()
        request = gate.submit(
            action="modify_file",
            path="core/governor.py",
            op_type="autopatch",
            description="Fix error handling in evaluate()",
        )
        if request.status == ApprovalStatus.PENDING:
            # Esperar aprobación del creador
            ...
        elif request.status == ApprovalStatus.AUTO_APPROVED:
            # Ejecutar
            ...
    """

    def __init__(
        self,
        operator_mode: str = OperatorMode.GUIDED.value,
        risk_bridge: Optional[RiskBridge] = None,
    ) -> None:
        self._mode = operator_mode
        self._risk_bridge = risk_bridge or get_risk_bridge()
        self._pending: Dict[str, ApprovalRequest] = {}
        self._history: List[ApprovalRequest] = []
        self._on_submit_callbacks: List[Callable[[ApprovalRequest], None]] = []

    @property
    def operator_mode(self) -> str:
        return self._mode

    @operator_mode.setter
    def operator_mode(self, mode: str) -> None:
        self._mode = mode

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------

    def submit(
        self,
        *,
        action: str,
        description: str = "",
        path: str = "",
        op_type: str = "read_only",
        topic: str = "general",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ApprovalRequest:
        """
        Submit una acción para aprobación.

        1. Evalúa riesgo via RiskBridge
        2. Aplica política según modo del operador
        3. Retorna ApprovalRequest con estado

        Returns:
            ApprovalRequest con status indicando el resultado.
        """
        # Evaluar riesgo
        risk_eval = self._risk_bridge.evaluate_action(
            path=path,
            op_type=op_type,
            topic=topic,
            operator_mode=self._mode,
        )

        # Crear request
        request = ApprovalRequest(
            action=action,
            description=description,
            path=path,
            op_type=op_type,
            topic=topic,
            risk_evaluation=risk_eval,
            metadata=metadata or {},
        )

        # Aplicar política
        request = self._apply_policy(request, risk_eval)

        # Almacenar
        if request.status == ApprovalStatus.PENDING:
            self._pending[request.id] = request
        self._history.append(request)

        # Callbacks
        for cb in self._on_submit_callbacks:
            try:
                cb(request)
            except Exception as exc:
                logger.debug("Submit callback error: %s", exc)

        # Registrar en ledger
        self._log_to_ledger(request, "submitted")

        logger.info(
            "Approval request %s: %s → %s | risk=%.2f",
            request.id, action, request.status.value,
            risk_eval.risk_score,
        )

        return request

    # ------------------------------------------------------------------
    # Approve / Reject (solo creador)
    # ------------------------------------------------------------------

    def approve(self, request_id: str, *, by: str = "creator") -> Optional[ApprovalRequest]:
        """Aprobar una solicitud pendiente."""
        request = self._pending.pop(request_id, None)
        if request is None:
            logger.warning("Approval request %s not found in pending", request_id)
            return None

        request.status = ApprovalStatus.APPROVED
        request.decided_at = time.time()
        request.decided_by = by
        self._log_to_ledger(request, "approved")
        logger.info("Approved: %s by %s", request_id, by)
        return request

    def reject(
        self,
        request_id: str,
        *,
        by: str = "creator",
        reason: str = "",
    ) -> Optional[ApprovalRequest]:
        """Rechazar una solicitud pendiente."""
        request = self._pending.pop(request_id, None)
        if request is None:
            logger.warning("Approval request %s not found in pending", request_id)
            return None

        request.status = ApprovalStatus.REJECTED
        request.decided_at = time.time()
        request.decided_by = by
        request.reason = reason
        self._log_to_ledger(request, "rejected")
        logger.info("Rejected: %s by %s — %s", request_id, by, reason)
        return request

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def get_pending(self) -> List[ApprovalRequest]:
        """Solicitudes pendientes ordenadas por riesgo descendente."""
        pending = list(self._pending.values())
        pending.sort(
            key=lambda r: r.risk_evaluation.risk_score if r.risk_evaluation else 0,
            reverse=True,
        )
        return pending

    def get_request(self, request_id: str) -> Optional[ApprovalRequest]:
        """Buscar una solicitud por ID (pendiente o histórica)."""
        if request_id in self._pending:
            return self._pending[request_id]
        for r in reversed(self._history):
            if r.id == request_id:
                return r
        return None

    def get_history(self, limit: int = 50) -> List[ApprovalRequest]:
        """Historial de solicitudes (más recientes primero)."""
        return list(reversed(self._history[-limit:]))

    def pending_count(self) -> int:
        return len(self._pending)

    def on_submit(self, callback: Callable[[ApprovalRequest], None]) -> None:
        """Registrar callback para nuevas solicitudes."""
        self._on_submit_callbacks.append(callback)

    def status(self) -> Dict[str, Any]:
        """Estado de la puerta de aprobación."""
        return {
            "operator_mode": self._mode,
            "pending_count": len(self._pending),
            "total_history": len(self._history),
            "pending_ids": list(self._pending.keys()),
        }

    # ------------------------------------------------------------------
    # Política interna
    # ------------------------------------------------------------------

    def _apply_policy(
        self,
        request: ApprovalRequest,
        risk_eval: RiskEvaluation,
    ) -> ApprovalRequest:
        """Aplica la política del operador según el modo y la decisión de riesgo."""
        decision = risk_eval.decision

        # BLOCK → rechazado automáticamente
        if decision == OperatorDecision.BLOCK:
            request.status = ApprovalStatus.BLOCKED
            request.reason = risk_eval.reason
            return request

        # GUIDED → todo requiere aprobación
        if self._mode == OperatorMode.GUIDED.value:
            request.status = ApprovalStatus.PENDING
            return request

        # SUPERVISED → auto-aprobar ALLOW, pending para REVIEW/ESCALATE
        if self._mode == OperatorMode.SUPERVISED.value:
            if decision == OperatorDecision.ALLOW:
                request.status = ApprovalStatus.AUTO_APPROVED
                request.decided_by = "policy:supervised"
                request.decided_at = time.time()
            else:
                request.status = ApprovalStatus.PENDING
            return request

        # AUTONOMOUS → auto-aprobar ALLOW y REVIEW, pending para ESCALATE
        if self._mode == OperatorMode.AUTONOMOUS.value:
            if decision in (OperatorDecision.ALLOW, OperatorDecision.ALLOW_WITH_REVIEW):
                request.status = ApprovalStatus.AUTO_APPROVED
                request.decided_by = "policy:autonomous"
                request.decided_at = time.time()
            else:
                request.status = ApprovalStatus.PENDING
            return request

        # Default → pending
        request.status = ApprovalStatus.PENDING
        return request

    # ------------------------------------------------------------------
    # Ledger integration
    # ------------------------------------------------------------------

    @staticmethod
    def _log_to_ledger(request: ApprovalRequest, event: str) -> None:
        """Registra en el audit ledger via ledger_bridge."""
        try:
            from core.operator.ledger_bridge import record_event, EventCategory, RiskZone

            risk_score = request.risk_evaluation.risk_score if request.risk_evaluation else 0
            zone = RiskZone.GREEN if risk_score < 0.4 else (
                RiskZone.YELLOW if risk_score < 0.7 else RiskZone.RED
            )

            record_event(
                action=f"approval:{event}:{request.action}",
                category=EventCategory.GOVERNANCE,
                risk_zone=zone,
                reason=f"{request.status.value} | risk={risk_score:.2f}",
                details={
                    "request_id": request.id,
                    "action": request.action,
                    "path": request.path,
                    "status": request.status.value,
                    "risk_score": round(risk_score, 4),
                },
            )
        except Exception as exc:
            logger.debug("Ledger logging failed: %s", exc)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_gate: Optional[ApprovalGate] = None


def get_approval_gate(
    operator_mode: str = OperatorMode.GUIDED.value,
) -> ApprovalGate:
    """Obtener la instancia singleton de la puerta de aprobación."""
    global _gate
    if _gate is None:
        _gate = ApprovalGate(operator_mode=operator_mode)
    return _gate
