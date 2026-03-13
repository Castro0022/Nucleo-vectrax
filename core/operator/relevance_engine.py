"""
Vectrax Core — Relevance Engine
==================================
Motor de relevancia que combina la prioridad de percepción con el
scoring gravitacional para determinar qué entradas merecen atención
del operador.

Fórmula de relevancia:

  relevance = (w_priority × priority) + (w_gravity × gravity) + (w_recency × recency)

Donde:
  - priority: viene del event_classifier (0-1)
  - gravity:  viene del gravity_engine (frecuencia, hits, tier)
  - recency:  decaimiento temporal exponencial

El resultado es un score ∈ [0, 1] que determina:
  - HIGH   (≥0.7): atención inmediata del operador
  - MEDIUM (≥0.4): incluir en siguiente ciclo de razonamiento
  - LOW    (<0.4): registrar y almacenar, no requiere acción

Conecta con:
  - core.learn.gravity_engine (GravityIndex)
  - cognition.perception (señales clasificadas)
  - core.operator.memory_manifest (políticas de tiers)

Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.operator.relevance_engine")


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

# Pesos de la fórmula de relevancia
W_PRIORITY = 0.45   # Peso de la prioridad del clasificador
W_GRAVITY = 0.35    # Peso del score gravitacional
W_RECENCY = 0.20    # Peso de la recencia temporal

# Umbrales de relevancia
RELEVANCE_HIGH = 0.7
RELEVANCE_MEDIUM = 0.4

# Decaimiento temporal
RECENCY_HALF_LIFE_HOURS = 24.0  # La recencia se reduce a la mitad cada 24h


class RelevanceLevel(str, Enum):
    """Nivel de relevancia de una entrada."""
    HIGH = "HIGH"       # Atención inmediata
    MEDIUM = "MEDIUM"   # Incluir en próximo ciclo
    LOW = "LOW"         # Registrar, no requiere acción


# ---------------------------------------------------------------------------
# Resultado de scoring
# ---------------------------------------------------------------------------

@dataclass
class RelevanceScore:
    """Score de relevancia para una entrada."""
    fingerprint: str
    relevance: float            # ∈ [0, 1]
    level: RelevanceLevel
    priority_component: float   # Contribución de priority
    gravity_component: float    # Contribución de gravity
    recency_component: float    # Contribución de recency
    source_summary: str = ""
    gravity_tier: Optional[str] = None
    gravity_hits: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "relevance": round(self.relevance, 4),
            "level": self.level.value,
            "components": {
                "priority": round(self.priority_component, 4),
                "gravity": round(self.gravity_component, 4),
                "recency": round(self.recency_component, 4),
            },
            "gravity_tier": self.gravity_tier,
            "gravity_hits": self.gravity_hits,
        }


# ---------------------------------------------------------------------------
# Motor de Relevancia
# ---------------------------------------------------------------------------

class RelevanceEngine:
    """
    Calcula la relevancia de señales combinando percepción y gravedad.

    Usage::

        engine = RelevanceEngine()
        scores = engine.score_signals(signals)
        high = engine.filter_high(scores)
    """

    def __init__(
        self,
        w_priority: float = W_PRIORITY,
        w_gravity: float = W_GRAVITY,
        w_recency: float = W_RECENCY,
    ) -> None:
        self._w_priority = w_priority
        self._w_gravity = w_gravity
        self._w_recency = w_recency
        self._gravity_index = None  # lazy load

    def _get_gravity_index(self):
        """Lazy load del gravity index para evitar imports circulares."""
        if self._gravity_index is None:
            try:
                from core.learn.gravity_engine import get_gravity_index
                self._gravity_index = get_gravity_index()
            except ImportError:
                logger.warning("GravityIndex not available — scoring without gravity")
        return self._gravity_index

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score_signal(
        self,
        *,
        fingerprint: str,
        priority: float,
        timestamp: float,
        summary: str = "",
    ) -> RelevanceScore:
        """
        Calcula el score de relevancia para una señal individual.

        Args:
            fingerprint: Identificador único o hash de la señal
            priority: Prioridad del clasificador (0-1)
            timestamp: Timestamp de la señal (epoch seconds)
            summary: Resumen para contexto
        """
        # 1. Componente de prioridad
        priority_comp = min(max(priority, 0.0), 1.0)

        # 2. Componente gravitacional
        gravity_comp = 0.0
        gravity_tier = None
        gravity_hits = 0

        gindex = self._get_gravity_index()
        if gindex is not None:
            record = gindex.get(fingerprint)
            if record is not None:
                gravity_tier = record.tier
                gravity_hits = record.hits
                # Normalizar: tier HOT=1.0, WARM=0.7, COLD=0.4, DEEP=0.1
                tier_scores = {"hot": 1.0, "warm": 0.7, "cold": 0.4, "deep": 0.1}
                tier_base = tier_scores.get(record.tier, 0.1)
                # Combinar tier con frecuencia normalizada
                freq_norm = min(record.freq / 10.0, 1.0) if record.freq else 0.0
                gravity_comp = 0.6 * tier_base + 0.4 * freq_norm

        # 3. Componente de recencia
        age_hours = max((time.time() - timestamp) / 3600.0, 0.001)
        recency_comp = math.exp(-0.693 * age_hours / RECENCY_HALF_LIFE_HOURS)

        # 4. Score combinado
        relevance = (
            self._w_priority * priority_comp
            + self._w_gravity * gravity_comp
            + self._w_recency * recency_comp
        )
        relevance = min(max(relevance, 0.0), 1.0)

        # 5. Nivel
        if relevance >= RELEVANCE_HIGH:
            level = RelevanceLevel.HIGH
        elif relevance >= RELEVANCE_MEDIUM:
            level = RelevanceLevel.MEDIUM
        else:
            level = RelevanceLevel.LOW

        return RelevanceScore(
            fingerprint=fingerprint,
            relevance=relevance,
            level=level,
            priority_component=self._w_priority * priority_comp,
            gravity_component=self._w_gravity * gravity_comp,
            recency_component=self._w_recency * recency_comp,
            source_summary=summary[:100],
            gravity_tier=gravity_tier,
            gravity_hits=gravity_hits,
        )

    def score_signals(
        self,
        signals: List[Dict[str, Any]],
    ) -> List[RelevanceScore]:
        """
        Score un lote de señales.

        Cada señal debe tener: fingerprint (o se genera desde summary),
        priority, timestamp, y opcionalmente summary.

        Returns:
            Lista de RelevanceScore ordenada por relevancia descendente.
        """
        scores: List[RelevanceScore] = []

        for s in signals:
            fp = s.get("fingerprint") or _generate_fingerprint(
                s.get("summary", ""), s.get("source", "")
            )
            score = self.score_signal(
                fingerprint=fp,
                priority=s.get("priority", 0.0),
                timestamp=s.get("timestamp", time.time()),
                summary=s.get("summary", ""),
            )
            scores.append(score)

        # Ordenar por relevancia descendente
        scores.sort(key=lambda s: s.relevance, reverse=True)
        return scores

    def score_cognitive_signals(
        self,
        signals,
    ) -> List[RelevanceScore]:
        """
        Score un lote de CognitiveSignal directamente.

        Args:
            signals: Lista de CognitiveSignal (cognition.types)
        """
        dicts = []
        for s in signals:
            fp = _generate_fingerprint(s.summary, s.source)
            dicts.append({
                "fingerprint": fp,
                "priority": s.priority,
                "timestamp": s.timestamp,
                "summary": s.summary,
                "source": s.source,
            })
        return self.score_signals(dicts)

    # ------------------------------------------------------------------
    # Filtros
    # ------------------------------------------------------------------

    def filter_by_level(
        self,
        scores: List[RelevanceScore],
        level: RelevanceLevel,
    ) -> List[RelevanceScore]:
        """Filtra scores por nivel de relevancia."""
        return [s for s in scores if s.level == level]

    def filter_high(self, scores: List[RelevanceScore]) -> List[RelevanceScore]:
        """Solo señales de alta relevancia."""
        return self.filter_by_level(scores, RelevanceLevel.HIGH)

    def filter_actionable(self, scores: List[RelevanceScore]) -> List[RelevanceScore]:
        """Señales HIGH + MEDIUM (requieren atención)."""
        return [s for s in scores if s.level in (RelevanceLevel.HIGH, RelevanceLevel.MEDIUM)]

    # ------------------------------------------------------------------
    # Estadísticas
    # ------------------------------------------------------------------

    def summarize(self, scores: List[RelevanceScore]) -> Dict[str, Any]:
        """Resumen estadístico de un batch de scores."""
        if not scores:
            return {"total": 0, "high": 0, "medium": 0, "low": 0, "avg": 0.0}

        by_level = {level.value: 0 for level in RelevanceLevel}
        total_relevance = 0.0

        for s in scores:
            by_level[s.level.value] += 1
            total_relevance += s.relevance

        return {
            "total": len(scores),
            "high": by_level["HIGH"],
            "medium": by_level["MEDIUM"],
            "low": by_level["LOW"],
            "avg": round(total_relevance / len(scores), 4),
            "max": round(max(s.relevance for s in scores), 4),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_fingerprint(summary: str, source: str = "") -> str:
    """Genera un fingerprint determinístico desde summary + source."""
    content = f"{source}:{summary}".lower().strip()
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_engine: Optional[RelevanceEngine] = None


def get_relevance_engine() -> RelevanceEngine:
    """Obtener la instancia singleton del motor de relevancia."""
    global _engine
    if _engine is None:
        _engine = RelevanceEngine()
    return _engine
