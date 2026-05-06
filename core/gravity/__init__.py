"""
core/gravity — Vectrax Gravity Engine.

Motor de memoria profunda con vector search y masa cognitiva.

Componentes:
  - vector_store: SQLiteVectorStore con tabla deep_memory.
                  query() filtra por user_id, calcula cosine_similarity
                  con numpy y devuelve top_k.
  - gravity_engine: should_use_deep_memory(message). Gate de entrada al
                    vector store. Si False, el Router no toca la DB.
  - mass_tracker: masa por categoría (Vision +5, Persona +8, Emoción +10).
                  Sincroniza con el Universe.

Principios:
  - Cero red. Todo cómputo de similitud es local.
  - Aislamiento estricto por user_id (RBAC implícito).
  - Persistencia real en SQLite (~/.vectrax/gravity.db).
  - Diseñado para millones de users (índice por user_id).

Creador: Mario Bravo Castro
Fecha:   2026-05-06
"""

from core.gravity.vector_store import (  # noqa: F401
    SQLiteVectorStore,
    cosine_similarity,
    DeepMemoryRecord,
)
from core.gravity.gravity_engine import (  # noqa: F401
    should_use_deep_memory,
    DeepMemoryRouter,
    DEEP_MEMORY_SIGNALS,
)
from core.gravity.mass_tracker import (  # noqa: F401
    MassTracker,
    MassKind,
    MASS_VALUES,
    add_mass,
    get_mass,
)
from core.gravity.governor import (  # noqa: F401
    MemoryGovernor,
    PROTECTED_MEMORY_TYPES,
)
from core.gravity.concept_fusion import (  # noqa: F401
    ConceptFusion,
    derive_identity_key,
)
from core.gravity.decay import (  # noqa: F401
    MemoryDecay,
    DECAY_AFTER_DAYS,
    DECAY_PER_CYCLE,
)
from core.gravity.essential_summary import (  # noqa: F401
    EssentialSummaryEngine,
)
from core.gravity.retrieval import (  # noqa: F401
    retrieve,
    rank_results,
    DEFAULT_WEIGHTS,
)

__all__ = [
    "SQLiteVectorStore",
    "DeepMemoryRecord",
    "cosine_similarity",
    "should_use_deep_memory",
    "DeepMemoryRouter",
    "DEEP_MEMORY_SIGNALS",
    "MassTracker",
    "MassKind",
    "MASS_VALUES",
    "add_mass",
    "get_mass",
    "MemoryGovernor",
    "PROTECTED_MEMORY_TYPES",
    "ConceptFusion",
    "derive_identity_key",
    "MemoryDecay",
    "DECAY_AFTER_DAYS",
    "DECAY_PER_CYCLE",
    "EssentialSummaryEngine",
    "retrieve",
    "rank_results",
    "DEFAULT_WEIGHTS",
]
