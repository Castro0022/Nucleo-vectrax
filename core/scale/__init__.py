"""
core/scale — Scale Governor Engine.

Capa middleware antes de Cognition. Selecciona el pipeline mínimo que
puede satisfacer la petición según intención, tier, riesgo, carga y
coste. Evita que cada mensaje active motores innecesarios.

Componentes:
  - pipeline_policy: PipelineProfile, Tier, PolicyConfig (declarativos).
  - scale_governor: ScaleGovernor (orquestador principal).
  - rate_limiter: Token Bucket por user/tier.
  - load_shedder: degrada el sistema bajo carga antes de caerse.
  - cost_tracker: mide latencia/tokens/éxito por motor.

Filosofía:
  - "Hola" no debe activar el pipeline cognitivo completo.
  - "Analiza este contrato" sí debe.
  - Cada perfil tiene un budget de motores activables.
  - Fallo de un motor = fallback inmediato, no cascada de retries.
  - Bajo carga, el sistema degrada gracefully — nunca se cae.

Creador: Mario Bravo Castro
Fecha:   2026-05-06
"""

from core.scale.pipeline_policy import (  # noqa: F401
    PipelineProfile,
    Tier,
    PolicyConfig,
    get_policy_for,
    MAX_ENGINES_BY_TIER,
)
from core.scale.scale_governor import ScaleGovernor  # noqa: F401
from core.scale.rate_limiter import RateLimiter  # noqa: F401
from core.scale.load_shedder import LoadShedder, LoadLevel  # noqa: F401
from core.scale.cost_tracker import (  # noqa: F401
    CostTracker,
    track_engine_cost,
    get_global_tracker,
)

__all__ = [
    "PipelineProfile",
    "Tier",
    "PolicyConfig",
    "get_policy_for",
    "MAX_ENGINES_BY_TIER",
    "ScaleGovernor",
    "RateLimiter",
    "LoadShedder",
    "LoadLevel",
    "CostTracker",
    "track_engine_cost",
    "get_global_tracker",
]
