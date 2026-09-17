"""
Vectrax — Convergencia Total del Núcleo
==========================================
Punto único de entrada para TODO input del sistema.

Cada elemento que entra al sistema —texto, código, instrucciones,
errores, conversaciones, decisiones, resultados— pasa por un ciclo
unificado de:

  1. PERCEPCIÓN:     Recibir, normalizar, clasificar el input
  2. CLASIFICACIÓN:  Etiquetar por tipo, intención, dominio, impacto
  3. MEMORIA:        Conectar con patrones previos (inmediata + estructural + evolutiva)
  4. ANÁLISIS:       Evaluar riesgo, coherencia, contradicciones
  5. SÍNTESIS:       Generar respuesta/acción/propuesta/código
  6. GRAVITACIÓN:    Almacenar según peso gravitacional en el núcleo
  7. APRENDIZAJE:    Alimentar hipótesis, validar reglas, evolucionar

Principios:
  - Coherencia: cada salida es consistente con el conocimiento previo
  - Continuidad: el sistema mantiene contexto entre interacciones
  - No repetición: patrones ya conocidos se reutilizan, no se recrean
  - Preservación: todo conocimiento útil se preserva (nada se borra)
  - Autoorganización: el sistema se estructura desde el centro
  - Autorización: cambios sensibles requieren aprobación del creador

Integra: PerceptionEngine, MemoryEngine, ReasoningEngine, GravityEngine,
         HypothesisEngine, ActiveLearning, Governor, EpisodicLedger,
         Constitution, UniversalBus

Capa: 1 — Núcleo Central (extensión de core.operator.nucleus)
Creado: 2026-03-20
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from core import state_manager
from core.learn.episodic import get_ledger, EpisodicEvent

logger = logging.getLogger("vectrax.nucleus.convergence")


# ---------------------------------------------------------------------------
# Input classification
# ---------------------------------------------------------------------------

class InputType(str, Enum):
    """Tipo de entrada al sistema."""
    TEXT = "text"                 # Mensaje de texto libre
    CODE = "code"                # Fragmento de código
    INSTRUCTION = "instruction"  # Instrucción directa al sistema
    ERROR = "error"              # Reporte de error
    CONVERSATION = "conversation"  # Mensaje conversacional
    DECISION = "decision"        # Decisión tomada
    RESULT = "result"            # Resultado de ejecución
    CONTRACT = "contract"        # Contrato / configuración
    SIGNAL = "signal"            # Señal interna del sistema
    UNKNOWN = "unknown"


class ConvergencePhase(str, Enum):
    """Fases del ciclo de convergencia."""
    PERCEPTION = "perception"
    CLASSIFICATION = "classification"
    MEMORY = "memory"
    ANALYSIS = "analysis"
    SYNTHESIS = "synthesis"
    GRAVITATION = "gravitation"
    LEARNING = "learning"


# ---------------------------------------------------------------------------
# Convergence Record — resultado de un ciclo de convergencia
# ---------------------------------------------------------------------------

@dataclass
class ConvergenceRecord:
    """Resultado completo de procesar un input a través del ciclo."""
    id: str = field(default_factory=lambda: f"CONV-{uuid.uuid4().hex[:8].upper()}")
    input_type: str = InputType.UNKNOWN.value
    input_fingerprint: str = ""
    domain: str = "unknown"
    intent: str = ""
    impact: str = "low"
    # Phase results
    phases_completed: List[str] = field(default_factory=list)
    # Memory connections
    memory_connections: int = 0
    prior_patterns_found: int = 0
    # Root-cause fix (2026-09-17): cuántas veces CCTracker (core/learn/
    # constitution.py) ya había observado este fingerprint EXACTO (sha256
    # completo del contenido) ANTES de este ciclo. 0 = primer contacto.
    # Distinto de `prior_patterns_found`, que también cuenta coincidencias
    # por INTENT (más ruidoso — muchos mensajes sin relación comparten
    # intent). Este campo es la señal precisa de "confirmación real
    # repetida" que usa `_compute_cc_observation_score()` para no inflar
    # `coherence_score` con matches genéricos.
    exact_repeat_count: int = 0
    # Evidencia real retenida (NucleusDecision ticket, 2026-09-17): las
    # variables intermedias de `_phase_memory()` ya no se descartan tras
    # colapsarlas a los 2 enteros de arriba. Abstracta (nombres/scores/ids
    # cortos), nunca contenido crudo del mensaje — mismo criterio de
    # privacidad que ya aplica el resto del motor. `memory_connections`/
    # `prior_patterns_found` se preservan sin cambios para no romper a
    # quien ya los consume (convergence_hook.py, ledger, etc.).
    memory_evidence: Dict[str, Any] = field(default_factory=dict)
    # Snapshot minimo de Capability Context (poblado por
    # `_run_capability_check()`, fase ANÁLISIS) — qué capacidades de
    # fallback están disponibles/autorizadas para esta consulta.
    capability_snapshot: Dict[str, Any] = field(default_factory=dict)
    # Acción operativa candidata (Strategy) que el Núcleo propone a
    # SmartRouter, o None si no hay evidencia/capacidad suficiente. Ver
    # `core/nucleus/nucleus_decision.py`.
    nucleus_decision: Optional[Any] = None
    gravitational_tier: str = ""
    # Analysis
    risk_level: str = "low"
    coherence_score: float = 0.0
    is_novel: bool = True          # True if no prior pattern matches
    contradictions: int = 0
    # Synthesis
    action_recommended: str = ""   # "proceed", "review", "block", "learn_only"
    # Learning
    hypothesis_fed: bool = False
    rule_matched: bool = False
    # Strategic reasoning (ReasoningEngine integration — auditoría 2026-09-13)
    reasoning_ran: bool = False
    reasoning_recommendation: str = ""
    reasoning_risk_level: str = ""
    reasoning_error: str = ""
    # Meta
    governor_mode: str = ""
    convergence_time_ms: float = 0.0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "input_type": self.input_type,
            "input_fingerprint": self.input_fingerprint,
            "domain": self.domain,
            "intent": self.intent,
            "impact": self.impact,
            "phases_completed": self.phases_completed,
            "memory_connections": self.memory_connections,
            "prior_patterns_found": self.prior_patterns_found,
            "exact_repeat_count": self.exact_repeat_count,
            "memory_evidence": self.memory_evidence,
            "capability_snapshot": self.capability_snapshot,
            "nucleus_decision": (
                self.nucleus_decision.to_dict()
                if self.nucleus_decision is not None else None
            ),
            "gravitational_tier": self.gravitational_tier,
            "risk_level": self.risk_level,
            "coherence_score": round(self.coherence_score, 4),
            "is_novel": self.is_novel,
            "contradictions": self.contradictions,
            "action_recommended": self.action_recommended,
            "hypothesis_fed": self.hypothesis_fed,
            "rule_matched": self.rule_matched,
            "reasoning_ran": self.reasoning_ran,
            "reasoning_recommendation": self.reasoning_recommendation,
            "reasoning_risk_level": self.reasoning_risk_level,
            "reasoning_error": self.reasoning_error,
            "governor_mode": self.governor_mode,
            "convergence_time_ms": round(self.convergence_time_ms, 2),
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Input type detection keywords
# ---------------------------------------------------------------------------

_CODE_INDICATORS = {
    "def ", "class ", "import ", "from ", "return ", "if __name__",
    "async def", "lambda ", "try:", "except:", "yield ", "raise ",
}
_INSTRUCTION_INDICATORS = {
    "activar", "crear", "implementar", "configurar", "eliminar",
    "cambiar", "actualizar", "iniciar", "detener", "ejecutar",
    "activate", "create", "implement", "configure", "delete",
    "start", "stop", "execute", "run", "build", "deploy",
}
_ERROR_INDICATORS = {
    "error", "exception", "traceback", "failed", "crash",
    "fallo", "excepción", "bug", "broken", "roto",
}

# NucleusDecision ticket (2026-09-17): umbral NUEVO, introducido para este
# ticket — no reutiliza `learning_gate.COHERENCE_THRESHOLD` (0.5) porque mide
# algo distinto (similitud a embeddings de patrones, no `cc_score` del CC
# Tracker). Sin criterio equivalente reutilizable ya existente para "evidencia
# de memoria suficiente para responder sin resolver", se define aquí explícito.
_HIGH_COHERENCE_THRESHOLD = 0.75

# Root-cause fix (2026-09-17) — semántica histórica de CCTracker.observation_score
# ==============================================================================
# `core/learn/constitution.py` documenta `observation_score` como "the intent
# confidence from the latest observation": el EMA (CC_DECAY_ALPHA=0.3) es quien
# acumula CONSISTENCIA a través del tiempo; cada llamada a `update()` debe
# aportar la confianza REAL de ESA observación puntual. Así lo hacen los demás
# consumidores reales (p.ej. `core/learn/ingestion.py` pasa
# `intent.confidence`, nunca un valor fijo).
#
# El bug de causa raíz en `_phase_analysis()` era alimentar SIEMPRE
# `max(record.coherence_score, 0.5)` — un piso constante sin relación con si
# la observación era realmente nueva, confirmada o contradictoria. Esa
# fórmula converge matemáticamente a exactamente 0.5 (demostrado con datos
# reales de producción: 772 fingerprints, 0 por encima de 0.75) y por lo
# tanto NUNCA puede cruzar `_HIGH_COHERENCE_THRESHOLD`.
#
# Fix: `_compute_cc_observation_score()` (más abajo) sustituye ese piso fijo
# por una señal basada en evidencia real — SIN tocar `CCTracker` (ya es
# correcto, lo usan otros consumidores) ni `_HIGH_COHERENCE_THRESHOLD`.
_CC_OBS_FIRST_SIGHTING = 0.5   # sin cambios: comportamiento histórico intacto
_CC_OBS_CONFIRMED = 0.9        # repetición EXACTA, consistente (sin contradicciones)
_CC_OBS_CONTRADICTED = 0.2     # repetición EXACTA, pero contradicha por ReasoningEngine

# Victoria B (2026-09-17) — selección de capacidad por el Núcleo cuando la
# evidencia de memoria es insuficiente
# ==============================================================================
# Auditoría previa (alcance único de este ticket): `CapabilityEntry.name` +
# `.health` + `.authorized` (core/self_observation/capability_context.py) YA
# distinguen el PROPÓSITO de cada capacidad de forma determinista —
# "online_search", "places_search", "market_observer", "llm_providers" son
# nombres ya existentes y estables del mismo catálogo que usa
# `capability_for_route()`. NO se requiere enriquecer `CapabilityEntry`: basta
# con consultar esos nombres ya existentes, exactamente como ya hace
# `external_gateway._active_capability_gate()`. `_FALLBACK_CANDIDATES` (ese
# mismo módulo) NO incluye "places_search" ni "llm_providers" — pertenece a
# una función distinta (narración de fallback al usuario) que no se toca aquí;
# por eso la disponibilidad se resuelve DIRECTO desde `ctx.entries` para los 4
# nombres que este mapeo necesita, sin modificar `capability_context.py`.
#
# Mapea `intent_ssot.IntentDecision.primary_intent` (valores de
# `core.smart_router.Intent`, YA calculados por el SSOT existente — sin
# tocarlo) al nombre de `CapabilityEntry` que respalda esa ruta. Deliberado:
# memory/local/identity/command/ai_single/ai_multi quedan FUERA — no dependen
# de una capacidad externa verificable (mismo criterio que ya aplica
# `capability_for_route()` para excluirlas de su propio mapeo). "cognitive"
# se resuelve aparte (ver `_build_nucleus_decision`) porque además exige
# riesgo estratégico bajo, no solo disponibilidad de capacidad.
_INTENT_CAPABILITY_NAME: Dict[str, str] = {
    "online": "online_search",
    "place_search": "places_search",
    "market": "market_observer",
}

# Los 4 nombres de CapabilityEntry que este ticket necesita consultar por
# disponibilidad+autorización real (fuera del recorte de _FALLBACK_CANDIDATES).
_TRACKED_CAPABILITY_NAMES: Tuple[str, ...] = (
    "online_search", "places_search", "market_observer", "llm_providers",
)

# Petición EXPLÍCITA de búsqueda online — SOLO para la excepción de
# prioridad (Victoria B, caso 4): "online" es el intent GENÉRICO de
# fallback de `core.smart_router._classify_regex()` para CUALQUIER pregunta
# factual (incluida una ya convergida por evidencia), a diferencia de
# "market"/"place_search" que solo se activan con patrones específicos. Sin
# esta mención textual explícita, tratar CUALQUIER intent=online como
# "petición explícita" rompería la Regresión de Victoria A (la misma
# pregunta repetida ya convergida dejaría de responder por evidencia).
_EXPLICIT_ONLINE_RE = re.compile(
    r"\b(?:online|internet|en\s+la\s+web|en\s+internet)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Total Convergence Engine
# ---------------------------------------------------------------------------

class TotalConvergenceEngine:
    """
    Punto único de convergencia de todo el sistema Vectrax.

    Todo input pasa por aquí. El motor coordina todos los subsistemas
    cognitivos en un ciclo unificado de 7 fases.

    Usage::

        engine = get_convergence_engine()
        engine.activate()
        record = engine.process("texto del usuario", source="telegram")
    """

    def __init__(self) -> None:
        self._ledger = get_ledger()
        self._cycle_count: int = 0
        self._total_inputs: int = 0
        self._active: bool = False
        # Lazy-loaded engines
        self._perception_engine = None
        self._memory_engine = None
        self._reasoning_engine = None
        self._gravity_index = None
        self._hypothesis_engine = None
        self._learning_orchestrator = None
        self._rules_store = None
        logger.info("TotalConvergenceEngine initialized")

    # -- Lazy imports (avoid circular dependencies) -----------------------

    def _get_perception(self):
        if self._perception_engine is None:
            try:
                from cognition.perception.perception_engine import PerceptionEngine
                self._perception_engine = PerceptionEngine()
            except ImportError:
                self._perception_engine = None
        return self._perception_engine

    def _get_memory(self):
        if self._memory_engine is None:
            try:
                from cognition.memory.memory_engine import MemoryEngine
                self._memory_engine = MemoryEngine()
            except ImportError:
                self._memory_engine = None
        return self._memory_engine

    def _get_reasoning(self):
        if self._reasoning_engine is None:
            try:
                from cognition.reasoning.reasoning_engine import ReasoningEngine
                self._reasoning_engine = ReasoningEngine()
            except ImportError:
                self._reasoning_engine = None
        return self._reasoning_engine

    def _get_gravity(self):
        if self._gravity_index is None:
            try:
                from core.learn.gravity_engine import get_gravity_index
                self._gravity_index = get_gravity_index()
            except ImportError:
                self._gravity_index = None
        return self._gravity_index

    def _get_hypothesis_engine(self):
        if self._hypothesis_engine is None:
            try:
                from core.operator.hypothesis_engine import get_hypothesis_engine
                self._hypothesis_engine = get_hypothesis_engine()
            except ImportError:
                self._hypothesis_engine = None
        return self._hypothesis_engine

    def _get_learning(self):
        if self._learning_orchestrator is None:
            try:
                from core.learn.active_learning import get_orchestrator
                self._learning_orchestrator = get_orchestrator()
            except ImportError:
                self._learning_orchestrator = None
        return self._learning_orchestrator

    def _get_rules_store(self):
        if self._rules_store is None:
            try:
                from core.learn.learned_rules import get_rules_store
                self._rules_store = get_rules_store()
            except ImportError:
                self._rules_store = None
        return self._rules_store

    def _get_governor_policy(self) -> Dict[str, Any]:
        try:
            from core.governor import get_current_policy
            return get_current_policy()
        except Exception:
            return {"mode": "observe", "autopatch_allowed": False}

    # =====================================================================
    # Activation
    # =====================================================================

    def activate(self) -> Dict[str, Any]:
        """
        Activar modo CONVERGENCIA TOTAL.

        Activa el bucle permanente de observación y actualización,
        punto único de entrada, integración de todas las memorias.
        """
        self._active = True

        state = state_manager.load()
        state["convergence_mode"] = "TOTAL"
        state["convergence_activated_at"] = datetime.now(timezone.utc).isoformat()
        state_manager.save(state)

        # Ensure active learning is also enabled
        learning = self._get_learning()
        if learning and not learning.is_active():
            learning.activate()

        self._ledger.append(EpisodicEvent(
            event_type="CONVERGENCE_MODE_ACTIVATED",
            summary="Convergencia Total del Núcleo activada",
            metadata={
                "mode": "TOTAL",
                "learning_active": learning.is_active() if learning else False,
                "governor": self._get_governor_policy().get("mode", "unknown"),
            },
        ))

        logger.info("CONVERGENCIA TOTAL activada")

        return {
            "mode": "TOTAL",
            "active": True,
            "activated_at": state["convergence_activated_at"],
            "learning_mode": state.get("learning_mode", "OBSERVATION"),
            "governor_mode": self._get_governor_policy().get("mode", "unknown"),
        }

    def deactivate(self) -> Dict[str, Any]:
        """Desactivar convergencia total."""
        self._active = False
        state_manager.put("convergence_mode", "INACTIVE")
        return {"mode": "INACTIVE", "active": False}

    @property
    def is_active(self) -> bool:
        return self._active or state_manager.get("convergence_mode") == "TOTAL"

    # =====================================================================
    # MAIN ENTRY POINT — process()
    # =====================================================================

    def process(
        self,
        content: str,
        *,
        source: str = "unknown",
        channel: str = "",
        owner: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConvergenceRecord:
        """
        Punto único de entrada para TODO input al sistema.

        Ejecuta el ciclo completo de 7 fases:
          1. Percepción → recibir y normalizar
          2. Clasificación → tipo, intención, dominio, impacto
          3. Memoria → buscar patrones previos, conectar
          4. Análisis → riesgo, coherencia, contradicciones
          5. Síntesis → determinar acción recomendada
          6. Gravitación → almacenar según peso gravitacional
          7. Aprendizaje → alimentar hipótesis, validar reglas

        Returns:
            ConvergenceRecord con el resultado completo del ciclo.
        """
        t0 = time.time()
        record = ConvergenceRecord()
        meta = metadata or {}

        # ── Phase 1: PERCEPCIÓN ────────────────────────────────────
        record = self._phase_perception(record, content, source, meta)

        # ── Phase 2: CLASIFICACIÓN ─────────────────────────────────
        record = self._phase_classification(record, content)

        # ── Phase 3: MEMORIA ───────────────────────────────────────
        record = self._phase_memory(record, content)

        # ── Phase 4: ANÁLISIS ──────────────────────────────────────
        record = self._phase_analysis(record, content)

        # ── Phase 5: SÍNTESIS ──────────────────────────────────────
        record = self._phase_synthesis(record)

        # ── Phase 6: GRAVITACIÓN ───────────────────────────────────
        record = self._phase_gravitation(record, content)

        # ── Phase 7: APRENDIZAJE ───────────────────────────────────
        record = self._phase_learning(record, content)

        # ── Finalize ───────────────────────────────────────────────
        record.convergence_time_ms = (time.time() - t0) * 1000
        self._cycle_count += 1
        self._total_inputs += 1

        self._ledger.append(EpisodicEvent(
            event_type="CONVERGENCE_CYCLE_COMPLETED",
            summary=(
                f"Convergence #{self._cycle_count}: "
                f"type={record.input_type} intent={record.intent} "
                f"action={record.action_recommended} "
                f"({record.convergence_time_ms:.0f}ms)"
            ),
            intent_fingerprint=record.input_fingerprint,
            metadata=record.to_dict(),
        ))

        logger.info(
            "Convergence #%d: type=%s intent=%s tier=%s action=%s (%.1fms)",
            self._cycle_count, record.input_type, record.intent,
            record.gravitational_tier, record.action_recommended,
            record.convergence_time_ms,
        )

        return record

    # =====================================================================
    # Phase 1: PERCEPCIÓN
    # =====================================================================

    def _phase_perception(
        self,
        record: ConvergenceRecord,
        content: str,
        source: str,
        meta: Dict[str, Any],
    ) -> ConvergenceRecord:
        """Recibir, normalizar, generar fingerprint."""
        # Generate content fingerprint (abstract, no raw storage)
        record.input_fingerprint = hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest()[:16]

        # Inject as perception signal if engine available
        perception = self._get_perception()
        if perception:
            try:
                perception.inject_signal(
                    raw_data={
                        "content_hash": record.input_fingerprint,
                        "source": source,
                        "type": "convergence_input",
                        "size": len(content),
                    },
                    source=source,
                )
            except Exception:
                pass  # perception failure is non-fatal

        self._ledger.append(EpisodicEvent(
            event_type="CONVERGENCE_INPUT_RECEIVED",
            summary=f"Input from {source}: {len(content)} chars",
            content_hash=record.input_fingerprint,
            metadata={
                "source": source,
                "size": len(content),
                "extra_meta": {k: str(v)[:100] for k, v in meta.items()},
            },
        ))

        record.phases_completed.append(ConvergencePhase.PERCEPTION.value)
        return record

    # =====================================================================
    # Phase 2: CLASIFICACIÓN
    # =====================================================================

    def _phase_classification(
        self,
        record: ConvergenceRecord,
        content: str,
    ) -> ConvergenceRecord:
        """Clasificar por tipo, intención, dominio, impacto."""
        # Detect input type
        record.input_type = self._detect_input_type(content).value

        # Detect intent using core intent engine
        intent_label = "unknown"
        try:
            from core.learn.intent_engine import infer_intent
            intent_result = infer_intent(content)
            intent_label = intent_result.intent_primary or "unknown"
            record.intent = intent_label
            record.impact = intent_result.impact
        except Exception:
            record.intent = self._simple_intent_detect(content)
            record.impact = "low"

        # Detect domain
        try:
            from core.learn.constitution import classify_domain
            record.domain = classify_domain(content)
        except Exception:
            record.domain = "unknown"

        # Governor context
        policy = self._get_governor_policy()
        record.governor_mode = policy.get("mode", "unknown")

        self._ledger.append(EpisodicEvent(
            event_type="CONVERGENCE_CLASSIFIED",
            summary=(
                f"Classified: type={record.input_type} "
                f"intent={record.intent} domain={record.domain} "
                f"impact={record.impact}"
            ),
            intent_fingerprint=record.input_fingerprint,
            metadata={
                "input_type": record.input_type,
                "intent": record.intent,
                "domain": record.domain,
                "impact": record.impact,
                "governor_mode": record.governor_mode,
            },
        ))

        record.phases_completed.append(ConvergencePhase.CLASSIFICATION.value)
        return record

    # =====================================================================
    # Phase 3: MEMORIA
    # =====================================================================

    def _phase_memory(
        self,
        record: ConvergenceRecord,
        content: str,
    ) -> ConvergenceRecord:
        """
        Conectar con patrones previos a través de 3 niveles de memoria:
          - Inmediata: señales recientes en buffer de percepción
          - Estructural: gravity engine, CC tracker, knowledge graph
          - Evolutiva: hipótesis activas, reglas aprendidas
        """
        connections = 0
        prior_patterns = 0
        # Evidencia real retenida (NucleusDecision ticket, 2026-09-17): cada
        # bloque abajo, además de sumar a los contadores (comportamiento sin
        # cambios), guarda una versión ABSTRACTA (conteos, ids/tags cortos,
        # scores — nunca contenido crudo del mensaje) de qué encontró antes
        # de descartar la variable. Defensivo: `getattr`/`.get()` con default
        # en todos lados porque las fuentes son motores lazy-import que
        # pueden no exponer los mismos atributos entre versiones.
        evidence: Dict[str, Any] = {}

        # --- Memoria Inmediata (perception buffer) ---
        perception = self._get_perception()
        if perception:
            try:
                recent = perception.recent_events(20)
                # Count related signals by matching intent
                related = [
                    s for s in recent
                    if hasattr(s, 'intent') and record.intent
                    and record.intent.lower() in (s.intent or "").lower()
                ]
                connections += len(related)
                if related:
                    evidence["immediate"] = {
                        "count": len(related),
                        "intents": [getattr(s, "intent", "") for s in related[:5]],
                    }
            except Exception:
                pass

        # --- Memoria Estructural (gravity + CC + memory engine) ---
        gravity = self._get_gravity()
        if gravity:
            try:
                similar = gravity.search_similar(record.input_fingerprint)
                prior_patterns += len(similar)
                connections += len(similar)
                if similar:
                    record.is_novel = False
                    evidence["gravity_similar"] = {
                        "count": len(similar),
                        "tiers": [getattr(s, "tier", "") for s in similar[:5]],
                        "fingerprints": [
                            getattr(s, "fingerprint", "")[:16] for s in similar[:5]
                        ],
                    }
            except Exception:
                pass

        # CC Tracker — cognitive consistency
        try:
            from core.learn.constitution import get_cc_tracker
            cc = get_cc_tracker()
            cc_entry = cc.get(record.input_fingerprint)
            if cc_entry:
                record.coherence_score = cc_entry.cc_score
                record.is_novel = False
                # Root-cause fix (2026-09-17): capturar cuántas veces YA se
                # observó este fingerprint EXACTO antes de este ciclo — la
                # señal precisa que _compute_cc_observation_score() usa para
                # distinguir "confirmación real repetida" de matches
                # genéricos por intent (ver comentario del campo).
                record.exact_repeat_count = cc_entry.observations
                connections += 1
                evidence["cc_entry"] = {
                    "cc_score": cc_entry.cc_score,
                    "domain": getattr(cc_entry, "domain", ""),
                    "intent": getattr(cc_entry, "intent", ""),
                }
        except Exception:
            pass

        # Memory Engine — deep structural memory
        memory = self._get_memory()
        if memory:
            try:
                mem_ctx = memory.query(intent=record.intent)
                connections += len(mem_ctx.related_decisions)
                connections += len(mem_ctx.related_patterns)
                prior_patterns += len(mem_ctx.related_patterns)
                connections += len(mem_ctx.graph_connections)
                if mem_ctx.related_patterns:
                    record.is_novel = False
                if mem_ctx.related_decisions or mem_ctx.related_patterns or mem_ctx.graph_connections:
                    evidence["structural_memory"] = {
                        "related_decisions": len(mem_ctx.related_decisions),
                        "related_patterns": len(mem_ctx.related_patterns),
                        "graph_connections": len(mem_ctx.graph_connections),
                        "pattern_ids": [
                            getattr(p, "id", str(p)[:32])
                            for p in list(mem_ctx.related_patterns)[:5]
                        ],
                    }
            except Exception:
                pass

        # --- Memoria Evolutiva (hipótesis + reglas aprendidas) ---
        hyp_engine = self._get_hypothesis_engine()
        if hyp_engine:
            try:
                active_hyps = hyp_engine.get_active()
                related_hyps = [
                    h for h in active_hyps
                    if record.intent in h.tags
                    or any(record.intent.lower() in t.lower() for t in h.tags)
                ]
                connections += len(related_hyps)
                if related_hyps:
                    evidence["hypotheses"] = {
                        "count": len(related_hyps),
                        "ids": [getattr(h, "id", "") for h in related_hyps[:5]],
                    }
            except Exception:
                pass

        rules_store = self._get_rules_store()
        if rules_store:
            try:
                active_rules = rules_store.get_active_rules()
                matched_rules = [
                    r for r in active_rules
                    if record.intent.lower() in r.get("pattern", "").lower()
                    or record.domain in r.get("category", "")
                ]
                if matched_rules:
                    record.rule_matched = True
                    evidence["matched_rules"] = {
                        "count": len(matched_rules),
                        "ids": [r.get("id", "") for r in matched_rules[:5]],
                        "patterns": [r.get("pattern", "")[:60] for r in matched_rules[:5]],
                    }
                connections += len(matched_rules)
            except Exception:
                pass

        record.memory_connections = connections
        record.prior_patterns_found = prior_patterns
        record.memory_evidence = evidence

        self._ledger.append(EpisodicEvent(
            event_type="CONVERGENCE_MEMORY_LINKED",
            summary=(
                f"Memory: {connections} connections, "
                f"{prior_patterns} prior patterns, "
                f"novel={record.is_novel}"
            ),
            intent_fingerprint=record.input_fingerprint,
            metadata={
                "connections": connections,
                "prior_patterns": prior_patterns,
                "is_novel": record.is_novel,
                "coherence_score": record.coherence_score,
                "rule_matched": record.rule_matched,
            },
        ))

        record.phases_completed.append(ConvergencePhase.MEMORY.value)
        return record

    # =====================================================================
    # Phase 4: ANÁLISIS
    # =====================================================================

    def _phase_analysis(
        self,
        record: ConvergenceRecord,
        content: str,
    ) -> ConvergenceRecord:
        """Evaluar riesgo, coherencia, contradicciones."""
        # Risk assessment via Governor policy
        policy = self._get_governor_policy()
        gov_mode = policy.get("mode", "observe")

        if gov_mode == "recover":
            record.risk_level = "high"
        elif gov_mode == "cautious":
            record.risk_level = "medium"
        elif gov_mode == "act":
            record.risk_level = "low"
        else:
            record.risk_level = "medium"

        # Contradiction detection + evaluación estratégica completa vía
        # ReasoningEngine (riesgo, impacto, escenarios, contradicciones
        # reales contra principios/decisiones pasadas, validación
        # constitucional). Una sola invocación por ciclo. Fail-safe: si
        # falla o no hay evidencia suficiente, contradictions queda en 0
        # y el resto del ciclo continúa exactamente igual que antes
        # (auditoría 2026-09-13 → conexión mínima 2026-09-13).
        #
        # Root-cause fix (2026-09-17): este bloque se movió ANTES del update
        # de CC (más abajo) porque _compute_cc_observation_score() necesita
        # record.contradictions YA resuelto para distinguir una confirmación
        # consistente de una contradicha. Esto NO cambia lo que ReasoningEngine
        # ve: _build_cognitive_context() usa record.coherence_score, que en
        # este punto sigue siendo el valor ya persistido en _phase_memory()
        # (el del ciclo anterior) — idéntico al que tenía antes de este cambio,
        # ya que el propio update de CC de abajo aún no corrió.
        record.contradictions = 0  # baseline; ajustado por _run_reasoning si corre OK
        record = self._run_reasoning(record, content)

        # Constitution CC update — observation_score ahora refleja la señal
        # REAL de ESTA observación (repetición exacta + consistencia), no un
        # piso fijo. Ver el bloque de comentarios junto a _CC_OBS_* arriba y
        # `_compute_cc_observation_score()` abajo para el razonamiento
        # completo. No modifica CCTracker ni _HIGH_COHERENCE_THRESHOLD.
        try:
            from core.learn.constitution import get_cc_tracker
            cc = get_cc_tracker()
            cc_entry = cc.update(
                fingerprint=record.input_fingerprint,
                observation_score=self._compute_cc_observation_score(record),
                domain=record.domain,
                intent=record.intent,
            )
            record.coherence_score = cc_entry.cc_score
        except Exception:
            pass

        # Capability Context (NucleusDecision ticket, 2026-09-17): mismo
        # patrón de bridge no invasivo que _run_reasoning() arriba — UNA
        # consulta por ciclo, resultado anotado en record.capability_snapshot,
        # nunca decide action_recommended por sí mismo.
        record = self._run_capability_check(record, content)

        record.phases_completed.append(ConvergencePhase.ANALYSIS.value)
        return record

    @staticmethod
    def _compute_cc_observation_score(record: ConvergenceRecord) -> float:
        """Confianza REAL de esta observación puntual para alimentar el EMA
        de `CCTracker` (core/learn/constitution.py) — root-cause fix
        2026-09-17. No modifica `CCTracker` ni `_HIGH_COHERENCE_THRESHOLD`
        (0.75): solo corrige QUÉ score se le pasa, preservando la semántica
        histórica documentada allí ("observation_score is the intent
        confidence from the latest observation") y ya usada así por otros
        consumidores reales (`core/learn/ingestion.py` pasa
        `intent.confidence`, nunca un piso fijo).

        Reglas (fail-safe, deterministas, sin I/O):
          - Primer contacto con este fingerprint EXACTO
            (`exact_repeat_count == 0`, poblado en `_phase_memory()` desde
            `CCEntry.observations`): sin evidencia previa que corroborar ->
            mismo piso histórico 0.5 (comportamiento IDÉNTICO al anterior
            para el primer mensaje — compatibilidad exacta).
          - Repetición EXACTA de ese mismo fingerprint, SIN contradicciones
            detectadas por ReasoningEngine en este ciclo
            (`contradictions == 0`): confirmación real y consistente -> score
            alto. El EMA (alpha=0.3) necesita varias confirmaciones
            consecutivas para cruzar 0.75 — nunca en una sola observación
            (ver tests/test_nucleus_decision.py::TestCCObservationScore).
          - Repetición EXACTA CON contradicciones (`contradictions > 0`): la
            evidencia se contradice a sí misma -> score bajo, que EMPUJA
            cc_score hacia abajo en la siguiente actualización EMA en vez de
            reforzarlo.

        Deliberadamente NO usa `record.is_novel` (también lo apaga un match
        genérico por `intent` en `memory.query()`, que puede dispararse para
        mensajes sin relación real entre sí) ni `prior_patterns_found` (mismo
        motivo) — usa `exact_repeat_count`, que solo aumenta cuando
        `CCTracker` ya vio ESTE fingerprint exacto antes.
        """
        if record.exact_repeat_count <= 0:
            return _CC_OBS_FIRST_SIGHTING
        if record.contradictions > 0:
            return _CC_OBS_CONTRADICTED
        return _CC_OBS_CONFIRMED

    # =====================================================================
    # Reasoning bridge — ConvergenceRecord -> CognitiveContext -> ReasoningEngine
    # =====================================================================

    @staticmethod
    def _build_cognitive_context(record: ConvergenceRecord):
        """
        Adaptador mínimo: construye un CognitiveContext (cognition.types)
        a partir de un ConvergenceRecord ya clasificado/analizado.

        No duplica modelos: reutiliza CognitiveContext/CognitiveSignal tal
        como los define cognition/types.py. No transporta contenido crudo
        del mensaje — solo metadatos ya abstraídos por las fases previas
        (tipo, intención, dominio, riesgo, novedad).
        """
        from cognition.types import (
            CognitiveContext, CognitiveSignal, SignalCategory, SignalSource,
        )

        if record.risk_level == "high":
            category = SignalCategory.ANOMALY
        elif not record.is_novel:
            category = SignalCategory.PATTERN
        elif record.is_novel:
            category = SignalCategory.CHANGE
        else:
            category = SignalCategory.NOISE

        priority = max(0.0, min(1.0, record.coherence_score or 0.0))

        signal = CognitiveSignal(
            source="total_convergence",
            source_type=SignalSource.INTERNAL,
            category=category,
            priority=priority,
            summary=(
                f"type={record.input_type} intent={record.intent} "
                f"domain={record.domain} impact={record.impact}"
            )[:200],
            intent=record.intent,
            metadata={"convergence_id": record.id, "is_novel": record.is_novel},
        )

        if record.governor_mode == "recover":
            system_health = "unhealthy"
        elif record.governor_mode == "cautious":
            system_health = "degraded"
        else:
            system_health = "healthy"

        return CognitiveContext(
            signals=[signal],
            signal_count=1,
            dominant_category=category,
            avg_priority=priority,
            governor_mode=record.governor_mode or "observe",
            system_health=system_health,
            changes_detected=1 if record.is_novel else 0,
            metadata={"convergence_record_id": record.id, "domain": record.domain},
        )

    def _run_reasoning(
        self,
        record: ConvergenceRecord,
        content: str,
    ) -> ConvergenceRecord:
        """
        Invoca ReasoningEngine.reason() UNA vez por ciclo, usando el
        adaptador ConvergenceRecord -> CognitiveContext. No reemplaza
        ninguna fase existente: solo anota record.reasoning_* para que
        _phase_synthesis() decida si escala la acción recomendada.

        Fail-safe estricto: cualquier fallo (motor no disponible,
        excepción en cualquier sub-motor, evidencia insuficiente) deja el
        record exactamente como las fases 1-4 ya lo dejaron — nunca
        bloquea, ejecuta ni inventa una acción nueva por sí mismo.
        """
        try:
            reasoning = self._get_reasoning()
            if reasoning is None:
                record.reasoning_error = "reasoning_engine_unavailable"
                return record

            context = self._build_cognitive_context(record)

            mem_ctx = None
            memory = self._get_memory()
            if memory:
                try:
                    mem_ctx = memory.query(intent=record.intent)
                except Exception:
                    mem_ctx = None

            # Descripción abstracta para ContradictionDetector — nunca el
            # contenido crudo del mensaje.
            description = (
                f"{record.input_type}:{record.intent}:{record.domain}"
            )[:200]

            result = reasoning.reason(context, mem_ctx, description=description)

            record.reasoning_ran = True
            record.reasoning_recommendation = result.recommendation
            record.reasoning_risk_level = result.risk_level
            record.contradictions = len(result.contradictions)

        except Exception as exc:
            # Fail-safe: no inventar razonamiento, no tocar action_recommended
            # ni ningún otro campo ya calculado por las fases previas.
            record.reasoning_error = str(exc)[:200]
            logger.warning(
                "ReasoningEngine failed (fail-safe, no action change): %s", exc,
            )

        return record

    # =====================================================================
    # Capability bridge — ConvergenceRecord -> CapabilityContext (NucleusDecision)
    # =====================================================================

    def _run_capability_check(
        self,
        record: ConvergenceRecord,
        content: str,
    ) -> ConvergenceRecord:
        """
        Consulta `core.self_observation.capability_context.build_capability_context()`
        UNA vez por ciclo — mismo patrón de bridge no invasivo que
        `_run_reasoning()`: el resultado se anota en `record.capability_snapshot`
        y NUNCA decide `action_recommended` ni ninguna otra fase por sí mismo.
        `_phase_synthesis()` es quien decide si usa este snapshot para poblar
        `NucleusDecision.candidate_strategy`.

        Victoria B (2026-09-17): ahora TAMBIÉN resuelve una `IntentDecision`
        REAL vía `core.intent_ssot.resolve_intent()` — el SSOT de intención
        ya existente, protegido (se LEE, nunca se modifica). Antes se usaba
        un shim con `domain=record.domain` (clasificador de RUTA DE ARCHIVO
        de `core.learn.constitution`, siempre "unknown" para texto
        conversacional) y `capability_query=False` fijo; ahora, cuando
        `resolve_intent()` responde, el shim usa su `domain`/`task_type`/
        `capability_query` reales — sin alterar `record.domain` (otros
        consumidores de ese campo, p.ej. gravity/rules_store, siguen
        exactamente igual). Si `resolve_intent()` falla, se degrada al shim
        anterior sin cambio de comportamiento.

        También resuelve, para las 4 capacidades relevantes a este ticket
        (`_TRACKED_CAPABILITY_NAMES`), disponibilidad+autorización real
        directo desde `ctx.entries` — `_FALLBACK_CANDIDATES` (módulo de
        capability_context) no incluye "places_search" ni "llm_providers"
        (pertenece a otra función, narración de fallback; no se toca), así
        que `fallback_sources` por sí solo no alcanza para ese subconjunto.

        Fail-safe estricto: cualquier fallo deja `capability_snapshot` vacío
        (`{}`) y el resto del ciclo continúa exactamente igual que antes.
        """
        try:
            from core.self_observation.capability_context import (
                build_capability_context, HEALTH_AVAILABLE,
            )

            intent_decision = None
            try:
                from core.intent_ssot import resolve_intent
                intent_decision = resolve_intent(content)
            except Exception as exc:
                logger.debug(
                    "intent_ssot.resolve_intent unavailable (fail-safe, "
                    "shim legado): %s", exc,
                )

            if intent_decision is not None and intent_decision.primary_intent:
                class _CapabilityQueryShim:
                    """IntentDecision real (intent_ssot) cuando está
                    disponible — ver docstring arriba."""
                    domain = intent_decision.domain or (
                        record.domain if record.domain != "unknown" else None
                    )
                    task_type = intent_decision.task_type or None
                    capability_query = intent_decision.capability_query
            else:
                class _CapabilityQueryShim:
                    """Shim legado (comportamiento idéntico al anterior a
                    Victoria B) cuando `resolve_intent()` no respondió."""
                    domain = record.domain if record.domain != "unknown" else None
                    task_type = None
                    capability_query = False

            ctx = build_capability_context(_CapabilityQueryShim())
            by_name = {e.name: e for e in ctx.entries}
            capability_available = {
                name: bool(
                    by_name[name].health == HEALTH_AVAILABLE
                    and by_name[name].authorized
                )
                for name in _TRACKED_CAPABILITY_NAMES
                if name in by_name
            }

            record.capability_snapshot = {
                "fallback_sources": list(ctx.fallback_sources),
                "gap_names": [g.name for g in ctx.gaps[:10]],
                # Victoria B: señales retenidas ABSTRACTAS (nunca contenido
                # crudo del mensaje) para que _build_nucleus_decision()
                # seleccione capacidad por Strategy — nunca un segundo router.
                "intent_primary": (
                    intent_decision.primary_intent if intent_decision else ""
                ),
                "intent_domain": (
                    intent_decision.domain if intent_decision else ""
                ),
                "intent_confidence": round(
                    intent_decision.confidence if intent_decision else 0.0, 4,
                ),
                "capability_available": capability_available,
                "explicit_online_request": bool(_EXPLICIT_ONLINE_RE.search(content or "")),
            }
        except Exception as exc:
            logger.debug(
                "Capability check failed (fail-safe, no action change): %s", exc,
            )

        return record

    # =====================================================================
    # Phase 5: SÍNTESIS
    # =====================================================================

    def _phase_synthesis(self, record: ConvergenceRecord) -> ConvergenceRecord:
        """
        Determinar acción recomendada basada en todo el análisis previo.

        Acciones posibles:
          - proceed: input seguro, puede procesarse normalmente
          - review: requiere revisión del creador
          - block: bloqueado por política o riesgo
          - learn_only: registrar para aprendizaje, no actuar
        """
        gov_mode = record.governor_mode

        # Hard blocks
        if gov_mode == "recover":
            record.action_recommended = "learn_only"
        elif record.risk_level == "high" and record.impact == "high":
            record.action_recommended = "review"
        # Novel high-impact inputs need review
        elif record.is_novel and record.impact == "high":
            record.action_recommended = "review"
        # Known patterns in act mode → proceed
        elif not record.is_novel and gov_mode == "act":
            record.action_recommended = "proceed"
        # Known patterns in cautious mode → proceed with caution
        elif not record.is_novel and gov_mode == "cautious":
            record.action_recommended = "proceed"
        # Novel inputs → learn only until patterns emerge
        elif record.is_novel and record.prior_patterns_found == 0:
            record.action_recommended = "learn_only"
        # Default
        else:
            record.action_recommended = "proceed"

        # Escalación por razonamiento estratégico (ReasoningEngine, Fase 4).
        # Nunca reemplaza la decisión heurística de arriba: solo la endurece
        # si ReasoningEngine detectó algo más grave. proceed no cambia nada;
        # review conserva la decisión previa pero la marca para revisión (a
        # menos que ya fuera block); block fuerza el bloqueo, que
        # should_block()/convergence_hook ya hacen cumplir en producción
        # (mecanismo de bloqueo ya existente, sin arquitectura nueva).
        if record.reasoning_ran and not record.reasoning_error:
            _strictness = {"proceed": 0, "learn_only": 0, "review": 1, "block": 2}
            current_rank = _strictness.get(record.action_recommended, 0)
            reasoning_rank = _strictness.get(record.reasoning_recommendation, 0)
            if reasoning_rank > current_rank:
                logger.info(
                    "Reasoning escalation: %s -> %s (risk=%s)",
                    record.action_recommended, record.reasoning_recommendation,
                    record.reasoning_risk_level,
                )
                record.action_recommended = record.reasoning_recommendation

        self._ledger.append(EpisodicEvent(
            event_type="CONVERGENCE_SYNTHESIZED",
            summary=(
                f"Synthesis: action={record.action_recommended} "
                f"risk={record.risk_level} novel={record.is_novel} "
                f"governor={gov_mode}"
            ),
            intent_fingerprint=record.input_fingerprint,
            metadata={
                "action": record.action_recommended,
                "risk_level": record.risk_level,
                "is_novel": record.is_novel,
                "coherence_score": record.coherence_score,
                "governor_mode": gov_mode,
            },
        ))

        record.nucleus_decision = self._build_nucleus_decision(record)

        record.phases_completed.append(ConvergencePhase.SYNTHESIS.value)
        return record

    # =====================================================================
    # NucleusDecision — acción operativa candidata (ticket 2026-09-17)
    # =====================================================================

    @staticmethod
    def _build_nucleus_decision(record: ConvergenceRecord):
        """
        Construye la `NucleusDecision` a partir de lo que las fases previas
        YA calcularon:

          0. (Victoria B, 2026-09-17) Petición EXPLÍCITA del usuario tiene
             prioridad sobre la evidencia retenida — nunca se sustituye
             silenciosamente una herramienta pedida por otra. "market"/
             "place_search" son señales ya específicas en `intent_ssot`
             (nunca el fallback genérico de cualquier pregunta factual);
             "online" sí lo es, así que ahí se exige además
             `explicit_online_request` (mención textual explícita) para no
             romper la Regresión de Victoria A.
          1. Evidencia suficiente (criterios ya existentes: `is_novel`,
             `prior_patterns_found`, `coherence_score`, `memory_evidence`
             no vacío) -> `Strategy.ANSWER_FROM_EVIDENCE`. Sin política de
             TTL/vigencia — fuera de alcance de este ticket.
          2. (Victoria B) Si no hay evidencia suficiente, el Núcleo consulta
             Capability Context y selecciona por sí mismo una capacidad
             disponible+autorizada, mapeando `IntentDecision.primary_intent`
             (`intent_ssot`, ya calculado en `_run_capability_check()`) contra
             `Strategy` — nunca contra `Intent` directamente, y nunca
             reimplementando un segundo router general: online ->
             RESOLVE_ONLINE, place_search -> RESOLVE_PLACES, market ->
             RESOLVE_MARKET, cognitive -> ROUTE_COGNITIVE (solo si
             ReasoningEngine ya calculó riesgo LOW en este mismo ciclo).
          3. Si ninguna de las anteriores aplica (ambigüedad, capacidad no
             disponible, o intent fuera de este mapeo — memory/local/
             identity/command/ai_single/ai_multi), `candidate_strategy=None`
             — `SmartRouter.route()` se comporta exactamente igual que hoy
             (selección desde texto, sin cambios).

        Fail-safe estricto: cualquier fallo (import, atributo faltante)
        devuelve una `NucleusDecision` vacía (`candidate_strategy=None`),
        nunca bloquea ni altera `record`.
        """
        try:
            from core.smart_router import Strategy
            from core.nucleus.nucleus_decision import NucleusDecision
        except Exception as exc:
            logger.debug("NucleusDecision unavailable (fail-safe, no candidate): %s", exc)
            return None

        candidate_strategy = None
        confidence = 0.0
        reason = ""
        evidence: Dict[str, Any] = {}

        intent_primary = record.capability_snapshot.get("intent_primary", "")
        capability_available: Dict[str, bool] = (
            record.capability_snapshot.get("capability_available", {}) or {}
        )
        explicit_online = bool(
            record.capability_snapshot.get("explicit_online_request", False)
        )

        has_sufficient_evidence = (
            not record.is_novel
            and record.prior_patterns_found > 0
            and record.coherence_score >= _HIGH_COHERENCE_THRESHOLD
            and bool(record.memory_evidence)
        )

        # Paso 0 (Victoria B): prioridad de petición explícita — evaluada
        # ANTES del gate de evidencia, para que gane incluso cuando hay
        # evidencia suficiente disponible.
        explicit_strategy = None
        explicit_capability = ""
        if intent_primary == "market" and capability_available.get("market_observer"):
            explicit_strategy = Strategy.RESOLVE_MARKET
            explicit_capability = "market_observer"
        elif intent_primary == "place_search" and capability_available.get("places_search"):
            explicit_strategy = Strategy.RESOLVE_PLACES
            explicit_capability = "places_search"
        elif (
            intent_primary == "online"
            and explicit_online
            and capability_available.get("online_search")
        ):
            explicit_strategy = Strategy.RESOLVE_ONLINE
            explicit_capability = "online_search"

        if explicit_strategy is not None:
            candidate_strategy = explicit_strategy
            confidence = 0.85
            reason = (
                f"petición explícita del usuario (intent={intent_primary}) + "
                f"capacidad '{explicit_capability}' disponible/autorizada — "
                f"no se sustituye por evidencia retenida"
            )
            evidence = {
                "capability_selected": explicit_capability,
                "intent_primary": intent_primary,
            }
        elif has_sufficient_evidence:
            candidate_strategy = Strategy.ANSWER_FROM_EVIDENCE
            confidence = round(record.coherence_score, 4)
            reason = (
                f"evidencia suficiente: prior_patterns_found="
                f"{record.prior_patterns_found}, coherence_score="
                f"{record.coherence_score:.2f} >= {_HIGH_COHERENCE_THRESHOLD}"
            )
            evidence = dict(record.memory_evidence)
        else:
            # Victoria B: sin evidencia suficiente -> selección de capacidad
            # por Strategy usando IntentDecision (intent_ssot, solo lectura)
            # + disponibilidad/autorización real de Capability Context.
            _cap_name = _INTENT_CAPABILITY_NAME.get(intent_primary, "")
            if _cap_name and capability_available.get(_cap_name):
                candidate_strategy = {
                    "online": Strategy.RESOLVE_ONLINE,
                    "place_search": Strategy.RESOLVE_PLACES,
                    "market": Strategy.RESOLVE_MARKET,
                }[intent_primary]
                confidence = 0.5 if intent_primary == "market" else 0.4
                reason = (
                    f"sin evidencia suficiente; intent={intent_primary} + "
                    f"capacidad '{_cap_name}' disponible/autorizada"
                )
                evidence = {"capability_selected": _cap_name}
            elif (
                intent_primary == "cognitive"
                and capability_available.get("llm_providers")
                and record.reasoning_ran
                and record.reasoning_risk_level == "LOW"
            ):
                candidate_strategy = Strategy.ROUTE_COGNITIVE
                confidence = 0.4
                reason = (
                    "sin evidencia suficiente; intent=cognitive + capacidad "
                    "'llm_providers' disponible/autorizada, riesgo LOW"
                )
                evidence = {"capability_selected": "llm_providers"}
            elif record.domain == "market" and capability_available.get("market_observer"):
                # Compatibilidad retroactiva: señal de record.domain (motor
                # de clasificación de dominio existente) cuando intent_ssot
                # no resolvió intent=market por alguna razón (p.ej. fallo).
                candidate_strategy = Strategy.RESOLVE_MARKET
                confidence = 0.5
                reason = "domain=market + capacidad de mercado disponible/autorizada"
                evidence = {"capability_selected": "market_observer"}
            else:
                reason = (
                    "sin evidencia suficiente ni capacidad identificable "
                    "(ambigüedad -> comportamiento legacy)"
                )

        return NucleusDecision(
            candidate_strategy=candidate_strategy,
            evidence=evidence,
            capability=dict(record.capability_snapshot),
            confidence=confidence,
            reason=reason,
            source="total_convergence",
        )

    # =====================================================================
    # Phase 6: GRAVITACIÓN
    # =====================================================================

    def _phase_gravitation(
        self,
        record: ConvergenceRecord,
        content: str,
    ) -> ConvergenceRecord:
        """Almacenar según peso gravitacional en el núcleo."""
        gravity = self._get_gravity()
        if gravity:
            try:
                g_rec, promotion = gravity.record_event(
                    fingerprint=record.input_fingerprint,
                    cc_score=record.coherence_score,
                    impact=record.impact,
                    domain=record.domain,
                    intent=record.intent,
                    outcome=record.action_recommended,
                    summary=(
                        f"type={record.input_type} intent={record.intent} "
                        f"action={record.action_recommended}"
                    ),
                )
                record.gravitational_tier = g_rec.tier
            except Exception:
                record.gravitational_tier = "unknown"
        else:
            record.gravitational_tier = "unknown"

        record.phases_completed.append(ConvergencePhase.GRAVITATION.value)
        return record

    # =====================================================================
    # Phase 7: APRENDIZAJE
    # =====================================================================

    def _phase_learning(
        self,
        record: ConvergenceRecord,
        content: str,
    ) -> ConvergenceRecord:
        """Alimentar hipótesis, validar reglas, evolucionar."""
        # Feed active learning orchestrator
        learning = self._get_learning()
        if learning and learning.is_active():
            try:
                learning.on_file_ingested(
                    file_path=f"convergence:{record.input_fingerprint[:8]}",
                    intent=record.intent,
                    confidence=record.coherence_score,
                )
                record.hypothesis_fed = True
            except Exception:
                pass

        # Feed hypothesis engine directly for novel inputs
        hyp_engine = self._get_hypothesis_engine()
        if hyp_engine and record.is_novel and record.intent != "unknown":
            try:
                # Check if there's a matching active hypothesis
                active = hyp_engine.get_active()
                for hyp in active:
                    if record.intent in hyp.tags or record.domain in hyp.tags:
                        from core.operator.hypothesis_engine import (
                            Evidence, EvidenceType,
                        )
                        hyp_engine.add_evidence(
                            hyp.id,
                            Evidence(
                                evidence_type=EvidenceType.OBSERVATION,
                                description=(
                                    f"Convergence input: type={record.input_type} "
                                    f"intent={record.intent}"
                                ),
                                weight=max(0.4, record.coherence_score * 0.6),
                                supports=True,
                                source="total_convergence",
                            ),
                        )
                        hyp_engine.resolve(hyp.id)
                        record.hypothesis_fed = True
            except Exception:
                pass

        # Record rule application if matched
        rules_store = self._get_rules_store()
        if rules_store and record.rule_matched:
            try:
                active_rules = rules_store.get_active_rules()
                for r in active_rules:
                    if (record.intent.lower() in r.get("pattern", "").lower()
                            or record.domain in r.get("category", "")):
                        rules_store.record_application(r["id"])
                        break
            except Exception:
                pass

        record.phases_completed.append(ConvergencePhase.LEARNING.value)
        return record

    # =====================================================================
    # Input type detection
    # =====================================================================

    @staticmethod
    def _detect_input_type(content: str) -> InputType:
        """Classify input type from content heuristics."""
        content_lower = content.lower().strip()

        # Code detection
        code_score = sum(1 for ind in _CODE_INDICATORS if ind in content)
        if code_score >= 2:
            return InputType.CODE

        # Error detection
        if any(ind in content_lower for ind in _ERROR_INDICATORS):
            if "traceback" in content_lower or "exception" in content_lower:
                return InputType.ERROR

        # Instruction detection
        first_word = content_lower.split()[0] if content_lower.split() else ""
        if first_word in _INSTRUCTION_INDICATORS:
            return InputType.INSTRUCTION
        if any(content_lower.startswith(ind) for ind in _INSTRUCTION_INDICATORS):
            return InputType.INSTRUCTION

        # Decision / result
        if any(w in content_lower for w in ("decidir", "aprobado", "rechazado", "decided")):
            return InputType.DECISION
        if any(w in content_lower for w in ("resultado", "result", "output", "completed")):
            return InputType.RESULT

        # Default to text
        return InputType.TEXT

    @staticmethod
    def _simple_intent_detect(content: str) -> str:
        """Fallback intent detection from keywords."""
        lower = content.lower()
        if any(w in lower for w in ("error", "bug", "fix", "reparar", "corregir")):
            return "BUG_FIX"
        if any(w in lower for w in ("crear", "nuevo", "add", "agregar", "implementar")):
            return "FEATURE_ADD"
        if any(w in lower for w in ("refactor", "limpiar", "mejorar", "optimizar")):
            return "REFACTOR"
        if any(w in lower for w in ("test", "prueba", "verificar")):
            return "TEST"
        if any(w in lower for w in ("config", "setup", "instalar")):
            return "CONFIG"
        return "UNKNOWN"

    # =====================================================================
    # Status
    # =====================================================================

    def status(self) -> Dict[str, Any]:
        """Estado completo del motor de convergencia total."""
        state = state_manager.load()
        policy = self._get_governor_policy()

        # Learning status
        learning = self._get_learning()
        learning_status = {}
        if learning:
            try:
                learning_status = learning.status()
            except Exception:
                learning_status = {"error": "unavailable"}

        # Hypothesis stats
        hyp_stats = {}
        hyp_engine = self._get_hypothesis_engine()
        if hyp_engine:
            try:
                hyp_stats = hyp_engine.stats()
            except Exception:
                pass

        return {
            "convergence_mode": state.get("convergence_mode", "INACTIVE"),
            "active": self.is_active,
            "activated_at": state.get("convergence_activated_at"),
            "cycles_completed": self._cycle_count,
            "total_inputs_processed": self._total_inputs,
            "governor": {
                "mode": policy.get("mode", "unknown"),
                "autopatch_allowed": policy.get("autopatch_allowed", False),
            },
            "learning": learning_status,
            "hypotheses": hyp_stats,
            "rules": self._get_rules_store().stats() if self._get_rules_store() else {},
            "memory_layers": {
                "immediate": "perception_buffer",
                "structural": "gravity_engine + cc_tracker + knowledge_graph",
                "evolutionary": "hypotheses + learned_rules",
            },
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_engine: Optional[TotalConvergenceEngine] = None


def get_convergence_engine() -> TotalConvergenceEngine:
    """Obtener la instancia singleton del motor de convergencia total."""
    global _engine
    if _engine is None:
        _engine = TotalConvergenceEngine()
    return _engine


def reset_convergence_engine() -> None:
    """Reset para testing. NO usar en producción."""
    global _engine
    _engine = None
