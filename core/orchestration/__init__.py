"""
core/orchestration — Capa de orquestación de motores de Vectrax.

Fuente única para conectar/activar todos los motores de forma declarativa,
idempotente y segura (tiers CORE / OBSERVE / GATED_INTERNAL / EXTERNAL).

Uso rápido::

    from core.orchestration import activate_all, get_engine_status
    report = activate_all("safe")     # activa interno, verifica externo
    status = get_engine_status()      # snapshot read-only por motor
"""
from core.orchestration.engine_registry import (
    EngineSpec,
    Tier,
    all_specs,
    by_group,
    by_tier,
    get,
    register,
)
from core.orchestration.bootstrap import (
    EngineResult,
    PROFILE_FULL,
    PROFILE_SAFE,
    activate_all,
    get_engine_status,
)

__all__ = [
    "EngineSpec",
    "Tier",
    "all_specs",
    "by_group",
    "by_tier",
    "get",
    "register",
    "EngineResult",
    "PROFILE_SAFE",
    "PROFILE_FULL",
    "activate_all",
    "get_engine_status",
]
