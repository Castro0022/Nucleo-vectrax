"""
Vectrax — Pre-Execution Constitutional Gate (P0)
=====================================================================
Garantiza que ninguna llamada externa real de las 4 fronteras gobernadas
(LLM, Online, Places, Market) ocurra sin pasar antes por una evaluación
Kybalion (7 principios) — el mismo motor que ya usa el gate `respond`
(`external_gateway.py::_constitutional_gate`, que permanece intacto y sin
modificar).

Este módulo NO modifica `core.operator.constitutional_guard` ni
`core.operator.constitutional_mode` — los reutiliza/importa. El modo
(SHADOW/ENFORCE) es independiente POR FRONTERA (`core.operator.boundary_mode`),
nunca un interruptor global.

Contrato — `authorize()` NUNCA LANZA. Siempre retorna un `GateDecision`
como valor. El registro en el ledger para cualquier decisión (incluido
BLOCK) ocurre de forma SÍNCRONA dentro de `authorize()`, antes de que
retorne — es un side-effect ya completado cuando el control vuelve al
caller. Un `try/except` posterior en el caller nunca puede perder esa
entrada de ledger ni confundir un BLOCK real con un fallo silencioso,
porque BLOCK jamás se señaliza vía excepción.

Creado: 2026-09-17
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.operator import boundary_mode
from core.operator.constitutional_filter import ActionProposal, PrincipleVerdict, evaluate
from core.operator.execution_context import ExecutionContext

logger = logging.getLogger("vectrax.operator.pre_execution_gate")


# ---------------------------------------------------------------------------
# Execution / decision tags — contrato cerrado
# ---------------------------------------------------------------------------

EXECUTE = "EXECUTE"
WOULD_BLOCK_MISSING_CONTEXT = "WOULD_BLOCK_MISSING_CONTEXT"
BLOCKED_MISSING_CONTEXT = "BLOCKED_MISSING_CONTEXT"
BLOCKED = "BLOCKED"
SKIPPED_EMPTY_AUTHORIZED_SET = "SKIPPED_EMPTY_AUTHORIZED_SET"

# Tags para los que el caller SÍ debe ejecutar el I/O real.
_PROCEED_TAGS = frozenset({EXECUTE, WOULD_BLOCK_MISSING_CONTEXT})
# Tags para los que el caller NO debe ejecutar ningún I/O.
_BLOCKED_TAGS = frozenset({BLOCKED_MISSING_CONTEXT, BLOCKED, SKIPPED_EMPTY_AUTHORIZED_SET})

DECISION_PASS = "PASS"
DECISION_CAUTION = "CAUTION"
DECISION_BLOCK = "BLOCK"
DECISION_MISSING_CONTEXT = "MISSING_CONTEXT"
DECISION_ERROR = "ERROR"


@dataclass(frozen=True)
class GateDecision:
    """Resultado de `authorize()`. Nunca se construye directamente fuera de
    este módulo — los callers solo lo consumen."""
    boundary: str
    execution: str          # EXECUTE | WOULD_BLOCK_MISSING_CONTEXT | BLOCKED_MISSING_CONTEXT | BLOCKED | SKIPPED_EMPTY_AUTHORIZED_SET
    decision: str           # PASS | CAUTION | BLOCK | MISSING_CONTEXT | ERROR
    mode: str               # shadow | enforce
    authorized_targets: Optional[List[str]]
    correction_applied: bool = False
    reason: str = ""

    @property
    def should_execute(self) -> bool:
        """True si el caller debe proceder a ejecutar el I/O real."""
        return self.execution in _PROCEED_TAGS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "boundary": self.boundary,
            "execution": self.execution,
            "decision": self.decision,
            "mode": self.mode,
            "authorized_targets": self.authorized_targets,
            "correction_applied": self.correction_applied,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

def _record_ledger(
    boundary: str,
    gate_decision: GateDecision,
    *,
    action: str = "",
    correlation_id: str = "",
    verdict_summary: str = "",
) -> None:
    """Registra la decisión en el ledger existente. Nunca lanza — un fallo
    de infraestructura de logging nunca debe propagarse al pipeline."""
    try:
        from core.operator import ledger_bridge as ledger

        zone = ledger.RiskZone.GREEN
        if gate_decision.execution in _BLOCKED_TAGS:
            zone = ledger.RiskZone.RED if gate_decision.mode == boundary_mode.ENFORCE else ledger.RiskZone.YELLOW
        elif gate_decision.decision == DECISION_CAUTION:
            zone = ledger.RiskZone.YELLOW

        ledger.record_event(
            action=f"pre_execution:{boundary}:{gate_decision.execution.lower()}",
            category=ledger.EventCategory.CONSTITUTIONAL,
            risk_zone=zone,
            reason=gate_decision.reason or verdict_summary,
            details={
                "boundary": boundary,
                "boundary_action": action,
                "correlation_id": correlation_id,
                **gate_decision.to_dict(),
            },
        )
    except Exception as exc:
        logger.warning("pre_execution_gate: ledger recording failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# DecisionAuthority (solo para el sub-paso CAUTION, igual patrón que
# constitutional_guard._real_decision_authority)
# ---------------------------------------------------------------------------

def _consult_decision_authority(proposal: ActionProposal):
    from core.operator.decision_authority import Authority, DecisionResult, check_authority

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
            "pre_execution_gate: DecisionAuthority check failed (fail-safe: "
            "no auto-aprobado): %s", exc,
        )
        return DecisionResult(
            action=proposal.action, authority=Authority.AUTHORIZED,
            auto_approved=False, reason=f"decision_authority_error: {exc}",
        )


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def authorize(
    boundary: str,
    execution_context: Optional[ExecutionContext],
    *,
    requested_targets: Optional[List[str]] = None,
) -> GateDecision:
    """
    Punto único de autorización PRE-ejecución para una de las 4 fronteras
    gobernadas (`"llm"`, `"online"`, `"places"`, `"market"`).

    Args:
        boundary: uno de `boundary_mode.BOUNDARIES`.
        execution_context: contexto propagado desde el caller que conoce el
            origen real de la operación. `None` se trata como "ausente".
        requested_targets: SOLO para operaciones de fan-out (ej. proveedores
            LLM en `query_parallel`). `None` para operaciones de destino
            único — en ese caso `authorized_targets` en el resultado
            también será `None` mientras se ejecute, y `[]` si se bloquea.

    Returns:
        `GateDecision`. Nunca lanza.
    """
    try:
        return _authorize_impl(boundary, execution_context, requested_targets)
    except Exception as exc:  # pragma: no cover - red de seguridad final
        logger.error(
            "pre_execution_gate.authorize: fallo interno inesperado (bug — "
            "fail-safe EXECUTE para no romper comportamiento existente): %s",
            exc,
        )
        return GateDecision(
            boundary=boundary, execution=EXECUTE, decision=DECISION_ERROR,
            mode="unknown", authorized_targets=requested_targets,
            reason=f"gate_internal_error:{exc}",
        )


def _authorize_impl(
    boundary: str,
    ctx: Optional[ExecutionContext],
    requested_targets: Optional[List[str]],
) -> GateDecision:
    # Paso 0 — cache: una operación secuencial que cruza varias funciones
    # (ej. la cadena LLM de 3 pasos) se autoriza UNA sola vez.
    if ctx is not None and boundary in ctx.resolved_decision:
        return ctx.resolved_decision[boundary]

    mode = boundary_mode.get_mode(boundary)
    targets = list(requested_targets) if requested_targets is not None else None

    # Paso 1 — contexto ausente/inválido.
    if ctx is None or not ctx.is_valid():
        if mode == boundary_mode.ENFORCE:
            decision = GateDecision(
                boundary=boundary, execution=BLOCKED_MISSING_CONTEXT,
                decision=DECISION_MISSING_CONTEXT, mode=mode,
                authorized_targets=[],
                reason="ExecutionContext ausente/inválido en modo ENFORCE — fail-closed",
            )
        else:
            decision = GateDecision(
                boundary=boundary, execution=WOULD_BLOCK_MISSING_CONTEXT,
                decision=DECISION_MISSING_CONTEXT, mode=mode,
                authorized_targets=targets,
                reason="ExecutionContext ausente/inválido en modo SHADOW — "
                       "solo observación, comportamiento sin cambios",
            )
        _record_ledger(boundary, decision)
        if ctx is not None:
            ctx.resolved_decision[boundary] = decision
        return decision

    # Paso 2 — contexto válido: evaluar contra los 7 principios.
    proposal = ctx.to_proposal()
    verdict = evaluate(proposal, mode=mode)

    if mode == boundary_mode.SHADOW:
        # Shadow: SIEMPRE ejecuta con el conjunto original, sin importar el
        # veredicto — solo observa y registra (idéntico a `shadow_check()`).
        decision = GateDecision(
            boundary=boundary, execution=EXECUTE, decision=verdict.overall.value.upper(),
            mode=mode, authorized_targets=targets, reason=verdict.summary(),
        )
        _record_ledger(
            boundary, decision, action=proposal.action,
            correlation_id=proposal.correlation_id, verdict_summary=verdict.summary(),
        )
        ctx.resolved_decision[boundary] = decision
        return decision

    # ENFORCE
    if verdict.overall == PrincipleVerdict.BLOCK:
        decision = GateDecision(
            boundary=boundary, execution=BLOCKED, decision=DECISION_BLOCK,
            mode=mode, authorized_targets=[], reason=verdict.summary(),
        )
    elif verdict.overall == PrincipleVerdict.PASS:
        decision = GateDecision(
            boundary=boundary, execution=EXECUTE, decision=DECISION_PASS,
            mode=mode, authorized_targets=targets, reason=verdict.summary(),
        )
    else:
        # CAUTION — UNA consulta real a DecisionAuthority, máximo UNA
        # corrección determinística.
        da_decision = _consult_decision_authority(proposal)
        if da_decision.auto_approved:
            # Corrección = identidad: el conjunto solicitado se ejecuta
            # completo, una sola vez.
            decision = GateDecision(
                boundary=boundary, execution=EXECUTE, decision=DECISION_CAUTION,
                mode=mode, authorized_targets=targets, correction_applied=True,
                reason=f"{verdict.summary()} | DecisionAuthority={da_decision.reason}",
            )
        elif targets is not None:
            # Operación de fan-out: la corrección vacía el conjunto
            # autorizado. Estado propio — NUNCA se reclasifica como BLOCK,
            # nunca cae a fallback.
            decision = GateDecision(
                boundary=boundary, execution=SKIPPED_EMPTY_AUTHORIZED_SET,
                decision=DECISION_CAUTION, mode=mode, authorized_targets=[],
                correction_applied=True,
                reason=f"{verdict.summary()} | DecisionAuthority={da_decision.reason}",
            )
        else:
            # Operación de destino único: no hay conjunto que vaciar.
            decision = GateDecision(
                boundary=boundary, execution=BLOCKED, decision=DECISION_CAUTION,
                mode=mode, authorized_targets=[], correction_applied=True,
                reason=f"{verdict.summary()} | DecisionAuthority={da_decision.reason}",
            )

    _record_ledger(
        boundary, decision, action=proposal.action,
        correlation_id=proposal.correlation_id, verdict_summary=verdict.summary(),
    )
    ctx.resolved_decision[boundary] = decision
    return decision


# ---------------------------------------------------------------------------
# Reporte de promoción (deliverable 6) — lee del ledger, no un contador
# paralelo. Solo lectura/reporte: NUNCA activa ENFORCE automáticamente.
# ---------------------------------------------------------------------------

_MISSING_CONTEXT_TAGS = frozenset({WOULD_BLOCK_MISSING_CONTEXT.lower(), BLOCKED_MISSING_CONTEXT.lower()})


def _parse_ledger_timestamp(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        cleaned = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def promotion_readiness(boundary: str, window_hours: float = 72.0) -> Dict[str, Any]:
    """
    Reporte de solo lectura sobre si `boundary` cumple el criterio
    cuantitativo de promoción SHADOW -> ENFORCE (decisión posterior,
    manual, NO ejecutada por esta función).

    Criterio (los 4 deben cumplirse a la vez):
      - 0 eventos WOULD_BLOCK_MISSING_CONTEXT / BLOCKED_MISSING_CONTEXT
        observados en la ventana continua.
      - 100% de ExecutionContext válido.
      - >= 100 ejecuciones observadas.
      - >= `window_hours` horas continuas sin ningún evento de contexto
        inválido (la ventana se reinicia ante cualquiera de esos eventos).

    Best-effort: lee del ledger existente (`core.audit_ledger`) filtrando
    por prefijo de acción `pre_execution:{boundary}:` — el ledger no
    soporta filtro por prefijo ni por rango de tiempo nativamente, así que
    se trae un lote acotado y se filtra en Python. Adecuado para reporte
    manual, no para paths de alta frecuencia.
    """
    if boundary not in boundary_mode.BOUNDARIES:
        return {"boundary": boundary, "error": f"boundary desconocido: {boundary}"}

    try:
        from core import audit_ledger
        rows = audit_ledger.query(limit=5000)
    except Exception as exc:
        return {"boundary": boundary, "error": f"ledger_query_failed: {exc}"}

    prefix = f"pre_execution:{boundary}:"
    events = []
    for row in rows:
        action = row.get("action", "")
        if not action.startswith(prefix):
            continue
        ts = _parse_ledger_timestamp(row.get("timestamp", ""))
        if ts is None:
            continue
        tag = action[len(prefix):]
        events.append((ts, tag))
    events.sort(key=lambda e: e[0])

    now = datetime.now(timezone.utc)
    window_start = now.timestamp() - window_hours * 3600.0
    in_window = [e for e in events if e[0].timestamp() >= window_start]

    total_in_window = len(in_window)
    missing_in_window = sum(1 for _, tag in in_window if tag in _MISSING_CONTEXT_TAGS)

    last_invalid_ts = max(
        (ts for ts, tag in events if tag in _MISSING_CONTEXT_TAGS), default=None,
    )
    if last_invalid_ts is not None:
        continuous_hours = (now - last_invalid_ts).total_seconds() / 3600.0
    elif events:
        continuous_hours = (now - events[0][0]).total_seconds() / 3600.0
    else:
        continuous_hours = 0.0

    valid_pct = (
        100.0 * (total_in_window - missing_in_window) / total_in_window
        if total_in_window > 0 else 0.0
    )

    ready = (
        missing_in_window == 0
        and total_in_window >= 100
        and continuous_hours >= window_hours
    )

    return {
        "boundary": boundary,
        "current_mode": boundary_mode.get_mode(boundary),
        "window_hours": window_hours,
        "total_executions_observed": total_in_window,
        "would_block_missing_context_count": missing_in_window,
        "valid_context_pct": round(valid_pct, 2),
        "continuous_hours_without_invalid_context": round(continuous_hours, 2),
        "ready": ready,
    }
