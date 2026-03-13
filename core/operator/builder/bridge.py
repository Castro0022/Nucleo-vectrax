"""
Vectrax Core — Builder Bridge
================================
Conecta el subsistema de autoconstrucción (Capa 8) al Bus Universal.

Responsabilidades:
  - Suscribirse a Channels.HYPOTHESIS y Channels.NUCLEUS
  - Cuando se detectan señales de alta relevancia en el codebase,
    ejecutar análisis de patrones con CodeReader + PatternExtractor
  - Emitir hallazgos estructurados en Channels.BUILDER
  - Proponer módulos o correcciones detectadas (sin ejecutar)

El bridge NO ejecuta cambios. Toda propuesta generada queda en
el ApprovalGate hasta aprobación explícita del creador (P-004).

Capa: 8 — Autoconstrucción de Módulos
Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import pathlib
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.operator.builder.bridge")

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.parent


# ---------------------------------------------------------------------------
# Builder Bridge
# ---------------------------------------------------------------------------

class BuilderBridge:
    """
    Puente entre el bus universal y el subsistema de autoconstrucción.

    Suscribe al bus para recibir notificaciones de:
      - cycle_complete con high_relevance > 0  → análisis de patrones
      - cycle_perceived con señales codebase_*  → inspección de archivos

    Emite en Channels.BUILDER:
      - builder_analysis_ready  — análisis de patrones del codebase
      - builder_pattern_found   — patrón arquitectónico detectado

    Usage::

        bridge = BuilderBridge()
        bridge.subscribe(bus)       # llamar en activate()
    """

    def __init__(self) -> None:
        self._analysis_count: int = 0
        self._last_analysis_time: Optional[float] = None
        self._min_interval: float = 30.0   # análisis máximo cada 30s
        self._subscription_ids: List[str] = []
        logger.info("BuilderBridge initialized")

    # -- Bus subscription --------------------------------------------------

    def subscribe(self, bus: Any) -> None:
        """
        Registra los handlers en el bus universal.
        Llamar una vez desde OperatorRuntime.activate().
        """
        from core.operator.universal_bus import Channels, EventPriority

        # Escuchar ciclos completos con señales de alta relevancia
        sid1 = bus.subscribe(
            Channels.NUCLEUS,
            self._on_cycle_complete,
            subscriber_name="builder_bridge",
            filter_type="cycle_complete",
            min_priority=EventPriority.NORMAL,
        )
        # Escuchar señales de percepción con actividad de codebase
        sid2 = bus.subscribe(
            Channels.PERCEPTION,
            self._on_cycle_perceived,
            subscriber_name="builder_bridge",
            filter_type="cycle_perceived",
            min_priority=EventPriority.NORMAL,
        )
        self._subscription_ids.extend([sid1, sid2])
        logger.info(
            "BuilderBridge subscribed | nucleus_sub=%s | perception_sub=%s",
            sid1, sid2,
        )

    # -- Bus event handlers ------------------------------------------------

    def _on_cycle_complete(self, event: Any) -> None:
        """
        Reacciona a cycle_complete cuando hay hipótesis activas.
        Dispara análisis de patrones si hay señales de alta relevancia
        relacionadas con codebase.
        """
        payload = event.payload or {}
        outcome = payload.get("outcome", "idle")

        # Solo analizar cuando hay actividad real (proposed u observed)
        if outcome not in ("proposed", "observed"):
            return

        # Respetar intervalo mínimo entre análisis
        now = time.time()
        if (self._last_analysis_time and
                now - self._last_analysis_time < self._min_interval):
            return

        self._run_pattern_analysis(event)

    def _on_cycle_perceived(self, event: Any) -> None:
        """
        Reacciona cuando la percepción detecta señales relevantes.
        Analiza archivos modificados recientemente en el codebase.
        """
        payload = event.payload or {}
        signals = payload.get("signals", 0)
        if signals == 0:
            return

        # Solo si hay señales reales — no disparar en ciclos idle
        now = time.time()
        if (self._last_analysis_time and
                now - self._last_analysis_time < self._min_interval):
            return

        # Análisis ligero: listar archivos modificados recientemente
        self._run_file_scan(event)

    # -- Analysis methods --------------------------------------------------

    def _run_pattern_analysis(self, trigger_event: Any) -> None:
        """
        Ejecuta análisis de patrones sobre el codebase y emite
        un resumen en Channels.BUILDER.
        """
        try:
            from core.operator.builder.code_reader import CodeReader
            from core.operator.builder.pattern_extractor import PatternExtractor
            from core.operator.universal_bus import (
                Channels, EventPriority, get_universal_bus,
            )

            self._analysis_count += 1
            self._last_analysis_time = time.time()
            t0 = time.time()

            reader = CodeReader()
            extractor = PatternExtractor(reader)

            # Analizar capas clave del operador
            targets = [
                "core/operator",
                "cognition/perception",
                "connectors",
            ]
            findings: List[Dict[str, Any]] = []
            for target in targets:
                target_path = _PROJECT_ROOT / target
                if not target_path.exists():
                    continue
                try:
                    report = extractor.analyze(str(target_path))
                    if report and hasattr(report, "patterns") and report.patterns:
                        findings.append({
                            "target": target,
                            "patterns": len(report.patterns),
                            "summary": getattr(report, "summary", f"{len(report.patterns)} patterns"),
                        })
                except Exception as exc:
                    logger.debug("PatternExtractor error on %s: %s", target, exc)

            elapsed_ms = round((time.time() - t0) * 1000, 1)

            bus = get_universal_bus()
            bus.emit(
                channel=Channels.BUILDER,
                event_type="builder_analysis_ready",
                source_layer=8,
                priority=EventPriority.NORMAL,
                payload={
                    "analysis_number": self._analysis_count,
                    "trigger_cycle": trigger_event.payload.get("cycle_id", "?"),
                    "targets_analyzed": len(findings),
                    "findings": findings,
                    "elapsed_ms": elapsed_ms,
                },
            )
            logger.info(
                "BuilderBridge: analysis #%d complete | %d targets | %.1fms",
                self._analysis_count, len(findings), elapsed_ms,
            )

        except Exception as exc:
            logger.warning("BuilderBridge pattern analysis failed: %s", exc)

    def _run_file_scan(self, trigger_event: Any) -> None:
        """
        Scan rápido de archivos Python modificados recientemente.
        Emite un resumen en Channels.BUILDER.
        """
        try:
            import subprocess
            from core.operator.universal_bus import (
                Channels, EventPriority, get_universal_bus,
            )

            self._last_analysis_time = time.time()

            result = subprocess.run(
                ["git", "-C", str(_PROJECT_ROOT), "status", "--short"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return

            dirty_files = [
                ln[3:].strip()
                for ln in result.stdout.splitlines()
                if ln.strip() and ln[3:].strip().endswith(".py")
            ]

            if not dirty_files:
                return

            bus = get_universal_bus()
            bus.emit(
                channel=Channels.BUILDER,
                event_type="builder_files_detected",
                source_layer=8,
                priority=EventPriority.NORMAL,
                payload={
                    "dirty_python_files": dirty_files[:10],
                    "count": len(dirty_files),
                    "trigger_signals": trigger_event.payload.get("signals", 0),
                },
            )
            logger.info(
                "BuilderBridge: %d dirty Python files detected", len(dirty_files),
            )

        except Exception as exc:
            logger.debug("BuilderBridge file scan failed: %s", exc)

    # -- Introspection -----------------------------------------------------

    def status(self) -> Dict[str, Any]:
        return {
            "analysis_count": self._analysis_count,
            "last_analysis_time": self._last_analysis_time,
            "subscriptions": len(self._subscription_ids),
            "min_interval_s": self._min_interval,
        }
