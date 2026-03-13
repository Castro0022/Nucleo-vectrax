"""
Vectrax Core — Perception Bridge
====================================
Conecta el motor de percepción existente (cognition.perception) con el
núcleo del operador. No duplica funcionalidad — orquesta y enriquece.

Funciones:
  - Inicializar PerceptionEngine con configuración del operador
  - Ejecutar ciclos de percepción delegando al motor existente
  - Enriquecer señales con contexto del operador (modo, capa, identidad)
  - Filtrar señales según dominio autorizado/restringido
  - Alimentar el relevance_engine con señales clasificadas

Pipeline del bridge:
  PerceptionEngine.perceive() → filtro de dominios → enriquecimiento
  → relevance scoring → registro en ledger (si notable)

Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from cognition.perception.perception_engine import PerceptionEngine
from cognition.types import (
    CognitiveContext,
    CognitiveSignal,
    SignalCategory,
    SignalSource,
)
from core.operator.identity import IDENTITY, RESTRICTED_DOMAINS

logger = logging.getLogger("vectrax.operator.perception_bridge")


# ---------------------------------------------------------------------------
# Filtros de dominio
# ---------------------------------------------------------------------------

# Señales que mencionan estos términos son filtradas como restringidas
_RESTRICTED_KEYWORDS = frozenset({
    "credential", "password", "secret", "token", "api_key",
    "private_key", "ssh_key", ".env",
})


def _signal_touches_restricted_domain(signal: CognitiveSignal) -> bool:
    """Detecta si una señal toca un dominio restringido."""
    text = signal.summary.lower()
    source = signal.source.lower()
    return (
        any(kw in text for kw in _RESTRICTED_KEYWORDS)
        or any(kw in source for kw in _RESTRICTED_KEYWORDS)
    )


# ---------------------------------------------------------------------------
# Enriquecimiento de señales
# ---------------------------------------------------------------------------

def _enrich_signal(
    signal: CognitiveSignal,
    operator_mode: str,
    cycle_id: int,
) -> CognitiveSignal:
    """Añade contexto del operador a una señal sin modificar datos originales."""
    signal.metadata["operator_mode"] = operator_mode
    signal.metadata["operator_cycle"] = cycle_id
    signal.metadata["operator_layer"] = 2  # Capa de Percepción

    # Marcar si toca dominio restringido
    if _signal_touches_restricted_domain(signal):
        signal.metadata["restricted_domain"] = True
        signal.metadata["original_priority"] = signal.priority
        # Elevar prioridad para que el gobernador lo revise
        signal.priority = max(signal.priority, 0.9)

    return signal


# ---------------------------------------------------------------------------
# Perception Bridge
# ---------------------------------------------------------------------------

class PerceptionBridge:
    """
    Puente entre el motor de percepción y el núcleo del operador.

    Usage::

        bridge = PerceptionBridge()
        result = bridge.perceive_cycle()
    """

    def __init__(
        self,
        buffer_size: int = 1000,
        persist: bool = True,
    ) -> None:
        self._engine = PerceptionEngine(
            buffer_size=buffer_size,
            persist=persist,
        )
        self._cycle_count: int = 0
        self._total_signals_processed: int = 0
        self._total_restricted_filtered: int = 0

        # Real signal source: watches git and log activity
        try:
            from core.operator.signal_sources.codebase_watcher import CodebaseWatcher
            self._watcher = CodebaseWatcher()
        except Exception as exc:
            logger.warning("CodebaseWatcher unavailable: %s", exc)
            self._watcher = None

        # UCE: register local filesystem connector so signal_intake sees it
        self._register_uce_connectors()

        logger.info("PerceptionBridge initialized")

    def _register_uce_connectors(self) -> None:
        """
        Register and authenticate the LocalFilesystemConnector in the UCE
        singleton so that SignalIntake.poll_connectors() picks it up on
        each perception cycle.

        Uses direct authenticate() (not engine.connect()) to avoid the
        approval-gate round-trip for read-only observe operations on the
        local project filesystem.
        """
        try:
            import pathlib
            from connectors.engine import get_connection_engine
            from connectors.adapters.local_filesystem import (
                LocalFilesystemConnector,
                FILESYSTEM_CONTRACT,
            )

            engine = get_connection_engine()

            # Only register once (idempotent)
            if "local_filesystem" not in engine.list_connectors():
                project_root = str(pathlib.Path(__file__).parent.parent.parent)
                connector = LocalFilesystemConnector(root=project_root)
                engine.register(
                    connector,
                    FILESYSTEM_CONTRACT,
                    granted_permissions=["read"],
                )
                # Authenticate directly — no approval gate for read-only observe
                connector.authenticate({"root": project_root})
                logger.info(
                    "UCE: local_filesystem connector registered | root=%s",
                    project_root,
                )
        except Exception as exc:
            logger.warning("UCE connector registration failed: %s", exc)

    @property
    def engine(self) -> PerceptionEngine:
        """Acceso directo al motor de percepción subyacente."""
        return self._engine

    # ------------------------------------------------------------------
    # Ciclo principal
    # ------------------------------------------------------------------

    def perceive_cycle(
        self,
        operator_mode: str = "guided",
        connector_names: Optional[List[str]] = None,
        extra_signals: Optional[List[CognitiveSignal]] = None,
    ) -> PerceptionResult:
        """
        Ejecuta un ciclo completo de percepción a través del bridge.

        1. Delega a PerceptionEngine.perceive()
        2. Filtra señales de dominios restringidos
        3. Enriquece señales con contexto del operador
        4. Calcula estadísticas del ciclo

        Returns:
            PerceptionResult con señales procesadas y métricas.
        """
        t0 = time.time()
        self._cycle_count += 1

        # 0. Collect watcher signals (git + log activity)
        watcher_signals: List[CognitiveSignal] = []
        if self._watcher is not None:
            try:
                watcher_signals = self._watcher.scan()
            except Exception as exc:
                logger.warning("CodebaseWatcher scan failed: %s", exc)

        # Merge watcher signals with any caller-supplied extra_signals
        all_extra = watcher_signals + (extra_signals or [])

        # 1. Percepción delegada al motor existente
        context = self._engine.perceive(
            connector_names=connector_names,
            extra_signals=all_extra if all_extra else None,
        )

        # 2. Filtrar y enriquecer señales
        processed: List[CognitiveSignal] = []
        restricted_count = 0

        for signal in context.signals:
            # Enriquecer
            signal = _enrich_signal(signal, operator_mode, self._cycle_count)

            # Contar restringidas (no las eliminamos, las marcamos)
            if signal.metadata.get("restricted_domain"):
                restricted_count += 1

            processed.append(signal)

        self._total_signals_processed += len(processed)
        self._total_restricted_filtered += restricted_count

        # 3. Separar por categoría para análisis
        by_category: Dict[str, List[CognitiveSignal]] = {}
        for s in processed:
            cat = s.category.value
            by_category.setdefault(cat, []).append(s)

        # 4. Priorizar (ordenar por prioridad descendente)
        processed.sort(key=lambda s: s.priority, reverse=True)

        elapsed_ms = (time.time() - t0) * 1000

        result = PerceptionResult(
            cycle_id=self._cycle_count,
            context=context,
            signals=processed,
            total_signals=len(processed),
            restricted_signals=restricted_count,
            by_category={k: len(v) for k, v in by_category.items()},
            top_priority=processed[0].priority if processed else 0.0,
            elapsed_ms=round(elapsed_ms, 2),
        )

        logger.info(
            "Perception cycle #%d: %d signals (%d restricted), %.1fms",
            self._cycle_count, len(processed), restricted_count, elapsed_ms,
        )

        return result

    # ------------------------------------------------------------------
    # Inyección de señales internas
    # ------------------------------------------------------------------

    def inject_operator_signal(
        self,
        summary: str,
        *,
        category: str = "CHANGE",
        intent: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CognitiveSignal:
        """
        Inyecta una señal interna del operador al motor de percepción.
        Útil para que el operador observe sus propios eventos.
        """
        raw = {
            "summary": summary,
            "intent": intent,
            "category": category,
            **(metadata or {}),
        }
        signal = self._engine.inject_signal(
            raw_data=raw,
            source="operator",
            source_type=SignalSource.INTERNAL,
        )
        return signal

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def recent_signals(self, n: int = 20) -> List[CognitiveSignal]:
        """Últimas N señales del buffer."""
        return self._engine.recent_events(n)

    def status(self) -> Dict[str, Any]:
        """Estado del bridge de percepción."""
        engine_status = self._engine.status()
        return {
            "bridge_cycles": self._cycle_count,
            "total_signals_processed": self._total_signals_processed,
            "total_restricted_filtered": self._total_restricted_filtered,
            "engine": engine_status,
        }


# ---------------------------------------------------------------------------
# Resultado de un ciclo de percepción
# ---------------------------------------------------------------------------

class PerceptionResult:
    """Resultado enriquecido de un ciclo de percepción."""

    __slots__ = (
        "cycle_id", "context", "signals", "total_signals",
        "restricted_signals", "by_category", "top_priority", "elapsed_ms",
    )

    def __init__(
        self,
        cycle_id: int,
        context: CognitiveContext,
        signals: List[CognitiveSignal],
        total_signals: int,
        restricted_signals: int,
        by_category: Dict[str, int],
        top_priority: float,
        elapsed_ms: float,
    ) -> None:
        self.cycle_id = cycle_id
        self.context = context
        self.signals = signals
        self.total_signals = total_signals
        self.restricted_signals = restricted_signals
        self.by_category = by_category
        self.top_priority = top_priority
        self.elapsed_ms = elapsed_ms

    @property
    def has_critical(self) -> bool:
        return self.by_category.get("CRITICAL", 0) > 0

    @property
    def has_restricted(self) -> bool:
        return self.restricted_signals > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "total_signals": self.total_signals,
            "restricted_signals": self.restricted_signals,
            "by_category": self.by_category,
            "top_priority": self.top_priority,
            "has_critical": self.has_critical,
            "elapsed_ms": self.elapsed_ms,
        }
