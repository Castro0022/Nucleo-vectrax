"""
Vectrax Core — Hypothesis Engine
====================================
Motor de hipótesis persistente con ciclo de vida completo.
Extiende el concepto de ScenarioEngine (cognition.reasoning.scenario_engine)
agregando: persistencia, estados, evidencia, evolución, y registro en ledger.

Una hipótesis NO es un hecho. Siempre se trata como conjetura hasta
que la evidencia la confirme o refute.

Principio P-005: "Propón soluciones, nunca supongas verdades.
Toda hipótesis debe ser verificable."

Capa: 5 — Hipótesis e Imaginación Controlada
Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.operator.hypothesis_engine")


# ---------------------------------------------------------------------------
# Estado del ciclo de vida
# ---------------------------------------------------------------------------

class HypothesisStatus(str, Enum):
    """Estado en el ciclo de vida de una hipótesis."""
    PROPOSED = "proposed"       # Recién generada, sin evidencia
    TESTING = "testing"         # En proceso de verificación
    CONFIRMED = "confirmed"     # Evidencia suficiente a favor
    REFUTED = "refuted"         # Evidencia en contra
    EVOLVED = "evolved"         # Transformada en una nueva hipótesis
    ARCHIVED = "archived"       # Ya no relevante, pero preservada


class EvidenceType(str, Enum):
    """Tipo de evidencia que soporta o contradice una hipótesis."""
    OBSERVATION = "observation"      # Algo observado en el entorno
    METRIC = "metric"                # Dato medible (score, count, etc.)
    PRECEDENT = "precedent"          # Decisión pasada similar
    CONTRADICTION = "contradiction"  # Dato que refuta
    CONFIRMATION = "confirmation"    # Dato que confirma
    EXTERNAL = "external"            # Fuente externa (conector, API)


# ---------------------------------------------------------------------------
# Evidencia
# ---------------------------------------------------------------------------

@dataclass
class Evidence:
    """Un punto de evidencia asociado a una hipótesis."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    evidence_type: EvidenceType = EvidenceType.OBSERVATION
    description: str = ""
    weight: float = 0.5       # 0-1, cuánto aporta a confirmar/refutar
    supports: bool = True     # True = a favor, False = en contra
    source: str = ""          # Origen de la evidencia
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "evidence_type": self.evidence_type.value,
            "description": self.description,
            "weight": round(self.weight, 4),
            "supports": self.supports,
            "source": self.source,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Hipótesis
# ---------------------------------------------------------------------------

@dataclass
class Hypothesis:
    """
    Una hipótesis con ciclo de vida completo.

    Campos clave:
    - confidence: 0-1, calculado dinámicamente a partir de la evidencia
    - status: ciclo de vida (PROPOSED → TESTING → CONFIRMED/REFUTED/EVOLVED)
    - evidence: lista de puntos de evidencia
    - parent_id: si evolucionó desde otra hipótesis
    """
    id: str = field(default_factory=lambda: f"HYP-{uuid.uuid4().hex[:8].upper()}")
    description: str = ""
    context: str = ""                # Contexto que originó la hipótesis
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    confidence: float = 0.5          # 0-1, nivel de confianza actual
    evidence: List[Evidence] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    parent_id: Optional[str] = None  # ID de la hipótesis madre (si evolucionó)
    children_ids: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    resolved_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "context": self.context,
            "status": self.status.value,
            "confidence": round(self.confidence, 4),
            "evidence_count": len(self.evidence),
            "evidence": [e.to_dict() for e in self.evidence],
            "tags": self.tags,
            "parent_id": self.parent_id,
            "children_ids": self.children_ids,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "resolved_at": self.resolved_at,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Umbrales de confianza
# ---------------------------------------------------------------------------

CONFIRM_THRESHOLD = 0.80   # ≥ 0.80 → puede ser confirmada
REFUTE_THRESHOLD = 0.20    # ≤ 0.20 → puede ser refutada
MIN_EVIDENCE = 2           # Mínimo de evidencias antes de resolver


# ---------------------------------------------------------------------------
# Motor de hipótesis
# ---------------------------------------------------------------------------

class HypothesisEngine:
    """
    Motor de hipótesis persistente con evolución.

    Responsabilidades:
    - Crear hipótesis a partir de contexto
    - Agregar evidencia y recalcular confianza
    - Transicionar estados según evidencia
    - Evolucionar hipótesis (generar hijas con ajustes)
    - Consultar hipótesis por estado, confianza, tags
    - Registrar transiciones en ledger
    """

    def __init__(self) -> None:
        self._hypotheses: Dict[str, Hypothesis] = {}
        self._resolved_count: int = 0
        logger.info("HypothesisEngine initialized")

    # -- Crear --------------------------------------------------------------

    def propose(
        self,
        description: str,
        *,
        context: str = "",
        initial_confidence: float = 0.5,
        tags: Optional[List[str]] = None,
        parent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Hypothesis:
        """
        Crea una nueva hipótesis en estado PROPOSED.
        """
        hyp = Hypothesis(
            description=description,
            context=context,
            confidence=max(0.0, min(1.0, initial_confidence)),
            tags=tags or [],
            parent_id=parent_id,
            metadata=metadata or {},
        )

        # Si tiene padre, registrar como hijo
        if parent_id and parent_id in self._hypotheses:
            self._hypotheses[parent_id].children_ids.append(hyp.id)

        self._hypotheses[hyp.id] = hyp
        self._record_transition(hyp, "proposed", "New hypothesis created")

        logger.info(
            "Hypothesis proposed: %s | confidence=%.2f | '%s'",
            hyp.id, hyp.confidence, description[:60],
        )
        return hyp

    # -- Evidencia ----------------------------------------------------------

    def add_evidence(
        self,
        hypothesis_id: str,
        evidence: Evidence,
    ) -> Optional[Hypothesis]:
        """
        Agrega evidencia a una hipótesis y recalcula confianza.
        Retorna None si la hipótesis no existe o ya está resuelta.
        """
        hyp = self._hypotheses.get(hypothesis_id)
        if hyp is None:
            logger.warning("Hypothesis not found: %s", hypothesis_id)
            return None

        if hyp.status in (
            HypothesisStatus.CONFIRMED,
            HypothesisStatus.REFUTED,
            HypothesisStatus.ARCHIVED,
        ):
            logger.warning(
                "Cannot add evidence to %s hypothesis: %s",
                hyp.status.value, hypothesis_id,
            )
            return None

        hyp.evidence.append(evidence)
        hyp.updated_at = time.time()

        # Transicionar a TESTING si estaba en PROPOSED
        if hyp.status == HypothesisStatus.PROPOSED:
            hyp.status = HypothesisStatus.TESTING

        # Recalcular confianza
        hyp.confidence = self._calculate_confidence(hyp)

        logger.info(
            "Evidence added to %s: %s (supports=%s, weight=%.2f) → confidence=%.2f",
            hypothesis_id, evidence.evidence_type.value,
            evidence.supports, evidence.weight, hyp.confidence,
        )
        return hyp

    def _calculate_confidence(self, hyp: Hypothesis) -> float:
        """
        Calcula la confianza basada en toda la evidencia.
        Fórmula: promedio ponderado de evidencia a favor vs en contra.
        """
        if not hyp.evidence:
            return 0.5

        support_weight = 0.0
        against_weight = 0.0

        for e in hyp.evidence:
            if e.supports:
                support_weight += e.weight
            else:
                against_weight += e.weight

        total = support_weight + against_weight
        if total == 0:
            return 0.5

        return round(support_weight / total, 4)

    # -- Resolución ---------------------------------------------------------

    def resolve(self, hypothesis_id: str) -> Optional[Hypothesis]:
        """
        Intenta resolver una hipótesis basándose en su confianza actual.
        Solo resuelve si hay suficiente evidencia y la confianza
        supera los umbrales.
        """
        hyp = self._hypotheses.get(hypothesis_id)
        if hyp is None:
            return None

        if hyp.status not in (HypothesisStatus.PROPOSED, HypothesisStatus.TESTING):
            return hyp  # Ya resuelta

        if len(hyp.evidence) < MIN_EVIDENCE:
            logger.info(
                "Not enough evidence to resolve %s (%d/%d)",
                hypothesis_id, len(hyp.evidence), MIN_EVIDENCE,
            )
            return hyp

        now = time.time()
        old_status = hyp.status

        if hyp.confidence >= CONFIRM_THRESHOLD:
            hyp.status = HypothesisStatus.CONFIRMED
            hyp.resolved_at = now
            self._resolved_count += 1
            self._record_transition(
                hyp, "confirmed",
                f"Confidence {hyp.confidence:.2f} ≥ {CONFIRM_THRESHOLD}",
            )
        elif hyp.confidence <= REFUTE_THRESHOLD:
            hyp.status = HypothesisStatus.REFUTED
            hyp.resolved_at = now
            self._resolved_count += 1
            self._record_transition(
                hyp, "refuted",
                f"Confidence {hyp.confidence:.2f} ≤ {REFUTE_THRESHOLD}",
            )
        else:
            logger.info(
                "Hypothesis %s inconclusive: confidence=%.2f",
                hypothesis_id, hyp.confidence,
            )

        hyp.updated_at = now
        return hyp

    # -- Evolución ----------------------------------------------------------

    def evolve(
        self,
        hypothesis_id: str,
        new_description: str,
        *,
        reason: str = "",
        adjust_confidence: float = 0.0,
    ) -> Optional[Hypothesis]:
        """
        Evoluciona una hipótesis: marca la original como EVOLVED y
        crea una nueva hipótesis hija con descripción ajustada.
        """
        parent = self._hypotheses.get(hypothesis_id)
        if parent is None:
            return None

        if parent.status in (HypothesisStatus.CONFIRMED, HypothesisStatus.ARCHIVED):
            logger.warning("Cannot evolve %s hypothesis", parent.status.value)
            return None

        # Marcar padre como evolucionado
        parent.status = HypothesisStatus.EVOLVED
        parent.updated_at = time.time()

        # Crear hija
        child_confidence = max(0.0, min(1.0, parent.confidence + adjust_confidence))
        child = self.propose(
            description=new_description,
            context=f"Evolved from {hypothesis_id}: {reason}",
            initial_confidence=child_confidence,
            tags=parent.tags.copy(),
            parent_id=hypothesis_id,
        )

        self._record_transition(
            parent, "evolved",
            f"Evolved into {child.id}: {reason}",
        )

        logger.info(
            "Hypothesis evolved: %s → %s | reason=%s",
            hypothesis_id, child.id, reason,
        )
        return child

    # -- Archivar -----------------------------------------------------------

    def archive(self, hypothesis_id: str, *, reason: str = "") -> bool:
        """Archiva una hipótesis (ya no relevante pero preservada)."""
        hyp = self._hypotheses.get(hypothesis_id)
        if hyp is None:
            return False

        hyp.status = HypothesisStatus.ARCHIVED
        hyp.updated_at = time.time()
        self._record_transition(hyp, "archived", reason)
        return True

    # -- Consultas ----------------------------------------------------------

    def get(self, hypothesis_id: str) -> Optional[Hypothesis]:
        """Obtiene una hipótesis por ID."""
        return self._hypotheses.get(hypothesis_id)

    def get_by_status(self, status: HypothesisStatus) -> List[Hypothesis]:
        """Filtra hipótesis por estado."""
        return [h for h in self._hypotheses.values() if h.status == status]

    def get_active(self) -> List[Hypothesis]:
        """Hipótesis que aún no se han resuelto."""
        active = {HypothesisStatus.PROPOSED, HypothesisStatus.TESTING}
        return [h for h in self._hypotheses.values() if h.status in active]

    def get_by_tag(self, tag: str) -> List[Hypothesis]:
        """Filtra hipótesis que contengan un tag."""
        return [h for h in self._hypotheses.values() if tag in h.tags]

    def get_high_confidence(self, threshold: float = 0.7) -> List[Hypothesis]:
        """Hipótesis activas con alta confianza."""
        return [
            h for h in self.get_active()
            if h.confidence >= threshold
        ]

    def get_lineage(self, hypothesis_id: str) -> List[Hypothesis]:
        """
        Obtiene la cadena evolutiva completa de una hipótesis
        (ancestros + descendientes).
        """
        chain: List[Hypothesis] = []
        hyp = self._hypotheses.get(hypothesis_id)
        if hyp is None:
            return chain

        # Subir a la raíz
        root = hyp
        while root.parent_id and root.parent_id in self._hypotheses:
            root = self._hypotheses[root.parent_id]

        # Recorrer hacia abajo
        def _collect(h: Hypothesis) -> None:
            chain.append(h)
            for child_id in h.children_ids:
                child = self._hypotheses.get(child_id)
                if child:
                    _collect(child)

        _collect(root)
        return chain

    # -- Estadísticas -------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """Estadísticas del motor de hipótesis."""
        all_hyps = list(self._hypotheses.values())
        by_status: Dict[str, int] = {}
        for h in all_hyps:
            by_status[h.status.value] = by_status.get(h.status.value, 0) + 1

        active = self.get_active()
        avg_confidence = (
            sum(h.confidence for h in active) / len(active)
            if active else 0.0
        )

        return {
            "total": len(all_hyps),
            "active": len(active),
            "resolved": self._resolved_count,
            "by_status": by_status,
            "avg_active_confidence": round(avg_confidence, 4),
            "total_evidence": sum(len(h.evidence) for h in all_hyps),
        }

    # -- Ledger -------------------------------------------------------------

    def _record_transition(
        self,
        hyp: Hypothesis,
        transition: str,
        reason: str,
    ) -> None:
        """Registra una transición de hipótesis en el ledger."""
        try:
            from core.operator import ledger_bridge as ledger
            ledger.record_event(
                action=f"hypothesis:{transition}",
                category=ledger.EventCategory.REASONING,
                risk_zone=ledger.RiskZone.GREEN,
                reason=reason,
                details={
                    "hypothesis_id": hyp.id,
                    "description": hyp.description[:120],
                    "status": hyp.status.value,
                    "confidence": hyp.confidence,
                    "evidence_count": len(hyp.evidence),
                    "parent_id": hyp.parent_id,
                },
            )
        except Exception as exc:
            logger.warning("Could not record hypothesis to ledger: %s", exc)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_engine: Optional[HypothesisEngine] = None


def get_hypothesis_engine() -> HypothesisEngine:
    """Obtener la instancia singleton del motor de hipótesis."""
    global _engine
    if _engine is None:
        _engine = HypothesisEngine()
    return _engine


def reset_hypothesis_engine() -> None:
    """Reset para testing. NO usar en producción."""
    global _engine
    _engine = None
