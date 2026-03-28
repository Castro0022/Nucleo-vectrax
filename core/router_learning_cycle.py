"""
Vectrax Router Learning Cycle — Ciclo de Aprendizaje Continuo
===============================================================
Observa decisiones del router, detecta conflictos recurrentes entre
semántico y regex, analiza pesos entre capas, y genera propuestas
concretas de mejora.

Modo: OBSERVACIÓN + PROPUESTA CONTINUA.
NUNCA aplica cambios sin aprobación del creador.

Integra:
  - RouterLearningEngine  (análisis de patrones)
  - RouterDecisionLedger  (datos persistentes)
  - state_manager         (estado del ciclo)

Ciclo:
  1. Leer feedback reciente del ledger
  2. Detectar conflictos semántico vs regex
  3. Analizar pesos entre capas (qué % decide cada una)
  4. Detectar patrones de ASK_MEMORY faltantes
  5. Calcular ajustes de threshold sugeridos
  6. Generar propuestas priorizadas
  7. Persistir propuestas en data/router_proposals.jsonl

Privacidad:
  Solo almacena métricas abstractas. Nunca contenido, identidad,
  credenciales ni datos personales.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.router_learning_cycle")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
PROPOSALS_PATH = os.path.join(DATA_DIR, "router_proposals.jsonl")


# ---------------------------------------------------------------------------
# Learning Proposal
# ---------------------------------------------------------------------------

@dataclass
class LearningProposal:
    """Propuesta generada por el ciclo de aprendizaje."""
    proposal_type: str          # conflict_pattern, threshold_adjust, weight_shift, missing_frame
    priority: str               # high, medium, low
    description: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    suggested_action: str = ""
    affected_component: str = ""   # semantic_classifier, smart_router, _LOCAL_KEYWORDS
    cycle_id: int = 0
    created_at: float = field(default_factory=time.time)
    status: str = "pending"        # pending, approved, rejected, applied

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_type": self.proposal_type,
            "priority": self.priority,
            "description": self.description,
            "evidence": self.evidence,
            "suggested_action": self.suggested_action,
            "affected_component": self.affected_component,
            "cycle_id": self.cycle_id,
            "created_at": self.created_at,
            "status": self.status,
        }


# ---------------------------------------------------------------------------
# Router Learning Cycle
# ---------------------------------------------------------------------------

class RouterLearningCycle:
    """
    Ciclo de aprendizaje continuo del router.

    Observa, detecta patrones, y genera propuestas sin aplicar cambios.

    Usage::

        cycle = get_learning_cycle()
        cycle.activate()
        result = cycle.run_cycle()
        print(result["proposals"])
    """

    # Umbrales para detección de patrones
    CONFLICT_REPETITION_THRESHOLD = 3    # min conflictos iguales para propuesta
    MIN_RECORDS_FOR_CYCLE = 10
    SEMANTIC_DOMINANCE_THRESHOLD = 0.7   # si semantic decide >70% → OK
    REGEX_FALLBACK_ALERT = 0.40          # si regex decide >40% → alerta

    def __init__(self) -> None:
        from core.router_learning import RouterDecisionLedger, RouterLearningEngine
        self._ledger = RouterDecisionLedger()
        self._engine = RouterLearningEngine(ledger=self._ledger)
        self._proposals: List[LearningProposal] = []

    # =====================================================================
    # Mode management
    # =====================================================================

    def activate(self) -> Dict[str, Any]:
        """Activate continuous learning cycle."""
        from core import state_manager
        state = state_manager.load()
        prev = state.get("router_learning_mode", "inactive")
        state["router_learning_mode"] = "active"
        state["router_learning_activated_at"] = datetime.now(timezone.utc).isoformat()
        state_manager.save(state)
        logger.info("Router learning cycle activated (was: %s)", prev)
        return {"previous_mode": prev, "new_mode": "active"}

    def deactivate(self) -> Dict[str, Any]:
        """Deactivate continuous learning cycle."""
        from core import state_manager
        state = state_manager.load()
        prev = state.get("router_learning_mode", "inactive")
        state["router_learning_mode"] = "inactive"
        state_manager.save(state)
        logger.info("Router learning cycle deactivated (was: %s)", prev)
        return {"previous_mode": prev, "new_mode": "inactive"}

    def is_active(self) -> bool:
        from core import state_manager
        return state_manager.get("router_learning_mode", "inactive") == "active"

    def get_status(self) -> Dict[str, Any]:
        """Return current learning cycle status."""
        from core import state_manager
        state = state_manager.load()
        records = self._ledger.read_all()
        proposals = self._read_proposals()
        return {
            "mode": state.get("router_learning_mode", "inactive"),
            "activated_at": state.get("router_learning_activated_at"),
            "cycles_completed": state.get("router_learning_cycles", 0),
            "total_feedback_records": len(records),
            "pending_proposals": sum(1 for p in proposals if p.get("status") == "pending"),
            "total_proposals": len(proposals),
        }

    # =====================================================================
    # Main learning cycle
    # =====================================================================

    def run_cycle(self) -> Dict[str, Any]:
        """
        Execute one complete learning cycle.

        Steps:
          1. Read recent feedback
          2. Detect semantic vs regex conflicts
          3. Analyze layer weights
          4. Detect missing memory patterns
          5. Calculate threshold suggestions
          6. Generate prioritized proposals
          7. Persist proposals

        Returns summary of cycle results.
        """
        if not self.is_active():
            return {"skipped": True, "reason": "Router learning is inactive"}

        t0 = time.time()
        records = self._ledger.read_all()

        if len(records) < self.MIN_RECORDS_FOR_CYCLE:
            return {
                "skipped": True,
                "reason": f"Insufficient data ({len(records)}/{self.MIN_RECORDS_FOR_CYCLE})",
            }

        # Increment cycle counter
        from core import state_manager
        state = state_manager.load()
        cycle_id = state.get("router_learning_cycles", 0) + 1
        state["router_learning_cycles"] = cycle_id
        state_manager.save(state)

        proposals: List[LearningProposal] = []

        # --- Step 2: Detect conflicts ---
        conflicts = self._detect_conflicts(records)

        # --- Step 3: Analyze layer weights ---
        weight_analysis = self._analyze_layer_weights(records)

        # --- Step 4: Detect missing memory patterns ---
        memory_gaps = self._detect_memory_gaps(records)

        # --- Step 5: Threshold suggestions ---
        threshold_suggestion = self._engine.suggest_threshold_adjustments()

        # --- Step 6: Generate proposals ---
        # 6a. Conflict-based proposals
        for pattern, count in conflicts.items():
            if count >= self.CONFLICT_REPETITION_THRESHOLD:
                sem_intent, regex_intent = pattern.split("→")
                proposals.append(LearningProposal(
                    proposal_type="conflict_pattern",
                    priority="high" if count >= 5 else "medium",
                    description=(
                        f"Conflicto recurrente ({count}x): semántico={sem_intent} "
                        f"vs regex={regex_intent}. Revisar clasificador semántico "
                        f"para el intent '{sem_intent}'."
                    ),
                    evidence={"semantic_intent": sem_intent, "regex_intent": regex_intent,
                              "occurrences": count},
                    suggested_action=f"Expandir patrones semánticos para '{sem_intent}'",
                    affected_component="semantic_classifier",
                    cycle_id=cycle_id,
                ))

        # 6b. Weight shift proposals
        semantic_rate = weight_analysis.get("semantic_rate", 0)
        regex_rate = weight_analysis.get("regex_rate", 0)
        if regex_rate > self.REGEX_FALLBACK_ALERT:
            proposals.append(LearningProposal(
                proposal_type="weight_shift",
                priority="medium",
                description=(
                    f"El regex decide {regex_rate:.0%} de los mensajes "
                    f"(semántico solo {semantic_rate:.0%}). "
                    f"Objetivo: que semántico decida >70%."
                ),
                evidence=weight_analysis,
                suggested_action="Expandir cobertura de frames semánticos",
                affected_component="semantic_classifier",
                cycle_id=cycle_id,
            ))

        # 6c. Memory gap proposals
        if memory_gaps.get("regex_local_without_semantic", 0) > 0:
            n = memory_gaps["regex_local_without_semantic"]
            proposals.append(LearningProposal(
                proposal_type="missing_frame",
                priority="high" if n >= 3 else "medium",
                description=(
                    f"{n} decisiones de LOCAL fueron decididas por regex sin apoyo "
                    f"semántico. Esto indica patrones faltantes en ASK_MEMORY frame."
                ),
                evidence=memory_gaps,
                suggested_action="Expandir ASK_MEMORY frame en semantic_classifier.py",
                affected_component="semantic_classifier.ASK_MEMORY",
                cycle_id=cycle_id,
            ))

        # 6d. Threshold proposal
        if (threshold_suggestion.get("status") == "ready"
                and threshold_suggestion.get("should_adjust")):
            current = threshold_suggestion["current_semantic_threshold"]
            suggested = threshold_suggestion["suggested_semantic_threshold"]
            proposals.append(LearningProposal(
                proposal_type="threshold_adjust",
                priority="low",
                description=(
                    f"Threshold semántico actual={current}, sugerido={suggested} "
                    f"basado en separación éxito/fallo."
                ),
                evidence=threshold_suggestion,
                suggested_action=f"Ajustar _SEMANTIC_CONFIDENCE_THRESHOLD a {suggested}",
                affected_component="smart_router._SEMANTIC_CONFIDENCE_THRESHOLD",
                cycle_id=cycle_id,
            ))

        # --- Step 7: Persist proposals ---
        for p in proposals:
            self._persist_proposal(p)

        elapsed_ms = (time.time() - t0) * 1000

        result = {
            "skipped": False,
            "cycle_id": cycle_id,
            "total_records_analyzed": len(records),
            "conflicts_detected": sum(conflicts.values()),
            "unique_conflict_patterns": len(conflicts),
            "weight_analysis": weight_analysis,
            "memory_gaps": memory_gaps,
            "proposals_generated": len(proposals),
            "proposals": [p.to_dict() for p in proposals],
            "elapsed_ms": round(elapsed_ms, 2),
        }

        logger.info(
            "Router learning cycle #%d: %d records, %d conflicts, %d proposals (%.0fms)",
            cycle_id, len(records), sum(conflicts.values()),
            len(proposals), elapsed_ms,
        )

        return result

    # =====================================================================
    # Analysis helpers
    # =====================================================================

    def _detect_conflicts(self, records: List[Dict]) -> Dict[str, int]:
        """
        Detect recurring conflicts between semantic and regex layers.

        A conflict is when semantic_would_have_been != final intent
        (i.e., regex overrode semantic because it was below threshold).
        """
        conflicts: Dict[str, int] = Counter()
        for r in records:
            sem_would = r.get("semantic_would_have_been")
            method = r.get("classification_method", "")
            intent = r.get("intent", "")
            # Conflict: semantic had an opinion but regex decided differently
            if sem_would and method == "regex_fallback" and sem_would != intent:
                key = f"{sem_would}→{intent}"
                conflicts[key] += 1
            # Also detect when decision_note contains "conflict:"
            note = r.get("decision_note", "")
            if "conflict:" in note and "semantic=" in note and "regex=" in note:
                # Already counted above via sem_would, but count note-based too
                pass
        return dict(conflicts)

    def _analyze_layer_weights(self, records: List[Dict]) -> Dict[str, Any]:
        """Analyze what % of decisions each layer makes."""
        total = len(records)
        if total == 0:
            return {"semantic_rate": 0, "regex_rate": 0, "command_rate": 0}

        methods = Counter(r.get("classification_method", "unknown") for r in records)
        semantic_count = methods.get("semantic", 0)
        regex_count = methods.get("regex_fallback", 0)
        # Commands don't set classification_method, count them separately
        command_count = total - semantic_count - regex_count

        # Alignment rate: when both agree
        aligned = sum(1 for r in records if r.get("regex_agrees") is True)
        conflict_below = sum(
            1 for r in records if r.get("semantic_below_threshold") is True
        )

        return {
            "total": total,
            "semantic_count": semantic_count,
            "semantic_rate": round(semantic_count / total, 4),
            "regex_count": regex_count,
            "regex_rate": round(regex_count / total, 4),
            "command_count": command_count,
            "aligned_count": aligned,
            "aligned_rate": round(aligned / max(semantic_count, 1), 4),
            "below_threshold_count": conflict_below,
        }

    def _detect_memory_gaps(self, records: List[Dict]) -> Dict[str, Any]:
        """Detect LOCAL decisions that went through regex without semantic support."""
        local_records = [r for r in records if r.get("intent") == "local"]
        if not local_records:
            return {"total_local": 0, "regex_local_without_semantic": 0}

        regex_local = [
            r for r in local_records
            if r.get("classification_method") == "regex_fallback"
        ]
        semantic_local = [
            r for r in local_records
            if r.get("classification_method") == "semantic"
        ]

        # Records where regex decided LOCAL but semantic would have said something else
        regex_local_no_sem = [
            r for r in regex_local
            if r.get("semantic_would_have_been") and r.get("semantic_would_have_been") != "local"
        ]

        return {
            "total_local": len(local_records),
            "semantic_local": len(semantic_local),
            "regex_local": len(regex_local),
            "regex_local_without_semantic": len(regex_local_no_sem),
            "semantic_coverage": round(
                len(semantic_local) / max(len(local_records), 1), 4
            ),
        }

    # =====================================================================
    # Proposal persistence
    # =====================================================================

    def _persist_proposal(self, proposal: LearningProposal) -> None:
        """Append proposal to JSONL file."""
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            with open(PROPOSALS_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(proposal.to_dict(), ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("Failed to persist proposal: %s", exc)

    def _read_proposals(self) -> List[Dict[str, Any]]:
        """Read all proposals from JSONL."""
        if not os.path.exists(PROPOSALS_PATH):
            return []
        proposals = []
        try:
            with open(PROPOSALS_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            proposals.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception:
            pass
        return proposals

    def get_pending_proposals(self) -> List[Dict[str, Any]]:
        """Get all pending proposals."""
        return [p for p in self._read_proposals() if p.get("status") == "pending"]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_cycle: Optional[RouterLearningCycle] = None


def get_learning_cycle() -> RouterLearningCycle:
    global _cycle
    if _cycle is None:
        _cycle = RouterLearningCycle()
    return _cycle
