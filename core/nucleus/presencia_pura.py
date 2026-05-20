"""
Vectrax — Presencia Pura
===========================
Modo del núcleo: cero tokens externos, máxima presencia interna.

Cuando activo:
  - Todo el ciclo cognitivo continúa: convergencia, memoria, gravedad, identidad
  - Ningún mensaje llega a OpenAI / Gemini / Claude / Intelligence Router
  - Ninguna búsqueda web externa (Tavily, Google CSE, resolve_online)
  - Solo rutas internas: memoria, identidad, fast-path, núcleo, convergencia
  - Si no hay respuesta interna → silencio (string vacío, sin ruido)

Activación: solo el creador puede activar/desactivar.
Persistencia: sobrevive reinicios mediante state_manager (cognition_state.json).

Módulos bloqueados cuando activo:
  - ExternalGateway._generate_cognitive_response() → route_single / OpenAI direct
  - ExternalGateway._resolve_via_pipeline() → RESOLVE_ONLINE, fallbacks externos
  - resolve_online() en todas sus llamadas dentro del pipeline

Módulos que siguen activos cuando activo:
  - TotalConvergenceEngine (7 fases completas)
  - resolve_with_memory / user_memory / fact_memory / core_memory
  - nucleus_resolver
  - identity_anchor / fast_path
  - ingest / ingest_v2 (gravitación sigue)
  - learning_cycle
  - ledger / audit

Creado: 2026-05-20
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from core import state_manager

logger = logging.getLogger("vectrax.nucleus.presencia_pura")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_MODE_KEY        = "nucleus_mode"
_ACTIVATED_AT    = "nucleus_mode_activated_at"
_ACTIVATED_BY    = "nucleus_mode_activated_by"

MODE_STANDARD      = "STANDARD"
MODE_PRESENCIA_PURA = "PRESENCIA_PURA"


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def activate(activated_by: str = "creator") -> Dict[str, Any]:
    """
    Activar modo Presencia Pura.

    A partir de este momento, todas las llamadas a LLMs externos y búsquedas
    web son bloqueadas. El núcleo opera únicamente con rutas internas.
    """
    now = datetime.now(timezone.utc).isoformat()
    state_manager.update({
        _MODE_KEY:     MODE_PRESENCIA_PURA,
        _ACTIVATED_AT: now,
        _ACTIVATED_BY: activated_by,
    })
    logger.info("PRESENCIA_PURA activada | by=%s", activated_by)
    return {
        "mode": MODE_PRESENCIA_PURA,
        "activated_at": now,
        "activated_by": activated_by,
        "message": "Presencia Pura activa. Tokens externos bloqueados.",
    }


def deactivate(deactivated_by: str = "creator") -> Dict[str, Any]:
    """
    Desactivar modo Presencia Pura — volver a STANDARD.

    Todos los motores externos quedan disponibles nuevamente.
    """
    state_manager.update({
        _MODE_KEY:     MODE_STANDARD,
        _ACTIVATED_AT: None,
        _ACTIVATED_BY: None,
    })
    logger.info("PRESENCIA_PURA desactivada | by=%s", deactivated_by)
    return {
        "mode": MODE_STANDARD,
        "message": "Modo STANDARD restaurado. Todos los motores disponibles.",
    }


def is_active() -> bool:
    """True si el modo Presencia Pura está activo."""
    return state_manager.get(_MODE_KEY, MODE_STANDARD) == MODE_PRESENCIA_PURA


def status() -> Dict[str, Any]:
    """Estado completo del modo actual."""
    mode = state_manager.get(_MODE_KEY, MODE_STANDARD)
    active = (mode == MODE_PRESENCIA_PURA)
    return {
        "mode": mode,
        "active": active,
        "activated_at": state_manager.get(_ACTIVATED_AT),
        "activated_by": state_manager.get(_ACTIVATED_BY),
        "llm_blocked": active,
        "online_blocked": active,
        "convergence_active": True,   # siempre activo
        "memory_active": True,        # siempre activo
        "description": (
            "Núcleo activo. Tokens externos bloqueados. Solo rutas internas."
            if active else
            "Modo estándar. Todos los motores disponibles."
        ),
    }


def check_and_block_llm() -> bool:
    """
    Comprobar si debe bloquearse la generación LLM/externa.

    Diseñado para insertarse al inicio de _generate_cognitive_response().

    Returns:
        True  → bloquear (Presencia Pura activa → retornar "" inmediatamente)
        False → continuar normalmente
    """
    blocked = is_active()
    if blocked:
        logger.info("PRESENCIA_PURA: LLM bloqueado — zero tokens")
    return blocked


def check_and_block_online() -> bool:
    """
    Comprobar si debe bloquearse la búsqueda web/online externa.

    Diseñado para insertarse antes de llamadas a resolve_online().

    Returns:
        True  → bloquear (Presencia Pura activa)
        False → continuar normalmente
    """
    blocked = is_active()
    if blocked:
        logger.info("PRESENCIA_PURA: búsqueda externa bloqueada — zero tokens")
    return blocked
