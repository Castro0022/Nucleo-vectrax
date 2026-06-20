"""
core/orchestration/engine_registry.py — Registro declarativo de motores.

Fuente ÚNICA de verdad de los motores de Vectrax y de CÓMO activarlos/verificarlos.
Reemplaza la activación dispersa (flags sueltos + funciones activate() repartidas)
por un registro declarativo con tiers de seguridad.

Diseño:
  - Cada motor es un ``EngineSpec`` con: nombre, grupo, tier, dependencias,
    un hook ``activate`` (idempotente, modo SEGURO) y un hook ``health`` (read-only).
  - Los hooks son LAZY: importan dentro de la función, así importar este módulo
    es barato y NUNCA falla por un motor roto.
  - Los tiers codifican el límite "interno se activa solo / externo requiere
    autorización":
        CORE            — cognitivo/interno, seguro de activar.
        OBSERVE         — observación/aprendizaje, no destructivo.
        GATED_INTERNAL  — interno pero sensible; se activa en su SUB-MODO SEGURO
                          (p.ej. operador GUIDED, PresenciaObserver OBSERVER).
                          El sub-modo que fuerza (ACTIVE/no-GUIDED) NUNCA lo
                          enciende el bootstrap: requiere acción explícita.
        EXTERNAL        — credenciales/dinero/red (providers cloud, eToro LIVE,
                          TTS, Places). El bootstrap SOLO verifica salud; jamás
                          los pone "en vivo".

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import importlib
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.orchestration.registry")


class Tier(str, Enum):
    CORE = "core"
    OBSERVE = "observe"
    GATED_INTERNAL = "gated_internal"
    EXTERNAL = "external"


@dataclass(frozen=True)
class EngineSpec:
    """Especificación declarativa de un motor."""
    name: str
    group: str
    tier: Tier
    description: str = ""
    deps: Tuple[str, ...] = ()
    gated_by: Optional[str] = None          # env var que debe estar "activa" para activar
    activate: Optional[Callable[[], object]] = None   # activación SEGURA e idempotente
    health: Optional[Callable[[], bool]] = None       # chequeo read-only de disponibilidad


# ---------------------------------------------------------------------------
# Helpers para construir hooks lazy (no importan nada hasta que se llaman)
# ---------------------------------------------------------------------------

def _available(module: str, attr: Optional[str] = None) -> Callable[[], bool]:
    """health hook: el módulo importa y (opcional) expone `attr`."""
    def _check() -> bool:
        m = importlib.import_module(module)
        if attr is not None:
            getattr(m, attr)
        return True
    return _check


def _call(module: str, func: str, *args, **kwargs) -> Callable[[], object]:
    """activate hook: importa `module` y llama `func(*args, **kwargs)` (idempotente)."""
    def _do() -> object:
        m = importlib.import_module(module)
        return getattr(m, func)(*args, **kwargs)
    return _do


def _env_truthy(var: str) -> bool:
    """True si la env var está activa (no '0'/'false'/'off'/'no'/'')."""
    return os.environ.get(var, "").strip().lower() not in ("", "0", "false", "off", "no")


# ---------------------------------------------------------------------------
# Almacén del registro
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, EngineSpec] = {}


def register(spec: EngineSpec) -> None:
    if spec.name in _REGISTRY:
        logger.debug("EngineSpec %s ya registrado; se reemplaza", spec.name)
    _REGISTRY[spec.name] = spec


def get(name: str) -> Optional[EngineSpec]:
    return _REGISTRY.get(name)


def all_specs() -> List[EngineSpec]:
    return list(_REGISTRY.values())


def by_tier(tier: Tier) -> List[EngineSpec]:
    return [s for s in _REGISTRY.values() if s.tier == tier]


def by_group(group: str) -> List[EngineSpec]:
    return [s for s in _REGISTRY.values() if s.group == group]


def clear() -> None:
    """Sólo para tests."""
    _REGISTRY.clear()


# ---------------------------------------------------------------------------
# Registro por defecto — los motores de Vectrax (ver docs/ENGINES.md)
# ---------------------------------------------------------------------------

def _register_defaults() -> None:
    if _REGISTRY:
        return
    specs: List[EngineSpec] = [
        # ---- Núcleo cognitivo ----
        EngineSpec("total_convergence", "nucleo", Tier.CORE,
                   "Entrada única; ciclo unificado de 7 fases.",
                   health=_available("core.nucleus.total_convergence", "get_convergence_engine")),
        EngineSpec("presencia_observer", "nucleo", Tier.GATED_INTERNAL,
                   "Capa inhibidora; se activa en modo OBSERVER (no fuerza).",
                   activate=_call("core.nucleus.presencia_pura", "get_observer"),
                   health=_available("core.nucleus.presencia_pura", "get_observer")),
        EngineSpec("convergence_learner", "nucleo", Tier.OBSERVE,
                   "Observa decisiones y recomienda ajustes.",
                   health=_available("core.nucleus.convergence_learner", "get_learner")),
        EngineSpec("law_signal", "nucleo", Tier.CORE,
                   "Las 7 Leyes pesan en cada emisión.",
                   health=_available("core.nucleus.law_signal", "build_law_signal")),
        EngineSpec("operator_runtime", "nucleo", Tier.GATED_INTERNAL,
                   "Operador universal; se activa en modo GUIDED (no ejecuta sin autorización).",
                   deps=("total_convergence",),
                   activate=_call("core.operator.activation", "activate_operator"),
                   health=_available("core.operator.activation", "get_operator_status")),
        EngineSpec("universal_bus", "nucleo", Tier.CORE,
                   "Bus central de eventos entre capas.",
                   health=_available("core.operator.universal_bus", "get_universal_bus")),

        # ---- Gravedad ----
        EngineSpec("gravity_engine", "gravedad", Tier.CORE,
                   "Memoria por capas HOT→WARM→COLD→DEEP.",
                   health=_available("core.learn.gravity_engine", "get_gravity_index")),
        EngineSpec("cognitive_gravity", "gravedad", Tier.CORE,
                   "Grafo de estrellas con masa gravitacional.",
                   health=_available("vectrax.cognitive_gravity", "detect_convergence")),
        EngineSpec("word_gravity", "gravedad", Tier.CORE,
                   "Word Gravity Index: palabras con masa y constelaciones.",
                   health=_available("core.word_gravity", "get_effective_mass")),
        EngineSpec("convergence_history", "gravedad", Tier.OBSERVE,
                   "Historial de convergencias (nacimiento/disolución).",
                   health=_available("core.learn.convergence_history", "record_snapshot")),
        EngineSpec("memory_governor", "gravedad", Tier.CORE,
                   "Gobernanza de memoria (destila, protege identidad, aísla).",
                   health=_available("core.gravity.governor", "MemoryGovernor")),

        # ---- Auto-observación ----
        EngineSpec("meta_loop", "auto_observacion", Tier.OBSERVE,
                   "Reflexión de 8 capas por ciclo (driver de observación).",
                   health=_available("core.meta_loop", "reflect")),
        EngineSpec("autonomous_observer", "auto_observacion", Tier.OBSERVE,
                   "Compara snapshots y registra cambios.",
                   health=_available("core.self_observation.autonomous_observer", "observe_and_record")),
        EngineSpec("universe_observer", "auto_observacion", Tier.OBSERVE,
                   "Snapshot unificado gravitacional + operacional.",
                   health=_available("core.self_observation.universe_observer", "observe_universe")),
        EngineSpec("observation_ledger", "auto_observacion", Tier.CORE,
                   "Memoria persistente de observaciones (SQLite).",
                   activate=_call("core.self_observation.observation_ledger", "init_ledger"),
                   health=_available("core.self_observation.observation_ledger", "record")),
        EngineSpec("universe_census", "auto_observacion", Tier.OBSERVE,
                   "Única fuente de verdad de conteos del universo.",
                   health=_available("core.universe_census", "get_census")),
        EngineSpec("evolution_memory", "auto_observacion", Tier.OBSERVE,
                   "Snapshots diarios para comparación longitudinal.",
                   health=_available("core.self_observation.evolution_memory", "record_daily_snapshot")),
        EngineSpec("quality_entities", "auto_observacion", Tier.OBSERVE,
                   "Pilar D: calidad como fenómenos del universo.",
                   health=_available("core.self_observation.quality_entities", "get_quality_entities")),
        EngineSpec("quality_observer", "auto_observacion", Tier.OBSERVE,
                   "Pilar C: registra salud de ingeniería. Escritor opt-in.",
                   gated_by="VECTRAX_TEST_LEDGER",
                   health=_available("core.self_observation.quality_observer", "record_event")),

        # ---- Aprendizaje ----
        EngineSpec("learning_gate", "aprendizaje", Tier.OBSERVE,
                   "Aprendizaje selectivo (novedad/coherencia/impacto).",
                   health=_available("core.learn.learning_gate", "evaluate")),
        EngineSpec("pattern_refinement", "aprendizaje", Tier.OBSERVE,
                   "Auto-organización del universo cada N interacciones.",
                   health=_available("core.learn.pattern_refinement", "run_refinement_cycle")),
        EngineSpec("observation_bias", "aprendizaje", Tier.OBSERVE,
                   "Recalcula pesos de observación por dominio.",
                   health=_available("core.learn.observation_bias", "get_bias")),
        EngineSpec("learning_pipeline", "aprendizaje", Tier.OBSERVE,
                   "Ciclo señal→investigación→verificación→integración.",
                   health=_available("core.learning_cycle.pipeline", "get_learning_pipeline")),
        EngineSpec("active_learning", "aprendizaje", Tier.GATED_INTERNAL,
                   "Orquestador de aprendizaje autónomo (activable).",
                   gated_by="VECTRAX_ACTIVE_LEARNING",
                   activate=_call("core.learn.active_learning", "get_orchestrator"),
                   health=_available("core.learn.active_learning", "get_orchestrator")),
        EngineSpec("router_learning", "aprendizaje", Tier.OBSERVE,
                   "Auto-aprendizaje pasivo del router.",
                   health=_available("core.router_learning", "get_learning_engine")),
        EngineSpec("router_learning_cycle", "aprendizaje", Tier.GATED_INTERNAL,
                   "Ciclo continuo del router; genera propuestas.",
                   gated_by="VECTRAX_ROUTER_LEARNING",
                   activate=_call("core.router_learning_cycle", "get_learning_cycle"),
                   health=_available("core.router_learning_cycle", "get_learning_cycle")),

        # ---- Routing ----
        EngineSpec("smart_router", "routing", Tier.CORE,
                   "Selección de provider/modelo por tarea.",
                   health=_available("core.routing.smart_router")),
        EngineSpec("semantic_classifier", "routing", Tier.CORE,
                   "Clasificación semántica de intención.",
                   health=_available("core.semantic_classifier")),

        # ---- Identidad / Memoria ----
        EngineSpec("identity_layer", "identidad", Tier.CORE,
                   "Filtra/moldea respuestas con voz propia.",
                   health=_available("vectrax.identity_layer", "apply_identity")),
        EngineSpec("identity_anchor", "identidad", Tier.CORE,
                   "Anclaje de identidad por usuario.",
                   health=_available("vectrax.identity_anchor")),
        EngineSpec("user_memory", "identidad", Tier.CORE,
                   "Memoria por usuario.",
                   health=_available("vectrax.user_memory", "resolve_with_memory")),
        EngineSpec("core_memory", "identidad", Tier.CORE,
                   "Memoria permanente indestructible.",
                   health=_available("vectrax.core_memory")),
        EngineSpec("fact_memory", "identidad", Tier.CORE,
                   "Segundo cerebro: hechos concretos.",
                   health=_available("vectrax.fact_memory")),

        # ---- Otros motores ----
        EngineSpec("intent_engine", "otros", Tier.CORE,
                   "Clasifica intención a nivel de acción.",
                   health=_available("core.intent_engine")),
        EngineSpec("risk_engine", "otros", Tier.CORE,
                   "risk_score probabilístico (6 señales).",
                   health=_available("core.risk_engine")),
        EngineSpec("proposal_engine", "otros", Tier.OBSERVE,
                   "Auto-evolución controlada (propone, no aplica).",
                   health=_available("core.proposal_engine")),
        EngineSpec("telegram_guard", "otros", Tier.CORE,
                   "Silent mode por defecto en el chat.",
                   health=_available("core.telegram_guard", "should_send_to_user")),
        EngineSpec("voice_engine", "otros", Tier.EXTERNAL,
                   "TTS (requiere OPENAI_API_KEY; gated).",
                   gated_by="OPENAI_API_KEY",
                   health=_available("core.voice.voice_engine")),
        EngineSpec("universal_pattern_library", "otros", Tier.OBSERVE,
                   "UPL: meta-patrones cross-dominio (hipótesis de observación).",
                   health=_available("core.universal_pattern_library", "get_usable_hypotheses")),
        EngineSpec("recovery_engine", "otros", Tier.GATED_INTERNAL,
                   "Auto-sanación (gated por RESILIENCE_ENABLED).",
                   gated_by="RESILIENCE_ENABLED",
                   health=_available("core.recovery")),

        # ---- Mercado / eToro (EXTERNAL: solo health; nunca LIVE automático) ----
        EngineSpec("market_observer", "mercado", Tier.EXTERNAL,
                   "Evalúa 4 condiciones de mercado (necesita feed).",
                   health=_available("connectors.etoro.market_observer", "evaluate")),
        EngineSpec("market_learning_engine", "mercado", Tier.EXTERNAL,
                   "Ciclo de aprendizaje de mercado (propone).",
                   health=_available("connectors.etoro.learning_engine", "run_learning_cycle")),
        EngineSpec("auto_executor", "mercado", Tier.EXTERNAL,
                   "Ejecución por fases; OFF por defecto. NUNCA auto-LIVE.",
                   gated_by="__never__",
                   health=_available("connectors.etoro.auto_executor", "get_config")),
        EngineSpec("pattern_memory", "mercado", Tier.EXTERNAL,
                   "Memoria estadística de señales resueltas.",
                   health=_available("connectors.etoro.pattern_memory", "get_patterns")),

        # ---- Cognición (orquestador) ----
        EngineSpec("perception_engine", "cognicion", Tier.CORE,
                   "Motor 1: intake→clasificar→buffer→cambios→contexto.",
                   health=_available("cognition.perception.perception_engine")),
        EngineSpec("reasoning_engine", "cognicion", Tier.CORE,
                   "Motor 2: riesgo→impacto→escenarios→contradicciones.",
                   health=_available("cognition.reasoning.reasoning_engine")),
        EngineSpec("memory_engine", "cognicion", Tier.CORE,
                   "API unificada de stores + grafo.",
                   health=_available("cognition.memory.memory_engine")),
    ]
    for s in specs:
        register(s)


# Poblar al importar (idempotente).
_register_defaults()
