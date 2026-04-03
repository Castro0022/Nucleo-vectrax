"""
Vectrax Autonomous Executor — Ejecución Controlada en Dominio Limitado
=========================================================================
Ejecuta acciones reales cuando el Governor lo aprueba.
Toda acción se registra en el ledger con resultado y feedback.

Modo: SUPERVISED AUTONOMOUS
  - Dominio limitado: solo acciones dentro de AUTHORIZED_DOMAINS
  - Governor debe estar en modo 'act' o 'cautious'
  - Decision Authority valida cada acción
  - Load Governor verifica presión del sistema
  - Resultado se registra para aprendizaje (Ley 6: Causa y Efecto)
  - Errores se registran con rollback info (Ley 4: Polaridad)

Acciones ejecutables autónomamente:
  - activate_learned_rule: activar regla aprendida confirmada
  - gravity_rebalance: rebalancear memoria gravitacional
  - cleanup_queue: limpiar cola de mensajes viejos
  - cleanup_stale_hypotheses: archivar hipótesis estancadas
  - restart_service: reiniciar un servicio caído

Acciones que requieren aprobación (notifica por Telegram):
  - apply_autopatch: modificar código
  - execute_proposal: ejecutar propuesta cognitiva
  - promote_hypothesis_to_rule: crear regla nueva

Principio: ejecutar con precisión, registrar todo, aprender del resultado.

Creado: 2026-03-28
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("vectrax.autonomous_executor")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ExecutionResult:
    """Resultado de una acción ejecutada."""
    action: str
    success: bool = False
    output: str = ""
    error: str = ""
    duration_ms: float = 0.0
    governor_mode: str = ""
    authority_level: str = ""
    feedback_recorded: bool = False
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Action registry
# ---------------------------------------------------------------------------

_ACTION_HANDLERS: Dict[str, Callable] = {}


def _register(name: str):
    """Decorator to register an action handler."""
    def decorator(fn):
        _ACTION_HANDLERS[name] = fn
        return fn
    return decorator


# ---------------------------------------------------------------------------
# Autonomous Executor
# ---------------------------------------------------------------------------

class AutonomousExecutor:
    """
    Ejecutor autónomo controlado.

    Flujo por acción:
      1. Verificar Governor mode (debe ser 'act' o 'cautious')
      2. Verificar Decision Authority
      3. Verificar Load Governor (no ejecutar bajo presión RED)
      4. Ejecutar acción
      5. Registrar resultado en ledger
      6. Alimentar feedback al learning cycle
    """

    def __init__(self) -> None:
        self._total_executed: int = 0
        self._total_success: int = 0
        self._total_failed: int = 0
        self._total_rejected: int = 0
        logger.info("AutonomousExecutor initialized")

    def execute(self, action: str, **params) -> ExecutionResult:
        """
        Ejecuta una acción pasando por todas las validaciones.
        """
        t0 = time.time()
        result = ExecutionResult(action=action)

        # --- 1. Governor check ---
        gov_mode = self._get_governor_mode()
        result.governor_mode = gov_mode

        if gov_mode not in ("act", "cautious"):
            result.success = False
            result.error = f"Governor en modo '{gov_mode}' — ejecución no permitida"
            self._total_rejected += 1
            self._record_ledger(result, "rejected_governor")
            return result

        # --- 2. Decision Authority ---
        try:
            from core.operator.decision_authority import check_authority
            decision = check_authority(action, governor_mode=gov_mode)
            result.authority_level = decision.authority.value

            if not decision.auto_approved:
                result.success = False
                result.error = f"Autorización requerida: {decision.reason}"
                self._total_rejected += 1
                self._record_ledger(result, "rejected_authority")
                self._notify_for_approval(action, params, decision.reason)
                return result
        except Exception as exc:
            result.success = False
            result.error = f"Decision Authority error: {exc}"
            self._record_ledger(result, "error_authority")
            return result

        # --- 3. Load Governor ---
        try:
            from core.operator.load_governor import get_load_governor
            lg = get_load_governor()
            if not lg.should_accept_heavy_job():
                result.success = False
                result.error = f"Sistema bajo presión ({lg.level.value}) — acción pospuesta"
                self._total_rejected += 1
                self._record_ledger(result, "rejected_pressure")
                return result
        except Exception:
            pass

        # --- 4. Execute ---
        handler = _ACTION_HANDLERS.get(action)
        if handler is None:
            result.success = False
            result.error = f"No handler registered for action: {action}"
            self._record_ledger(result, "no_handler")
            return result

        try:
            output = handler(**params)
            result.success = True
            result.output = str(output)[:500] if output else "OK"
            self._total_executed += 1
            self._total_success += 1
        except Exception as exc:
            result.success = False
            result.error = str(exc)[:300]
            self._total_executed += 1
            self._total_failed += 1

        result.duration_ms = round((time.time() - t0) * 1000, 1)

        # --- 5. Record in ledger ---
        self._record_ledger(
            result,
            "executed_success" if result.success else "executed_error",
        )

        # --- 6. Feed learning cycle ---
        self._feed_learning(result)

        logger.info(
            "EXEC %s | %s | %.0fms | %s",
            action,
            "OK" if result.success else "FAIL",
            result.duration_ms,
            result.output[:60] if result.success else result.error[:60],
        )

        return result

    # =====================================================================
    # Internal helpers
    # =====================================================================

    @staticmethod
    def _get_governor_mode() -> str:
        try:
            from core.governor import get_current_policy
            return get_current_policy().get("mode", "observe")
        except Exception:
            return "observe"

    @staticmethod
    def _record_ledger(result: ExecutionResult, event: str) -> None:
        try:
            from core.operator import ledger_bridge as ledger
            ledger.record_event(
                action=f"autonomous_exec:{event}",
                category=ledger.EventCategory.ACTION,
                risk_zone=(
                    ledger.RiskZone.GREEN if result.success
                    else ledger.RiskZone.YELLOW
                ),
                reason=f"{result.action}: {result.output[:100] or result.error[:100]}",
                details={
                    "action": result.action,
                    "success": result.success,
                    "duration_ms": result.duration_ms,
                    "governor_mode": result.governor_mode,
                    "authority": result.authority_level,
                },
            )
            result.feedback_recorded = True
        except Exception:
            pass

    @staticmethod
    def _feed_learning(result: ExecutionResult) -> None:
        """Feed execution result to learning cycle for pattern detection."""
        try:
            from core.learning_cycle.anomaly_detector import InputEvent
            from core.learning_cycle.pipeline import get_learning_pipeline
            get_learning_pipeline().process_event(InputEvent(
                text=f"exec:{result.action}:{('ok' if result.success else 'fail')}",
                intent=f"autonomous_{result.action}",
                source="executor",
                length=0,
            ))
        except Exception:
            pass

    @staticmethod
    def _notify_for_approval(action: str, params: Dict, reason: str) -> None:
        """Notify creator via Telegram for actions needing approval."""
        try:
            import os
            import httpx
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            chat_id = os.environ.get("TELEGRAM_CREATOR_CHAT_ID", "")
            if not token or not chat_id:
                return
            msg = (
                f"🟡 ACCIÓN REQUIERE APROBACIÓN\n\n"
                f"Acción: {action}\n"
                f"Razón: {reason[:150]}\n"
                f"Parámetros: {str(params)[:100]}"
            )
            httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": msg},
                timeout=5,
            )
        except Exception:
            pass

    # =====================================================================
    # Status
    # =====================================================================

    def stats(self) -> Dict[str, Any]:
        return {
            "total_executed": self._total_executed,
            "total_success": self._total_success,
            "total_failed": self._total_failed,
            "total_rejected": self._total_rejected,
            "success_rate": round(
                self._total_success / max(self._total_executed, 1), 4,
            ),
        }


# ---------------------------------------------------------------------------
# Action handlers (dominio limitado)
# ---------------------------------------------------------------------------

@_register("activate_learned_rule")
def _action_activate_rule(rule_id: str = "", **kw) -> str:
    from core.learn.learned_rules import get_rules_store
    store = get_rules_store()
    if store.activate_rule(rule_id):
        return f"Rule {rule_id} activated"
    return f"Rule {rule_id} not found"


@_register("gravity_rebalance")
def _action_gravity_rebalance(**kw) -> str:
    from core.learn.gravity_engine import get_gravity_index
    from core.learn.gravity_decay import apply_decay
    idx = get_gravity_index()
    demoted = apply_decay(idx)
    return f"Rebalanced: {len(demoted)} demoted"


@_register("cleanup_queue")
def _action_cleanup_queue(**kw) -> str:
    from core.transport.message_queue import cleanup
    removed = cleanup()
    return f"Cleaned {removed} old messages"


@_register("cleanup_stale_hypotheses")
def _action_cleanup_hypotheses(max_age_hours: float = 48, **kw) -> str:
    from core.operator.hypothesis_engine import get_hypothesis_engine, HypothesisStatus
    import time as _t
    engine = get_hypothesis_engine()
    active = engine.get_active()
    cutoff = _t.time() - (max_age_hours * 3600)
    archived = 0
    for h in active:
        if h.created_at < cutoff:
            engine.archive(h.id, reason="Stale hypothesis auto-archived")
            archived += 1
    return f"Archived {archived} stale hypotheses"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_executor: Optional[AutonomousExecutor] = None


def get_executor() -> AutonomousExecutor:
    global _executor
    if _executor is None:
        _executor = AutonomousExecutor()
    return _executor


def execute(action: str, **params) -> ExecutionResult:
    """Shortcut: execute an action through the autonomous executor."""
    return get_executor().execute(action, **params)
