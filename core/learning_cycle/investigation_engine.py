"""
Vectrax INVESTIGATION_ENGINE
===============================
Se activa cuando el AnomalyDetector marca una señal.
Busca contexto en tres fuentes y genera hipótesis múltiples.

Fuentes de contexto:
  a) Memoria histórica — EpisodicLedger + LearnedRulesStore
  b) Estado actual — state_manager + candidatos AscensionGate
  c) Fuentes externas — adaptable via callback

Cada hipótesis se crea con tags del tipo de anomalía para trazabilidad
completa hasta la señal original.

Privacy: nunca almacena contenido raw, PII ni credenciales.

Creado: 2026-03-28
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional

from core.learning_cycle.anomaly_detector import AnomalySignal

logger = logging.getLogger("vectrax.learning_cycle.investigation")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ContextSource:
    """Contexto recolectado de una fuente durante investigación."""
    source_name: str = ""          # "historical_memory", "current_state", "external"
    data: Dict[str, Any] = field(default_factory=dict)
    relevance: float = 0.0        # 0-1
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InvestigationResult:
    """Resultado completo de una investigación."""
    anomaly_id: str = ""
    anomaly_type: str = ""
    context_sources: List[ContextSource] = field(default_factory=list)
    hypothesis_ids: List[str] = field(default_factory=list)
    hypotheses_generated: int = 0
    investigation_time_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "anomaly_id": self.anomaly_id,
            "anomaly_type": self.anomaly_type,
            "context_sources": [c.to_dict() for c in self.context_sources],
            "hypothesis_ids": self.hypothesis_ids,
            "hypotheses_generated": self.hypotheses_generated,
            "investigation_time_ms": round(self.investigation_time_ms, 2),
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Investigation Engine
# ---------------------------------------------------------------------------

# Tipo para callback de fuentes externas
ExternalSourceCallback = Callable[[AnomalySignal], List[ContextSource]]


class InvestigationEngine:
    """
    Motor de investigación que se activa ante anomalías.

    Recolecta contexto de tres fuentes:
      1. Memoria histórica (EpisodicLedger + LearnedRulesStore)
      2. Estado actual (state_manager + AscensionGate)
      3. Fuentes externas (via callback registrado)

    Genera múltiples hipótesis por anomalía usando el HypothesisEngine.

    Usage::

        engine = InvestigationEngine()
        result = engine.investigate(anomaly_signal)
        # result.hypothesis_ids contiene los IDs generados
    """

    MAX_HYPOTHESES_PER_ANOMALY = 3

    def __init__(
        self,
        external_source: Optional[ExternalSourceCallback] = None,
    ) -> None:
        self._external_source = external_source
        self._total_investigations: int = 0
        self._total_hypotheses: int = 0
        logger.info("InvestigationEngine initialized")

    def register_external_source(self, callback: ExternalSourceCallback) -> None:
        """Registra un callback para fuentes externas."""
        self._external_source = callback

    # =====================================================================
    # Main API
    # =====================================================================

    def investigate(self, anomaly: AnomalySignal) -> InvestigationResult:
        """
        Investiga una anomalía: recolecta contexto y genera hipótesis.

        Pasos:
          1. Buscar en memoria histórica
          2. Consultar estado actual
          3. Consultar fuentes externas (si disponible)
          4. Generar hipótesis basadas en el contexto
        """
        t0 = time.time()

        result = InvestigationResult(
            anomaly_id=anomaly.id,
            anomaly_type=anomaly.anomaly_type,
        )

        # --- 1. Memoria histórica ---
        historical = self._search_historical_memory(anomaly)
        if historical:
            result.context_sources.append(historical)

        # --- 2. Estado actual ---
        current = self._search_current_state(anomaly)
        if current:
            result.context_sources.append(current)

        # --- 3. Fuentes externas ---
        if self._external_source:
            try:
                external_contexts = self._external_source(anomaly)
                result.context_sources.extend(external_contexts)
            except Exception as exc:
                logger.warning("External source error: %s", exc)

        # --- 4. Generar hipótesis ---
        hypothesis_ids = self._generate_hypotheses(anomaly, result.context_sources)
        result.hypothesis_ids = hypothesis_ids
        result.hypotheses_generated = len(hypothesis_ids)

        result.investigation_time_ms = (time.time() - t0) * 1000
        self._total_investigations += 1
        self._total_hypotheses += len(hypothesis_ids)

        logger.info(
            "Investigation complete: anomaly=%s type=%s hypotheses=%d (%.1fms)",
            anomaly.id, anomaly.anomaly_type, len(hypothesis_ids),
            result.investigation_time_ms,
        )

        return result

    def investigate_batch(
        self, anomalies: List[AnomalySignal],
    ) -> List[InvestigationResult]:
        """Investiga múltiples anomalías."""
        return [self.investigate(a) for a in anomalies]

    # =====================================================================
    # Context search (privado)
    # =====================================================================

    def _search_historical_memory(self, anomaly: AnomalySignal) -> Optional[ContextSource]:
        """Busca en EpisodicLedger y LearnedRulesStore."""
        data: Dict[str, Any] = {
            "related_events": [],
            "related_rules": [],
        }
        relevance = 0.0

        # --- Episodic ledger ---
        try:
            from core.learn.episodic import get_ledger
            ledger = get_ledger()
            recent = ledger.read_recent(30)

            related = [
                ev for ev in recent
                if (
                    anomaly.anomaly_type in ev.get("summary", "")
                    or anomaly.context.get("intent", "") in ev.get("summary", "")
                )
            ]
            data["related_events"] = [
                {"event_type": ev.get("event_type"), "summary": ev.get("summary", "")[:100]}
                for ev in related[:5]
            ]
            if related:
                relevance = min(1.0, len(related) * 0.2)
        except Exception as exc:
            logger.warning("Historical memory (ledger) error: %s", exc)

        # --- Learned rules ---
        try:
            from core.learn.learned_rules import get_rules_store
            store = get_rules_store()
            active_rules = store.get_active_rules()

            matching_rules = [
                r for r in active_rules
                if (
                    anomaly.anomaly_type in r.get("pattern", "")
                    or anomaly.context.get("intent", "") in r.get("pattern", "")
                )
            ]
            data["related_rules"] = [
                {"id": r.get("id"), "pattern": r.get("pattern", "")[:80]}
                for r in matching_rules[:3]
            ]
            if matching_rules:
                relevance = min(1.0, relevance + len(matching_rules) * 0.3)
        except Exception as exc:
            logger.warning("Historical memory (rules) error: %s", exc)

        if not data["related_events"] and not data["related_rules"]:
            return None

        return ContextSource(
            source_name="historical_memory",
            data=data,
            relevance=round(relevance, 2),
        )

    def _search_current_state(self, anomaly: AnomalySignal) -> Optional[ContextSource]:
        """Busca en state_manager y AscensionGate."""
        data: Dict[str, Any] = {}
        relevance = 0.0

        # --- State manager ---
        try:
            from core import state_manager
            state = state_manager.load()
            data["learning_mode"] = state.get("learning_mode", "OBSERVATION")
            data["learning_cycles"] = state.get("learning_cycles", 0)
            data["hypotheses_generated"] = state.get("hypotheses_generated", 0)
            data["rules_learned"] = state.get("rules_learned", 0)
            relevance = 0.3
        except Exception as exc:
            logger.warning("Current state (state_manager) error: %s", exc)

        # --- Ascension gate candidates ---
        try:
            from core.learn.ascension_gate import get_gate
            gate = get_gate()
            counts = gate.count_by_state()
            data["candidate_counts"] = counts
            if sum(counts.values()) > 0:
                relevance = min(1.0, relevance + 0.2)
        except Exception as exc:
            logger.warning("Current state (gate) error: %s", exc)

        if not data:
            return None

        return ContextSource(
            source_name="current_state",
            data=data,
            relevance=round(relevance, 2),
        )

    # =====================================================================
    # Hypothesis generation
    # =====================================================================

    def _generate_hypotheses(
        self,
        anomaly: AnomalySignal,
        context_sources: List[ContextSource],
    ) -> List[str]:
        """
        Genera hipótesis múltiples basadas en la anomalía y el contexto.

        Usa el HypothesisEngine existente para crear las hipótesis.
        """
        try:
            from core.operator.hypothesis_engine import get_hypothesis_engine
            engine = get_hypothesis_engine()
        except Exception as exc:
            logger.error("Cannot access HypothesisEngine: %s", exc)
            return []

        hypothesis_ids: List[str] = []
        context_summary = self._summarize_context(context_sources)

        # --- Hipótesis por tipo de anomalía ---
        generators = {
            "frequency_spike": self._hypotheses_for_frequency_spike,
            "new_pattern": self._hypotheses_for_new_pattern,
            "distribution_shift": self._hypotheses_for_distribution_shift,
            "repetition_burst": self._hypotheses_for_repetition_burst,
            "outlier_input": self._hypotheses_for_outlier_input,
        }

        generator = generators.get(anomaly.anomaly_type)
        if generator:
            descriptions = generator(anomaly, context_summary)
        else:
            descriptions = [
                f"Anomalía no clasificada ({anomaly.anomaly_type}): {anomaly.description}"
            ]

        # --- Crear hipótesis ---
        for desc in descriptions[:self.MAX_HYPOTHESES_PER_ANOMALY]:
            try:
                hyp = engine.propose(
                    description=desc,
                    context=f"Investigación de anomalía {anomaly.id}: {context_summary}",
                    initial_confidence=0.4,
                    tags=[
                        anomaly.anomaly_type,
                        f"severity:{anomaly.severity}",
                        "learning_cycle",
                    ],
                    metadata={
                        "anomaly_id": anomaly.id,
                        "anomaly_type": anomaly.anomaly_type,
                        "severity": anomaly.severity,
                        "source_pipeline": "learning_cycle",
                    },
                )
                hypothesis_ids.append(hyp.id)
            except Exception as exc:
                logger.warning("Failed to create hypothesis: %s", exc)

        return hypothesis_ids

    # =====================================================================
    # Hypothesis generators by anomaly type
    # =====================================================================

    def _hypotheses_for_frequency_spike(
        self, anomaly: AnomalySignal, context: str,
    ) -> List[str]:
        intent = anomaly.context.get("intent", "desconocido")
        count = anomaly.context.get("count", 0)
        factor = anomaly.context.get("factor", 0)
        return [
            (
                f"El intent '{intent}' muestra un spike de frecuencia "
                f"({count}x, factor {factor}x). "
                f"Puede indicar un cambio en el comportamiento del usuario."
            ),
            (
                f"El aumento en '{intent}' podría ser un patrón temporal "
                f"(sesión activa) y no una tendencia permanente."
            ),
            (
                f"Spike en '{intent}' podría indicar un problema en el router "
                f"que clasifica múltiples inputs bajo el mismo intent."
            ),
        ]

    def _hypotheses_for_new_pattern(
        self, anomaly: AnomalySignal, context: str,
    ) -> List[str]:
        return [
            (
                f"Nuevo patrón detectado: {anomaly.description}. "
                f"Podría representar una nueva capacidad requerida."
            ),
            (
                f"Nuevo patrón: {anomaly.description}. "
                f"Podría ser un error de clasificación en el router."
            ),
        ]

    def _hypotheses_for_distribution_shift(
        self, anomaly: AnomalySignal, context: str,
    ) -> List[str]:
        shifts = anomaly.context.get("shifts", [])
        shift_desc = ", ".join(
            f"{s.get('intent', '?')} ({s.get('delta', 0):.0%})"
            for s in shifts[:3]
        )
        return [
            (
                f"Cambio en distribución de intents: {shift_desc}. "
                f"Podría reflejar evolución en las necesidades del usuario."
            ),
            (
                f"Shift en distribución: {shift_desc}. "
                f"Podría indicar degradación en la detección de intents."
            ),
        ]

    def _hypotheses_for_repetition_burst(
        self, anomaly: AnomalySignal, context: str,
    ) -> List[str]:
        count = anomaly.context.get("count", 0)
        intent = anomaly.context.get("intent", "?")
        return [
            (
                f"Ráfaga de {count} entradas idénticas (intent={intent}). "
                f"Podría indicar reintentos automáticos o bot."
            ),
            (
                f"Repetición x{count} de intent '{intent}'. "
                f"El usuario podría estar insatisfecho con la respuesta."
            ),
        ]

    def _hypotheses_for_outlier_input(
        self, anomaly: AnomalySignal, context: str,
    ) -> List[str]:
        length = anomaly.context.get("length", 0)
        z_score = anomaly.context.get("z_score", 0)
        return [
            (
                f"Input atípico de {length} chars (z={z_score}). "
                f"Podría ser contenido pegado (documento, código, URL larga)."
            ),
            (
                f"Longitud anormal ({length} chars). "
                f"Podría indicar un intento de injection o test de límites."
            ),
        ]

    # =====================================================================
    # Helpers
    # =====================================================================

    @staticmethod
    def _summarize_context(sources: List[ContextSource]) -> str:
        """Resume las fuentes de contexto en una línea."""
        parts = []
        for src in sources:
            parts.append(f"{src.source_name}(relevance={src.relevance})")
        return "; ".join(parts) if parts else "sin contexto adicional"

    # =====================================================================
    # Status
    # =====================================================================

    def stats(self) -> Dict[str, Any]:
        return {
            "total_investigations": self._total_investigations,
            "total_hypotheses_generated": self._total_hypotheses,
            "has_external_source": self._external_source is not None,
            "avg_hypotheses_per_investigation": (
                round(self._total_hypotheses / max(self._total_investigations, 1), 2)
            ),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_engine: Optional[InvestigationEngine] = None


def get_investigation_engine() -> InvestigationEngine:
    """Obtener la instancia singleton del InvestigationEngine."""
    global _engine
    if _engine is None:
        _engine = InvestigationEngine()
    return _engine


def reset_investigation_engine() -> None:
    """Reset para testing."""
    global _engine
    _engine = None
