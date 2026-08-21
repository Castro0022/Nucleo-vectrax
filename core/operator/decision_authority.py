"""
Vectrax — Autoridad de Decisión
==================================
Define qué decide Vectrax solo y qué requiere autorización del creador.

Regla fundamental:
  - Todo lo INTERNO → automático (Vectrax decide solo)
  - Todo lo EXTERNO CRÍTICO → requiere autorización del creador

Clasificación de acciones:

  AUTO (Vectrax decide solo, sin preguntar):
    - Clasificar mensajes (SmartRouter)
    - Buscar en memoria / web / mercado / lugares
    - Generar respuestas al usuario
    - Almacenar interacciones en memoria
    - Registrar en ledger
    - Ejecutar ciclo de aprendizaje (detect → investigate → verify)
    - Decay gravitacional / rebalance de memoria
    - Health checks / reinicio de servicios caídos
    - Transcribir audio
    - Detectar anomalías

  AUTORIZADO (requiere confirmación del creador):
    - Integrar reglas aprendidas al núcleo (learning → rule promotion)
    - Modificar archivos de código del sistema
    - Cambiar configuración de Governor / políticas
    - Acciones irreversibles (delete, drop, reset)
    - Operaciones con credenciales / tokens
    - Cambiar identidad del sistema
    - Ejecutar en modo autónomo (cambiar de guided → autonomous)
    - Activar autopatch

Creado: 2026-03-28
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger("vectrax.operator.decision_authority")


# ---------------------------------------------------------------------------
# Authority levels
# ---------------------------------------------------------------------------

class Authority(str, Enum):
    """Nivel de autoridad requerido para una acción."""
    AUTO = "auto"                 # Vectrax decide solo
    AUTHORIZED = "authorized"     # Requiere confirmación del creador
    BLOCKED = "blocked"           # Prohibido siempre


# ---------------------------------------------------------------------------
# Action classification
# ---------------------------------------------------------------------------

# Acciones que Vectrax ejecuta automáticamente (internas, no destructivas)
AUTO_ACTIONS = frozenset({
    # Procesamiento de mensajes
    "classify_message",
    "route_message",
    "generate_response",
    # "respond" es el nombre REAL usado por
    # core.operator.external_gateway.py::_constitutional_gate() al construir
    # el ActionProposal para el choke point constitucional (Fase 1/1.5).
    # Semánticamente es exactamente "Generar respuestas al usuario" (arriba);
    # se mantiene "generate_response" también por compatibilidad con otros
    # posibles callers.
    "respond",
    "resolve_online",
    "resolve_local",
    "resolve_market",
    "resolve_places",
    "resolve_identity",
    "transcribe_voice",

    # Memoria y aprendizaje
    "store_memory",
    "store_interaction",
    "store_user_location",
    "update_user_profile",
    "ingest_star",
    "update_star_gravity",
    "gravity_decay",
    "gravity_rebalance",
    "detect_anomaly",
    "investigate_anomaly",
    "verify_hypothesis",
    "generate_hypothesis",
    "detect_patterns",
    "detect_constellations",

    # Observabilidad
    "record_ledger",
    "record_episodic",
    "log_event",
    "health_check",
    "restart_service",

    # Ciclos internos
    "learning_cycle",
    "cognitive_cycle",
    "meta_loop_cycle",
    "law_enforcement_check",

    # Executor: acciones autónomas de mantenimiento
    "cleanup_queue",
    "cleanup_stale_hypotheses",
    "gravity_rebalance",
})

# Acciones que requieren autorización del creador (externas, críticas)
AUTHORIZED_ACTIONS = frozenset({
    # Integración de conocimiento al núcleo
    "integrate_learned_rule",
    "activate_learned_rule",
    "promote_hypothesis_to_rule",

    # Modificación del sistema
    "modify_source_code",
    "apply_autopatch",
    "execute_proposal",
    "modify_configuration",
    "change_governor_mode",
    "change_autonomy_level",

    # Acciones externas reales
    "send_email",
    "make_api_call_external",
    "execute_trade",
    "create_webhook",
})

# Acciones bloqueadas siempre
BLOCKED_ACTIONS = frozenset({
    "modify_identity",
    "modify_fundamental_laws",
    "modify_constitution",
    "modify_invariants",
    "delete_core_memory",
    "delete_vault",
    "expose_credentials",
    "disable_ledger",
    "disable_law_enforcement",
})


# ---------------------------------------------------------------------------
# Decision result
# ---------------------------------------------------------------------------

@dataclass
class DecisionResult:
    """Resultado de consultar la autoridad de decisión."""
    action: str
    authority: Authority
    auto_approved: bool
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "authority": self.authority.value,
            "auto_approved": self.auto_approved,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Core decision function
# ---------------------------------------------------------------------------

def check_authority(
    action: str,
    *,
    governor_mode: str = "observe",
    risk_level: str = "LOW",
) -> DecisionResult:
    """
    Determina si Vectrax puede ejecutar una acción automáticamente.

    Args:
        action: nombre de la acción a evaluar
        governor_mode: modo actual del governor
        risk_level: nivel de riesgo de la operación

    Returns:
        DecisionResult con la decisión y razón.
    """
    # --- Blocked: siempre prohibido ---
    if action in BLOCKED_ACTIONS:
        _log_decision(action, Authority.BLOCKED, "Acción prohibida por ley inmutable")
        return DecisionResult(
            action=action,
            authority=Authority.BLOCKED,
            auto_approved=False,
            reason="Acción prohibida — viola leyes inmutables del sistema",
        )

    # --- Auto: Vectrax decide solo ---
    if action in AUTO_ACTIONS:
        # Excepción: en modo recover, algunas acciones auto se pausan
        if governor_mode == "recover" and action in {
            "learning_cycle", "gravity_rebalance", "detect_patterns",
        }:
            return DecisionResult(
                action=action,
                authority=Authority.AUTO,
                auto_approved=False,
                reason=f"Acción auto pausada — Governor en modo {governor_mode} (Ley 5: Ritmo)",
            )
        return DecisionResult(
            action=action,
            authority=Authority.AUTO,
            auto_approved=True,
            reason="Acción interna — Vectrax decide automáticamente",
        )

    # --- Authorized: requiere creador ---
    if action in AUTHORIZED_ACTIONS:
        # En modo 'act' con riesgo bajo, algunas acciones se auto-aprueban
        if governor_mode == "act" and risk_level == "LOW":
            if action in {"integrate_learned_rule", "activate_learned_rule"}:
                return DecisionResult(
                    action=action,
                    authority=Authority.AUTHORIZED,
                    auto_approved=True,
                    reason="Acción autorizable — Governor en modo 'act' + riesgo bajo",
                )
        _log_decision(action, Authority.AUTHORIZED, "Requiere autorización del creador")
        return DecisionResult(
            action=action,
            authority=Authority.AUTHORIZED,
            auto_approved=False,
            reason="Acción externa crítica — requiere autorización del creador",
        )

    # --- Desconocida: por defecto requiere autorización ---
    _log_decision(action, Authority.AUTHORIZED, f"Acción desconocida: {action}")
    return DecisionResult(
        action=action,
        authority=Authority.AUTHORIZED,
        auto_approved=False,
        reason=f"Acción no clasificada '{action}' — requiere autorización por precaución",
    )


def _log_decision(action: str, authority: Authority, reason: str) -> None:
    """Registra decisiones no-auto en el ledger."""
    try:
        from core.operator import ledger_bridge as ledger
        ledger.record_event(
            action=f"decision_authority:{authority.value}",
            category=ledger.EventCategory.REASONING,
            risk_zone=(
                ledger.RiskZone.RED if authority == Authority.BLOCKED
                else ledger.RiskZone.YELLOW
            ),
            reason=reason,
            details={"action": action, "authority": authority.value},
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def can_auto_execute(action: str, **kwargs) -> bool:
    """Shortcut: ¿puede Vectrax ejecutar esto sin preguntar?"""
    return check_authority(action, **kwargs).auto_approved


def requires_creator(action: str, **kwargs) -> bool:
    """Shortcut: ¿requiere autorización del creador?"""
    result = check_authority(action, **kwargs)
    return result.authority in (Authority.AUTHORIZED, Authority.BLOCKED) and not result.auto_approved


def is_blocked(action: str) -> bool:
    """Shortcut: ¿está prohibido siempre?"""
    return action in BLOCKED_ACTIONS
