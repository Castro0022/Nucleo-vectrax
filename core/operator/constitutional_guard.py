"""
Vectrax — Constitutional Guard (punto único de instrumentación)
===================================================================
Función compartida que los "choke points" del pipeline llaman para:
  1. Evaluar la propuesta contra los 7 principios (`constitutional_filter`).
  2. Simular qué decidiría `DecisionAuthority` con ese veredicto (solo para
     el reporte comparativo de la Fase 1 — en shadow mode NUNCA se aplica).
  3. Registrar todo en el ledger existente (`ledger_bridge`,
     `EventCategory.CONSTITUTIONAL`).

Contrato de Fase 1 (shadow mode) — MUY IMPORTANTE:
  `shadow_check()` es de solo observación. Su valor de retorno puede
  inspeccionarse (p.ej. en tests), pero NINGÚN call site del pipeline debe
  usarlo para decidir nada mientras el modo global sea "shadow". La función
  en sí nunca lanza, nunca bloquea, nunca modifica el estado del caller.

Creado: 2026-08-14
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from core.operator import constitutional_mode
from core.operator.constitutional_filter import (
    ActionProposal,
    ConstitutionalVerdict,
    PrincipleVerdict,
    evaluate,
)

logger = logging.getLogger("vectrax.operator.constitutional_guard")


def _simulate_decision_authority(verdict: ConstitutionalVerdict, proposal: ActionProposal) -> Dict[str, Any]:
    """
    Qué decidiría DecisionAuthority.check_authority() si el veredicto
    constitucional se aplicara de verdad. Solo informativo en shadow mode
    — nunca se ejecuta como decisión real desde aquí.
    """
    try:
        from core.operator.decision_authority import check_authority
        from core.governor import get_current_policy

        gov_mode = "observe"
        try:
            gov_mode = get_current_policy().get("mode", "observe")
        except Exception:
            pass

        if verdict.overall == PrincipleVerdict.BLOCK:
            return {
                "would_execute": False,
                "authority": "blocked_by_constitution",
                "reason": "; ".join(r.reason for r in verdict.blocked_principles),
            }

        risk_level = "HIGH" if proposal.is_irreversible else "LOW"
        decision = check_authority(proposal.action, governor_mode=gov_mode, risk_level=risk_level)

        if verdict.overall == PrincipleVerdict.CAUTION:
            # CAUTION: solo reversible/bajo riesgo podría continuar, y solo
            # si DecisionAuthority ya lo autoriza automáticamente.
            would_execute = (not proposal.is_irreversible) and decision.auto_approved
            return {
                "would_execute": would_execute,
                "authority": decision.authority.value,
                "reason": (
                    f"CAUTION constitucional + DecisionAuthority={decision.authority.value}: "
                    f"{decision.reason}"
                ),
            }

        # overall == PASS
        return {
            "would_execute": decision.auto_approved,
            "authority": decision.authority.value,
            "reason": decision.reason,
        }
    except Exception as exc:
        logger.debug("Decision authority simulation failed: %s", exc)
        return {"would_execute": False, "authority": "unknown", "reason": f"simulation_error: {exc}"}


def _record_ledger(verdict: ConstitutionalVerdict, proposal: ActionProposal, simulated: Dict[str, Any]) -> None:
    try:
        from core.operator import ledger_bridge as ledger

        zone = ledger.RiskZone.GREEN
        if verdict.overall == PrincipleVerdict.CAUTION:
            zone = ledger.RiskZone.YELLOW
        elif verdict.overall == PrincipleVerdict.BLOCK:
            zone = ledger.RiskZone.RED

        ledger.record_event(
            action=f"constitutional:{verdict.mode}:{proposal.action}",
            category=ledger.EventCategory.CONSTITUTIONAL,
            risk_zone=zone,
            reason=verdict.summary(),
            details={
                "proposal_id": verdict.proposal_id,
                "mode": verdict.mode,
                "overall": verdict.overall.value,
                "results": [r.to_dict() for r in verdict.results],
                "filter_error": verdict.filter_error,
                "simulated_decision_authority": simulated,
            },
        )
    except Exception as exc:
        # Registrar el fallo de ledger nunca debe romper al caller.
        logger.warning("Constitutional ledger recording failed: %s", exc)


def shadow_check(proposal: ActionProposal) -> ConstitutionalVerdict:
    """
    Evalúa + registra. SOLO OBSERVACIÓN mientras el modo global sea
    'shadow' — el caller NUNCA debe ramificar su comportamiento sobre el
    valor de retorno en esta fase. Nunca lanza: un fallo de infraestructura
    (p.ej. el ledger no disponible) nunca debe propagarse al pipeline.
    """
    mode = constitutional_mode.get_mode()
    verdict = evaluate(proposal, mode=mode)
    try:
        simulated = _simulate_decision_authority(verdict, proposal)
    except Exception as exc:
        logger.debug("Decision authority simulation failed (outer): %s", exc)
        simulated = {"would_execute": False, "authority": "unknown", "reason": f"simulation_error: {exc}"}
    try:
        _record_ledger(verdict, proposal, simulated)
    except Exception as exc:
        logger.warning("Constitutional ledger recording failed (outer): %s", exc)
    return verdict
