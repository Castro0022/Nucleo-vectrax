"""
Vectrax Core — Manifiesto de Memoria Gravitacional
=====================================================
Define la estructura formal de la memoria del operador y sus reglas.
Este archivo NO reimplementa la memoria — conecta y formaliza los
motores ya existentes bajo un modelo unificado.

Modelo de gravedad:
  HOT   — memoria activa, alta frecuencia, acceso inmediato
  WARM  — patrones emergentes, evidencia creciente
  COLD  — patrones confirmados pero infrecuentes
  DEEP  — archivo histórico, nunca borrado, comprimido

Regla cardinal: NADA SE BORRA. Solo se enfría, comprime o archiva.

Motores existentes integrados:
  - core.learn.gravity_engine (GravityIndex — HOT/WARM/COLD/DEEP)
  - core.learn.gravity_decay  (decaimiento temporal)
  - vectrax.gravity           (stars/constellations scoring)
  - cognition.memory          (memory_engine, knowledge_graph, stores)

Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Capas de memoria
# ---------------------------------------------------------------------------

class MemoryTier(str, Enum):
    """Capas gravitacionales de memoria."""
    HOT = "hot"     # Activa — acceso inmediato, alta prioridad
    WARM = "warm"   # Emergente — patrones con evidencia creciente
    COLD = "cold"   # Estable — confirmado pero poco frecuente
    DEEP = "deep"   # Archivo — histórico, comprimido, nunca borrado


@dataclass(frozen=True)
class TierPolicy:
    """Política de una capa de memoria."""
    tier: MemoryTier
    max_age_days: Optional[int]     # None = sin límite
    promotion_threshold: float       # Hits/frecuencia para subir
    demotion_threshold: float        # Inactividad para bajar
    compression_allowed: bool        # Si permite compresión
    deletion_allowed: bool           # Siempre False — regla cardinal

    def to_dict(self) -> dict:
        return {
            "tier": self.tier.value,
            "max_age_days": self.max_age_days,
            "promotion_threshold": self.promotion_threshold,
            "demotion_threshold": self.demotion_threshold,
            "compression_allowed": self.compression_allowed,
            "deletion_allowed": self.deletion_allowed,
        }


# ---------------------------------------------------------------------------
# Políticas por capa
# ---------------------------------------------------------------------------

TIER_POLICIES: Dict[MemoryTier, TierPolicy] = {
    MemoryTier.HOT: TierPolicy(
        tier=MemoryTier.HOT,
        max_age_days=None,
        promotion_threshold=0.0,        # Ya está en el nivel más alto
        demotion_threshold=7.0,          # 7 días sin actividad → WARM
        compression_allowed=False,
        deletion_allowed=False,
    ),
    MemoryTier.WARM: TierPolicy(
        tier=MemoryTier.WARM,
        max_age_days=None,
        promotion_threshold=3.0,         # 3 hits en 7 días + cc≥0.6 → HOT
        demotion_threshold=14.0,         # 14 días sin actividad → COLD
        compression_allowed=False,
        deletion_allowed=False,
    ),
    MemoryTier.COLD: TierPolicy(
        tier=MemoryTier.COLD,
        max_age_days=None,
        promotion_threshold=2.0,         # 2 hits en 14 días → WARM
        demotion_threshold=30.0,         # 30 días sin actividad → DEEP
        compression_allowed=True,
        deletion_allowed=False,
    ),
    MemoryTier.DEEP: TierPolicy(
        tier=MemoryTier.DEEP,
        max_age_days=None,              # Sin límite — nunca expira
        promotion_threshold=1.0,         # Cualquier hit → COLD (déjà vu)
        demotion_threshold=0.0,          # No hay capa inferior
        compression_allowed=True,
        deletion_allowed=False,          # REGLA CARDINAL: nunca borrar
    ),
}


# ---------------------------------------------------------------------------
# Tipos de memoria
# ---------------------------------------------------------------------------

class MemoryType(str, Enum):
    """Tipos de contenido almacenado en memoria."""
    EVENT = "event"                 # Evento del sistema (ingest, error, etc.)
    PATTERN = "pattern"             # Patrón detectado (repetición, correlación)
    DECISION = "decision"           # Decisión tomada (propuesta aprobada/rechazada)
    HYPOTHESIS = "hypothesis"       # Hipótesis generada (no confirmada)
    STAR = "star"                   # Estrella en el grafo gravitacional
    CONSTELLATION = "constellation" # Constelación de estrellas relacionadas
    EPISODE = "episode"             # Episodio episódico (secuencia de eventos)


# ---------------------------------------------------------------------------
# Mapa de motores existentes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EngineMapping:
    """Mapea un aspecto de la memoria a su implementación existente."""
    aspect: str
    module: str
    entry_point: str
    description: str


MEMORY_ENGINES: List[EngineMapping] = [
    EngineMapping(
        aspect="gravity_index",
        module="core.learn.gravity_engine",
        entry_point="get_gravity_index()",
        description="Índice gravitacional HOT→WARM→COLD→DEEP con déjà vu",
    ),
    EngineMapping(
        aspect="gravity_decay",
        module="core.learn.gravity_decay",
        entry_point="run_decay()",
        description="Decaimiento temporal de registros por inactividad",
    ),
    EngineMapping(
        aspect="star_gravity",
        module="vectrax.gravity",
        entry_point="compute_star_gravity(star)",
        description="Scoring gravitacional de estrellas individuales",
    ),
    EngineMapping(
        aspect="constellation_gravity",
        module="vectrax.gravity",
        entry_point="compute_constellation_gravity(c)",
        description="Scoring gravitacional de constelaciones",
    ),
    EngineMapping(
        aspect="cognitive_memory",
        module="cognition.memory.memory_engine",
        entry_point="MemoryEngine()",
        description="Motor de memoria cognitiva con query por intent",
    ),
    EngineMapping(
        aspect="knowledge_graph",
        module="cognition.memory.knowledge_graph",
        entry_point="KnowledgeGraph()",
        description="Grafo de conocimiento con nodos y relaciones",
    ),
    EngineMapping(
        aspect="episodic_memory",
        module="core.learn.episodic",
        entry_point="get_episodic_store()",
        description="Memoria episódica: secuencias de eventos",
    ),
    EngineMapping(
        aspect="semantic_memory",
        module="core.learn.semantic",
        entry_point="get_semantic_store()",
        description="Memoria semántica: significados y relaciones abstractas",
    ),
]


# ---------------------------------------------------------------------------
# Manifiesto completo
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MemoryManifest:
    """Manifiesto formal de la estructura de memoria del operador."""

    cardinal_rule: str = "Nada se borra. Solo se enfría, comprime o archiva."
    model: str = "gravitacional"
    tiers: int = 4

    def get_tier_policy(self, tier: MemoryTier) -> TierPolicy:
        return TIER_POLICIES[tier]

    def get_all_policies(self) -> Dict[str, dict]:
        return {t.value: p.to_dict() for t, p in TIER_POLICIES.items()}

    def get_engines(self) -> List[dict]:
        return [
            {
                "aspect": e.aspect,
                "module": e.module,
                "entry_point": e.entry_point,
                "description": e.description,
            }
            for e in MEMORY_ENGINES
        ]

    def validate_no_deletion(self) -> bool:
        """Verifica que ninguna política permita borrado."""
        return all(not p.deletion_allowed for p in TIER_POLICIES.values())

    def to_dict(self) -> dict:
        return {
            "cardinal_rule": self.cardinal_rule,
            "model": self.model,
            "tiers": self.tiers,
            "policies": self.get_all_policies(),
            "engines": self.get_engines(),
            "no_deletion_verified": self.validate_no_deletion(),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

MEMORY_MANIFEST = MemoryManifest()


def get_memory_manifest() -> MemoryManifest:
    """Retorna el manifiesto de memoria."""
    return MEMORY_MANIFEST
