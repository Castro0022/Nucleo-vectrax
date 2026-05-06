"""
core/scale/load_shedder.py — Graceful degradation under load.

Mide la carga del sistema y degrada el pipeline ANTES de caerse:
  - NORMAL:    todo permitido, modelo natural del PolicyConfig.
  - ELEVATED:  apaga simulación de escenarios.
  - HIGH:      apaga learning no crítico.
  - CRITICAL:  modelo barato/rápido, solo motores esenciales, mantiene
               la respuesta viva (que el usuario no perciba caída).

Métricas usadas (best-effort; si una fuente falla, asume NORMAL):
  - CPU usage promedio (psutil si está, sino fallback a /proc/loadavg).
  - RAM% (psutil).
  - Cola activa de mensajes (si message_queue.queue_depth() existe).
  - Errores recientes (si status monitor existe).

API:
  shed = LoadShedder()
  level = shed.get_load_level()
  model = shed.choose_model(profile, level)
  policy = shed.degrade_pipeline(policy, level)
  if shed.should_disable_engine('learning_loop', level):
      ...
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from enum import Enum
from typing import Optional

from core.scale.pipeline_policy import PolicyConfig, PipelineProfile

logger = logging.getLogger("vectrax.scale.load_shedder")


class LoadLevel(str, Enum):
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# Tunable thresholds (env-overridable)
_CPU_ELEVATED = float(os.environ.get("LOAD_CPU_ELEVATED", "60"))
_CPU_HIGH = float(os.environ.get("LOAD_CPU_HIGH", "80"))
_CPU_CRITICAL = float(os.environ.get("LOAD_CPU_CRITICAL", "92"))

_RAM_ELEVATED = float(os.environ.get("LOAD_RAM_ELEVATED", "70"))
_RAM_HIGH = float(os.environ.get("LOAD_RAM_HIGH", "85"))
_RAM_CRITICAL = float(os.environ.get("LOAD_RAM_CRITICAL", "94"))

_QUEUE_ELEVATED = int(os.environ.get("LOAD_QUEUE_ELEVATED", "20"))
_QUEUE_HIGH = int(os.environ.get("LOAD_QUEUE_HIGH", "60"))
_QUEUE_CRITICAL = int(os.environ.get("LOAD_QUEUE_CRITICAL", "150"))


# Engines considered NON-essential (eligible for shedding under load)
_NON_ESSENTIAL_ENGINES = {
    "scenarios",
    "what_if_simulator",
    "deep_memory_search",
    "convergence_detector",
    "learning_loop",
    "auto_learn",
    "embedding_indexer",
    "background_clustering",
}

# Engines that MUST stay alive even under CRITICAL (response delivery)
_ESSENTIAL_ENGINES = {
    "intent_classifier",
    "language_gate",
    "identity_handler",
    "telegram_send",
    "voice_dispatch",
    "fallback_response",
}


class LoadShedder:

    def __init__(self) -> None:
        self._cached_level: Optional[LoadLevel] = None

    # -- Métricas -----------------------------------------------------------

    def _cpu_pct(self) -> float:
        """Best-effort CPU% usage. Returns 0 if no source available."""
        try:
            import psutil  # type: ignore
            return float(psutil.cpu_percent(interval=None))
        except Exception:
            pass
        # Fallback: use load average / cpu_count
        try:
            with open("/proc/loadavg", "r") as f:
                load1 = float(f.read().split()[0])
            cpus = os.cpu_count() or 1
            return min(100.0, (load1 / cpus) * 100.0)
        except Exception:
            return 0.0

    def _ram_pct(self) -> float:
        try:
            import psutil  # type: ignore
            return float(psutil.virtual_memory().percent)
        except Exception:
            return 0.0

    def _queue_depth(self) -> int:
        """Cola de mensajes pendientes (si está disponible)."""
        try:
            from core.transport.message_queue import queue_depth
            return int(queue_depth())
        except Exception:
            pass
        try:
            from core.transport.message_queue import pending_count
            return int(pending_count())
        except Exception:
            return 0

    # -- Decisión -----------------------------------------------------------

    def get_load_level(self) -> LoadLevel:
        """Compute level from CPU + RAM + queue.  NEVER raises."""
        try:
            cpu = self._cpu_pct()
            ram = self._ram_pct()
            depth = self._queue_depth()

            # Cualquier dimensión en CRITICAL → CRITICAL global
            if cpu >= _CPU_CRITICAL or ram >= _RAM_CRITICAL or depth >= _QUEUE_CRITICAL:
                level = LoadLevel.CRITICAL
            elif cpu >= _CPU_HIGH or ram >= _RAM_HIGH or depth >= _QUEUE_HIGH:
                level = LoadLevel.HIGH
            elif cpu >= _CPU_ELEVATED or ram >= _RAM_ELEVATED or depth >= _QUEUE_ELEVATED:
                level = LoadLevel.ELEVATED
            else:
                level = LoadLevel.NORMAL

            self._cached_level = level
            return level
        except Exception as exc:
            logger.debug("get_load_level fallback to NORMAL: %s", exc)
            return LoadLevel.NORMAL

    # -- Modelo -------------------------------------------------------------

    def choose_model(self, profile: PipelineProfile, level: LoadLevel) -> str:
        """Decide qué modelo (cheap/std/premium) usar dado el perfil y la
        carga actual. Bajo CRITICAL todo cae a 'cheap'.
        """
        if level == LoadLevel.CRITICAL:
            return "cheap"
        if level == LoadLevel.HIGH:
            # Premium degrada a std, std queda igual, cheap igual
            return "std" if profile in (PipelineProfile.MISSION_CRITICAL,
                                        PipelineProfile.ADVANCED) else "cheap"
        # NORMAL / ELEVATED: respetar el perfil natural
        if profile == PipelineProfile.MISSION_CRITICAL:
            return "premium"
        if profile == PipelineProfile.ADVANCED:
            return "std"
        if profile == PipelineProfile.STANDARD:
            return "std"
        return "cheap"

    # -- Pipeline degradation ----------------------------------------------

    def degrade_pipeline(
        self,
        policy: PolicyConfig,
        level: LoadLevel,
    ) -> PolicyConfig:
        """Apply load-level-specific degradations to a PolicyConfig.

        NEVER returns None. Always returns a usable policy.
        """
        if level == LoadLevel.NORMAL:
            return policy

        new = policy
        if level == LoadLevel.ELEVATED:
            # Apaga escenarios pesados
            new = replace(new, allow_scenarios=False)
        elif level == LoadLevel.HIGH:
            # Apaga learning no crítico + escenarios
            new = replace(
                new,
                allow_scenarios=False,
                allow_learning=False,
                # Reduce budget de motores en 30%
                max_engines=max(1, int(new.max_engines * 0.7)),
            )
        elif level == LoadLevel.CRITICAL:
            # Mínimo viable: respuesta viva, sin lujos.
            new = replace(
                new,
                allow_scenarios=False,
                allow_learning=False,
                allow_multimodal=False,
                allow_reasoning=False,
                allow_deep_memory=False,
                max_engines=2,
                llm_model="cheap",
                timeout_ms=min(new.timeout_ms, 4000),
            )
        return new

    # -- Engine gate --------------------------------------------------------

    def should_disable_engine(self, engine_name: str, level: LoadLevel) -> bool:
        """True si bajo el nivel actual de carga este motor debe APAGARSE.

        Reglas:
          - NORMAL: nada se apaga.
          - ELEVATED: motores de scenarios.
          - HIGH: scenarios + learning.
          - CRITICAL: todos los no-esenciales.
        """
        name = (engine_name or "").lower()

        if level == LoadLevel.NORMAL:
            return False

        if name in _ESSENTIAL_ENGINES:
            return False

        if level == LoadLevel.CRITICAL:
            # Solo esenciales
            return name not in _ESSENTIAL_ENGINES

        if level == LoadLevel.HIGH:
            # scenarios + learning
            return any(k in name for k in
                       ("scenario", "learning", "auto_learn", "what_if",
                        "background", "indexer"))

        if level == LoadLevel.ELEVATED:
            # Solo scenarios
            return any(k in name for k in ("scenario", "what_if"))

        return False
