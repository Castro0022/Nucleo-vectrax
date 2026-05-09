"""
core/self_observation — Self Perception Layer.

Le da a Baytracks la capacidad de observar su propio estado, percibir
qué cambió, qué se resolvió, qué sigue inestable, y producir una
síntesis estructurada que puede inyectarse al contexto del LLM cuando
habla con el creador.

NO es logs. Es percepción.

Pipeline:
    state_collector.collect_state()         → SystemSnapshot
    deployment_memory.deploy_summary()      → commits + módulos
    behavior_diff.diff_against_baseline()   → cambios vs baseline
    operational_reflection.reflect_now()    → percepción categorizada
    self_summary.compose_self_summary()     → bloque para el prompt
"""

from core.self_observation.state_collector import (  # noqa: F401
    SystemSnapshot,
    collect_state,
)
from core.self_observation.deployment_memory import (  # noqa: F401
    recent_commits,
    modified_modules,
    restart_count,
    deploy_summary,
)
from core.self_observation.behavior_diff import (  # noqa: F401
    save_baseline,
    load_baseline,
    diff,
    diff_against_baseline,
)
from core.self_observation.operational_reflection import (  # noqa: F401
    reflect,
    reflect_now,
)
from core.self_observation.self_summary import (  # noqa: F401
    compose_self_summary,
    compose_self_summary_for_prompt,
)

__all__ = [
    "SystemSnapshot",
    "collect_state",
    "recent_commits",
    "modified_modules",
    "restart_count",
    "deploy_summary",
    "save_baseline",
    "load_baseline",
    "diff",
    "diff_against_baseline",
    "reflect",
    "reflect_now",
    "compose_self_summary",
    "compose_self_summary_for_prompt",
]
