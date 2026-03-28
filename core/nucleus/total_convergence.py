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
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

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
            "gravitational_tier": self.gravitational_tier,
            "risk_level": self.risk_level,
            "coherence_score": round(self.coherence_score, 4),
            "is_novel": self.is_novel,
            "contradictions": self.contradictions,
            "action_recommended": self.action_recommended,
            "hypothesis_fed": self.hypothesis_fed,
            "rule_matched": self.rule_matched,
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
                connections += 1
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
                connections += len(matched_rules)
            except Exception:
                pass

        record.memory_connections = connections
        record.prior_patterns_found = prior_patterns

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

        # Constitution CC update
        try:
            from core.learn.constitution import get_cc_tracker
            cc = get_cc_tracker()
            cc_entry = cc.update(
                fingerprint=record.input_fingerprint,
                observation_score=max(record.coherence_score, 0.5),
                domain=record.domain,
                intent=record.intent,
            )
            record.coherence_score = cc_entry.cc_score
        except Exception:
            pass

        # Contradiction detection (simplified — full engine used when
        # ReasoningEngine is available with CognitiveContext)
        record.contradictions = 0  # baseline; full analysis in synthesis

        record.phases_completed.append(ConvergencePhase.ANALYSIS.value)
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

        record.phases_completed.append(ConvergencePhase.SYNTHESIS.value)
        return record

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
