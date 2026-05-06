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
]
