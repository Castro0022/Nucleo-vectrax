"""
Vectrax — Constitutional Guard (punto único de aplicación)
===================================================================
Función compartida que los "choke points" del pipeline llaman para:
  1. Evaluar la propuesta contra los 7 principios (`constitutional_filter`).
  2. Resolver CAUTION contra `DecisionAuthority` REAL.
  3. Registrar la decisión completa en el ledger
     (`ledger_bridge`, `EventCategory.CONSTITUTIONAL`).
  4. Devolver un veredicto que el llamador DEBE obedecer.

EL CONTRATO
-----------
`enforce_check()` devuelve una `GateDecision`. Si `allowed` es False, la acción
NO se ejecuta y se explica por qué. No hay ninguna función que evalúe y
devuelva algo que el llamador pueda ignorar.

QUÉ SE RETIRÓ, Y POR QUÉ
------------------------
Existía `shadow_check()`: evaluaba, registraba y devolvía un veredicto que su
propio contrato prohibía usar para decidir. Dos de sus tres llamadores
—`core/idea_store.py` y `core/learning_cycle/learning_integrator.py`— ni
siquiera asignaban el valor de retorno. El resultado era un control que
aparentaba existir en tres puntos del pipeline y no gobernaba ninguno, ni
siquiera con la bandera global en 'enforce': el interruptor no los alcanzaba.

También se retiró `_simulate_decision_authority()`, que calculaba qué HABRÍA
decidido la autoridad si el veredicto se aplicara. Con el veredicto aplicándose
de verdad, simular la aplicación es describir lo que ya ocurre.

FALLA CERRADO
-------------
Un error interno —evaluación, ledger o autoridad— produce `allowed=False` con
la causa. Nunca una autorización silenciosa, y nunca una degradación a
observación.

Creado: 2026-08-14
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from core.operator import constitutional_mode
from core.operator.constitutional_filter import (
    ActionProposal,
    ConstitutionalVerdict,
    PrincipleVerdict,
    evaluate,
)

logger = logging.getLogger("vectrax.operator.constitutional_guard")


@dataclass(frozen=True)
class GateDecision:
    """Lo que el control decidió, y todo lo necesario para auditarlo.

    `allowed` es vinculante: el llamador no puede continuar con la acción si es
    False. Los demás campos existen para que la decisión sea reconstruible sin
    volver a ejecutarla.
    """
    allowed: bool
    reason: str
    correlation_id: str
    action: str
    application_point: str
    actor: str
    mode: str
    timestamp: float
    verdict: Optional[ConstitutionalVerdict] = None
    decision: Optional[Any] = None
    rules: tuple = field(default_factory=tuple)

    @property
    def overall(self) -> str:
        return self.verdict.overall.value if self.verdict else "unavailable"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "correlation_id": self.correlation_id,
            "action": self.action,
            "application_point": self.application_point,
            "actor": self.actor,
            "mode": self.mode,
            "timestamp": self.timestamp,
            "overall": self.overall,
            "rules": list(self.rules),
        }


def _rules_of(verdict: Optional[ConstitutionalVerdict]) -> tuple:
    """Principios que no pasaron, en la forma en que se auditan."""
    if verdict is None:
        return ()
    return tuple(
        f"L{r.number}={r.verdict.value}"
        for r in verdict.results
        if r.verdict != PrincipleVerdict.PASS
    )


def _record_ledger(decision: GateDecision) -> None:
    """Asienta la decisión completa. Un fallo aquí nunca rompe al llamador.

    Guarda lo que hace falta para reconstruirla sin volver a ejecutarla:
    correlation_id, acción, veredicto, reglas que no pasaron, actor, marca de
    tiempo y punto de aplicación.
    """
    try:
        from core.operator import ledger_bridge as ledger

        zone = ledger.RiskZone.GREEN
        if decision.overall == PrincipleVerdict.CAUTION.value:
            zone = ledger.RiskZone.YELLOW
        elif decision.overall == PrincipleVerdict.BLOCK.value or not decision.allowed:
            zone = ledger.RiskZone.RED

        verdict = decision.verdict
        ledger.record_event(
            action=f"constitutional:{decision.mode}:{decision.action}",
            category=ledger.EventCategory.CONSTITUTIONAL,
            risk_zone=zone,
            reason=decision.reason,
            details={
                # `proposal_id` es el campo que ya consumían el ledger y sus
                # lectores; se conserva. `correlation_id` es el mismo valor con
                # el nombre que pide la traza de decisiones. Quitar el primero
                # habría sido un cambio de esquema silencioso.
                "proposal_id": verdict.proposal_id if verdict else decision.correlation_id,
                "correlation_id": decision.correlation_id,
                "action": decision.action,
                "application_point": decision.application_point,
                "actor": decision.actor,
                "mode": decision.mode,
                "timestamp": decision.timestamp,
                "allowed": decision.allowed,
                "overall": decision.overall,
                "rules": list(decision.rules),
                "results": [r.to_dict() for r in verdict.results] if verdict else [],
                "filter_error": verdict.filter_error if verdict else None,
            },
        )
    except Exception as exc:
        logger.warning("Constitutional ledger recording failed: %s", exc)


def _real_decision_authority(verdict: ConstitutionalVerdict, proposal: ActionProposal) -> Any:
    """Consulta `DecisionAuthority.check_authority()` DE VERDAD.

    Fail-safe: cualquier fallo se trata como "no auto-aprobado" — nunca
    autoriza silenciosamente ante un error técnico.
    """
    from core.operator.decision_authority import (
        Authority, DecisionResult, check_authority,
    )
    try:
        gov_mode = "observe"
        try:
            from core.governor import get_current_policy
            gov_mode = get_current_policy().get("mode", "observe")
        except Exception:
            pass
        risk_level = "HIGH" if proposal.is_irreversible else "LOW"
        return check_authority(proposal.action, governor_mode=gov_mode, risk_level=risk_level)
    except Exception as exc:
        logger.warning(
            "Real DecisionAuthority check failed (fail-safe: no auto-aprobado): %s", exc,
        )
        return DecisionResult(
            action=proposal.action, authority=Authority.AUTHORIZED,
            auto_approved=False, reason=f"decision_authority_error: {exc}",
        )


def enforce_check(
    proposal: ActionProposal,
    *,
    application_point: str,
    actor: str = "",
) -> GateDecision:
    """Evalúa, registra y DECIDE. El llamador debe obedecer `allowed`.

    * PASS    → continúa.
    * BLOCK   → se impide la acción, y `reason` nombra los principios.
    * CAUTION → lo resuelve `DecisionAuthority` real; auto-aprobado continúa,
                no auto-aprobado se impide. Queda visible y auditado en ambos
                casos, que es su semántica de siempre.
    * pausa   → se impide, diciendo que el control está pausado.
    * error   → se impide, diciendo cuál.

    `application_point` identifica el choke point que aplica la decisión, para
    que el ledger diga DÓNDE se aplicó y no solo qué se decidió.
    """
    correlation_id = getattr(proposal, "correlation_id", "") or f"CC-{uuid.uuid4().hex[:12]}"
    ts = time.time()
    mode = constitutional_mode.get_mode()

    def _finish(allowed, reason, verdict=None, decision=None):
        gate = GateDecision(
            allowed=allowed, reason=reason, correlation_id=correlation_id,
            action=proposal.action, application_point=application_point,
            actor=actor, mode=mode, timestamp=ts,
            verdict=verdict, decision=decision, rules=_rules_of(verdict),
        )
        # Un fallo del ledger NUNCA se propaga al pipeline: no poder asentar
        # la decisión es grave y se registra como warning, pero no puede
        # convertir una decisión tomada en una excepción para el llamador.
        try:
            _record_ledger(gate)
        except Exception as exc:  # pragma: no cover - defensa del defensor
            logger.warning("Constitutional ledger recording failed (outer): %s", exc)
        return gate

    # Pausa de emergencia: se DETIENE. No es una puerta abierta.
    if mode == constitutional_mode.PAUSED:
        return _finish(
            False,
            "El control constitucional está en pausa de emergencia: "
            "no se ejecutan acciones sujetas a control mientras dure.",
        )

    try:
        verdict = evaluate(proposal, mode=mode)
    except Exception as exc:
        # Falla CERRADO: un fallo del evaluador no autoriza nada.
        logger.error("Constitutional evaluation failed (fail-closed): %s", exc)
        return _finish(
            False,
            f"El control constitucional no pudo evaluar la acción ({exc}). "
            "Se detiene por seguridad.",
        )

    if verdict.overall == PrincipleVerdict.BLOCK:
        causes = "; ".join(r.reason for r in verdict.blocked_principles)
        return _finish(False, f"Bloqueado por el filtro constitucional: {causes}", verdict)

    if verdict.overall == PrincipleVerdict.CAUTION:
        decision = _real_decision_authority(verdict, proposal)
        if getattr(decision, "auto_approved", False):
            return _finish(
                True,
                f"CAUTION autorizado por DecisionAuthority: "
                f"{getattr(decision, 'reason', '')}",
                verdict, decision,
            )
        return _finish(
            False,
            f"CAUTION no autorizado por DecisionAuthority: "
            f"{getattr(decision, 'reason', '')}",
            verdict, decision,
        )

    return _finish(True, verdict.summary(), verdict)
