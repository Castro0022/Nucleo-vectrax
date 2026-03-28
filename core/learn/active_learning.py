"""
Vectrax Active Learning Orchestrator
========================================
Connects all learning subsystem components into a continuous
autonomous learning cycle:

  detect patterns → generate hypotheses → validate with evidence
  → promote to persistent rules → integrate into behaviour

Components orchestrated:
  - Watchdog (filesystem monitoring)
  - Governor (policy validation)
  - HypothesisEngine (hypothesis lifecycle)
  - AscensionGate (candidate management)
  - LearnedRulesStore (rule persistence)
  - Constitution / CC Tracker (consistency tracking)
  - Episodic Ledger (audit trail)

Privacy: only abstract operational metrics are stored — never raw
content, personal data, credentials, or PII.

Creado: 2026-03-19
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core import state_manager
from core.learn.ascension_gate import get_gate
from core.learn.episodic import get_ledger, EpisodicEvent
from core.learn.learned_rules import LearnedRule, get_rules_store

logger = logging.getLogger("vectrax.learn.active_learning")

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

PATTERN_REPETITION_THRESHOLD = 3    # min repetitions to generate hypothesis
HYPOTHESIS_CONFIDENCE_PROMOTE = 0.80  # min confidence to promote to rule
CANDIDATE_RELEASE_CONFIDENCE = 0.6  # min confidence to release from quarantine
MAX_HYPOTHESES_PER_CYCLE = 5        # limit hypothesis generation per cycle
MAX_RULES_PER_CYCLE = 3             # limit rule promotion per cycle


# ---------------------------------------------------------------------------
# Active Learning Orchestrator
# ---------------------------------------------------------------------------

class ActiveLearningOrchestrator:
    """
    Central orchestrator for the autonomous learning subsystem.

    Implements the complete learning cycle:
      1. Detect repeated patterns in candidates and ledger
      2. Generate hypotheses from detected patterns
      3. Validate hypotheses with new evidence
      4. Promote confirmed hypotheses to persistent rules
      5. Release quarantined candidates for evaluation
      6. All through Governor policy control

    Usage::

        orchestrator = get_orchestrator()
        orchestrator.activate()      # switch to ACTIVE_LEARNING
        result = orchestrator.run_cycle()  # one learning cycle
    """

    def __init__(self) -> None:
        self._gate = get_gate()
        self._ledger = get_ledger()
        self._rules_store = get_rules_store()
        self._hypothesis_engine = None  # lazy
        self._governor = None           # lazy
        logger.info("ActiveLearningOrchestrator initialized")

    # -- Lazy imports (avoid circular) ------------------------------------

    def _get_hypothesis_engine(self):
        if self._hypothesis_engine is None:
            from core.operator.hypothesis_engine import get_hypothesis_engine
            self._hypothesis_engine = get_hypothesis_engine()
        return self._hypothesis_engine

    def _get_governor_policy(self) -> Dict[str, Any]:
        try:
            from core.governor import get_current_policy
            return get_current_policy()
        except Exception:
            return {"mode": "observe", "autopatch_allowed": False}

    # =====================================================================
    # Mode management
    # =====================================================================

    def activate(self) -> Dict[str, Any]:
        """
        Switch from OBSERVATION to ACTIVE_LEARNING mode.

        Activates:
          - Hypothesis generation from patterns
          - Candidate release from quarantine
          - Continuous learning cycle
        """
        state = state_manager.load()
        prev_mode = state.get("learning_mode", "OBSERVATION")

        state["learning_mode"] = "ACTIVE_LEARNING"
        state["learning_activated_at"] = datetime.now(timezone.utc).isoformat()
        state_manager.save(state)

        self._ledger.append(EpisodicEvent(
            event_type="LEARNING_MODE_CHANGED",
            summary=f"Learning mode: {prev_mode} → ACTIVE_LEARNING",
            metadata={
                "previous_mode": prev_mode,
                "new_mode": "ACTIVE_LEARNING",
            },
        ))

        logger.info("Learning mode activated: %s → ACTIVE_LEARNING", prev_mode)

        return {
            "previous_mode": prev_mode,
            "new_mode": "ACTIVE_LEARNING",
            "activated_at": state["learning_activated_at"],
        }

    def deactivate(self) -> Dict[str, Any]:
        """Return to OBSERVATION mode."""
        state = state_manager.load()
        prev_mode = state.get("learning_mode", "OBSERVATION")
        state["learning_mode"] = "OBSERVATION"
        state_manager.save(state)

        self._ledger.append(EpisodicEvent(
            event_type="LEARNING_MODE_CHANGED",
            summary=f"Learning mode: {prev_mode} → OBSERVATION",
            metadata={"previous_mode": prev_mode, "new_mode": "OBSERVATION"},
        ))

        return {"previous_mode": prev_mode, "new_mode": "OBSERVATION"}

    def is_active(self) -> bool:
        """Check if active learning is enabled."""
        return state_manager.get("learning_mode", "OBSERVATION") == "ACTIVE_LEARNING"

    # =====================================================================
    # Main learning cycle
    # =====================================================================

    def run_cycle(self) -> Dict[str, Any]:
        """
        Execute one complete learning cycle.

        Steps:
          1. Check Governor policy (skip if in recover mode)
          2. Detect patterns from candidates
          3. Generate hypotheses from patterns
          4. Validate existing hypotheses with new evidence
          5. Promote confirmed hypotheses to rules
          6. Release quarantined candidates
          7. Update state counters

        Returns a summary of the cycle results.
        """
        if not self.is_active():
            return {"skipped": True, "reason": "Learning mode is OBSERVATION"}

        t0 = time.time()
        cycle_result: Dict[str, Any] = {
            "skipped": False,
            "patterns_detected": 0,
            "hypotheses_generated": 0,
            "hypotheses_validated": 0,
            "rules_promoted": 0,
            "candidates_released": 0,
            "governor_mode": "unknown",
        }

        # --- 1. Governor check ---
        policy = self._get_governor_policy()
        cycle_result["governor_mode"] = policy.get("mode", "unknown")

        if policy.get("mode") == "recover":
            cycle_result["skipped"] = True
            cycle_result["reason"] = "Governor in RECOVER mode — learning paused"
            logger.warning("Learning cycle skipped: Governor in recover")
            return cycle_result

        # --- 2. Detect patterns ---
        patterns = self._detect_patterns()
        cycle_result["patterns_detected"] = len(patterns)

        # --- 3. Generate hypotheses ---
        new_hypotheses = self._generate_hypotheses(patterns, policy)
        cycle_result["hypotheses_generated"] = len(new_hypotheses)

        # --- 4. Validate existing hypotheses ---
        validated = self._validate_hypotheses()
        cycle_result["hypotheses_validated"] = validated

        # --- 5. Promote confirmed hypotheses to rules ---
        promoted = self._promote_to_rules(policy)
        cycle_result["rules_promoted"] = promoted

        # --- 6. Release quarantined candidates ---
        if policy.get("mode") in ("act", "cautious"):
            released = self._release_quarantined()
            cycle_result["candidates_released"] = len(released)

        # --- 7. Update state ---
        elapsed = time.time() - t0
        cycle_result["elapsed_seconds"] = round(elapsed, 3)

        state = state_manager.load()
        state["learning_cycles"] = state.get("learning_cycles", 0) + 1
        state["hypotheses_generated"] = (
            state.get("hypotheses_generated", 0) + len(new_hypotheses)
        )
        state["rules_learned"] = (
            state.get("rules_learned", 0) + promoted
        )
        state_manager.save(state)

        self._ledger.append(EpisodicEvent(
            event_type="LEARNING_CYCLE_COMPLETED",
            summary=(
                f"Cycle #{state['learning_cycles']}: "
                f"{len(patterns)} patterns, {len(new_hypotheses)} hypotheses, "
                f"{promoted} rules"
            ),
            metadata=cycle_result,
        ))

        logger.info(
            "Learning cycle #%d: patterns=%d hyp=%d validated=%d rules=%d (%.2fs)",
            state["learning_cycles"], len(patterns), len(new_hypotheses),
            validated, promoted, elapsed,
        )

        return cycle_result

    # =====================================================================
    # Pattern detection
    # =====================================================================

    def _detect_patterns(self) -> List[Dict[str, Any]]:
        """
        Detect repeated patterns from candidates and ledger.

        Looks for:
          - Repeated intents across files
          - Repeated structure patterns (same symbols, same domains)
          - Frequency anomalies in CC tracker
        """
        patterns: List[Dict[str, Any]] = []

        # --- Pattern 1: Repeated intents in candidates ---
        candidates = self._gate.list_all()
        intent_counter: Counter = Counter()
        domain_counter: Counter = Counter()
        intent_examples: Dict[str, List[str]] = {}

        for c in candidates:
            intent = c.get("intent", "UNKNOWN")
            domain = c.get("domain", "unknown")
            intent_counter[intent] += 1
            domain_counter[domain] += 1
            if intent not in intent_examples:
                intent_examples[intent] = []
            if len(intent_examples[intent]) < 3:
                intent_examples[intent].append(c.get("file_path", ""))

        for intent, count in intent_counter.items():
            if count >= PATTERN_REPETITION_THRESHOLD and intent != "UNKNOWN":
                patterns.append({
                    "type": "repeated_intent",
                    "intent": intent,
                    "count": count,
                    "examples": intent_examples.get(intent, []),
                    "description": (
                        f"Intent '{intent}' detected {count} times "
                        f"across candidates"
                    ),
                })

        # --- Pattern 2: Domain concentration ---
        total_candidates = len(candidates) or 1
        for domain, count in domain_counter.items():
            ratio = count / total_candidates
            if count >= PATTERN_REPETITION_THRESHOLD and ratio >= 0.3:
                patterns.append({
                    "type": "domain_concentration",
                    "domain": domain,
                    "count": count,
                    "ratio": round(ratio, 2),
                    "description": (
                        f"Domain '{domain}' represents {ratio:.0%} "
                        f"of all candidates ({count}/{total_candidates})"
                    ),
                })

        # --- Pattern 3: CC-based patterns ---
        try:
            from core.learn.constitution import get_cc_tracker
            cc_tracker = get_cc_tracker()
            entries = cc_tracker.all_entries()

            high_cc = [e for e in entries if e.cc_score >= 0.7 and e.observations >= 3]
            for entry in high_cc[:5]:
                patterns.append({
                    "type": "high_cc_pattern",
                    "fingerprint": entry.fingerprint,
                    "cc_score": entry.cc_score,
                    "observations": entry.observations,
                    "domain": entry.domain,
                    "description": (
                        f"High CC pattern: {entry.fingerprint} "
                        f"(cc={entry.cc_score:.2f}, obs={entry.observations})"
                    ),
                })
        except Exception:
            pass

        return patterns

    # =====================================================================
    # Hypothesis generation
    # =====================================================================

    def _generate_hypotheses(
        self,
        patterns: List[Dict[str, Any]],
        policy: Dict[str, Any],
    ) -> List[str]:
        """
        Generate hypotheses from detected patterns.

        Returns list of new hypothesis IDs.
        """
        engine = self._get_hypothesis_engine()
        new_ids: List[str] = []

        for pattern in patterns[:MAX_HYPOTHESES_PER_CYCLE]:
            ptype = pattern.get("type", "unknown")
            description = ""
            tags = [ptype]

            if ptype == "repeated_intent":
                description = (
                    f"El sistema muestra un patrón recurrente de intent "
                    f"'{pattern['intent']}' ({pattern['count']}x). "
                    f"Esto sugiere una tendencia de desarrollo activa en esa área."
                )
                tags.append(pattern["intent"])

            elif ptype == "domain_concentration":
                description = (
                    f"El dominio '{pattern['domain']}' concentra "
                    f"{pattern['ratio']:.0%} de los candidatos. "
                    f"Puede indicar un foco de desarrollo principal."
                )
                tags.append(pattern["domain"])

            elif ptype == "high_cc_pattern":
                description = (
                    f"Patrón con alta consistencia cognitiva: "
                    f"{pattern['fingerprint']} (CC={pattern['cc_score']:.2f}). "
                    f"Observado {pattern['observations']}x. "
                    f"Candidato para convertirse en regla estable."
                )
                tags.append("high_cc")

            if not description:
                continue

            # Check for existing similar hypothesis (avoid duplicates)
            existing = engine.get_by_tag(ptype)
            already_exists = any(
                ptype in h.tags
                and pattern.get("intent", pattern.get("domain", ""))
                in h.description
                for h in existing
                if h.status.value in ("proposed", "testing")
            )
            if already_exists:
                continue

            hyp = engine.propose(
                description=description,
                context=f"Pattern detected: {pattern.get('description', '')}",
                initial_confidence=0.5,
                tags=tags,
                metadata={
                    "pattern": pattern,
                    "governor_mode": policy.get("mode", "unknown"),
                },
            )
            new_ids.append(hyp.id)

            self._ledger.append(EpisodicEvent(
                event_type="HYPOTHESIS_FROM_PATTERN",
                summary=f"Hypothesis {hyp.id} from {ptype}: {description[:80]}",
                metadata={
                    "hypothesis_id": hyp.id,
                    "pattern_type": ptype,
                    "confidence": hyp.confidence,
                },
            ))

        return new_ids

    # =====================================================================
    # Hypothesis validation
    # =====================================================================

    def _validate_hypotheses(self) -> int:
        """
        Validate active hypotheses with evidence from recent activity.

        Returns count of hypotheses that received new evidence.
        """
        engine = self._get_hypothesis_engine()
        active = engine.get_active()
        validated = 0

        if not active:
            return 0

        # Gather recent evidence from candidates
        candidates = self._gate.list_all()
        recent_candidates = sorted(
            candidates,
            key=lambda c: c.get("created_at", ""),
            reverse=True,
        )[:50]

        intent_counts = Counter(c.get("intent", "") for c in recent_candidates)
        domain_counts = Counter(c.get("domain", "") for c in recent_candidates)

        for hyp in active:
            evidence_added = False

            for tag in hyp.tags:
                # Check if intent pattern still holds
                if tag in intent_counts and intent_counts[tag] >= 2:
                    from core.operator.hypothesis_engine import Evidence, EvidenceType
                    engine.add_evidence(
                        hyp.id,
                        Evidence(
                            evidence_type=EvidenceType.METRIC,
                            description=(
                                f"Intent '{tag}' seen {intent_counts[tag]}x "
                                f"in recent candidates"
                            ),
                            weight=min(0.8, intent_counts[tag] * 0.15),
                            supports=True,
                            source="active_learning_cycle",
                        ),
                    )
                    evidence_added = True

                # Check domain concentration
                if tag in domain_counts and domain_counts[tag] >= 3:
                    from core.operator.hypothesis_engine import Evidence, EvidenceType
                    engine.add_evidence(
                        hyp.id,
                        Evidence(
                            evidence_type=EvidenceType.OBSERVATION,
                            description=(
                                f"Domain '{tag}' has {domain_counts[tag]} "
                                f"recent candidates"
                            ),
                            weight=0.6,
                            supports=True,
                            source="active_learning_cycle",
                        ),
                    )
                    evidence_added = True

            if evidence_added:
                # Try to resolve
                engine.resolve(hyp.id)
                validated += 1

                self._ledger.append(EpisodicEvent(
                    event_type="HYPOTHESIS_VALIDATED",
                    summary=(
                        f"Hypothesis {hyp.id} validated: "
                        f"confidence={hyp.confidence:.2f} "
                        f"status={hyp.status.value}"
                    ),
                    metadata={
                        "hypothesis_id": hyp.id,
                        "confidence": hyp.confidence,
                        "status": hyp.status.value,
                    },
                ))

        return validated

    # =====================================================================
    # Rule promotion
    # =====================================================================

    def _promote_to_rules(self, policy: Dict[str, Any]) -> int:
        """
        Convert confirmed hypotheses to persistent learned rules.

        Only promotes if Governor is in 'act' or 'cautious' mode.
        In 'cautious' mode, rules are created as 'pending'.
        In 'act' mode, rules are auto-activated.
        """
        engine = self._get_hypothesis_engine()
        from core.operator.hypothesis_engine import HypothesisStatus

        confirmed = engine.get_by_status(HypothesisStatus.CONFIRMED)
        promoted = 0
        gov_mode = policy.get("mode", "observe")

        if gov_mode not in ("act", "cautious"):
            return 0

        for hyp in confirmed[:MAX_RULES_PER_CYCLE]:
            # Check if rule already exists for this hypothesis
            existing = self._rules_store.list_all()
            already_has_rule = any(
                r.get("source_hypothesis_id") == hyp.id for r in existing
            )
            if already_has_rule:
                continue

            # Derive category from tags
            category = "general"
            if "repeated_intent" in hyp.tags:
                category = "development_pattern"
            elif "domain_concentration" in hyp.tags:
                category = "domain_focus"
            elif "high_cc" in hyp.tags:
                category = "cognitive_pattern"

            rule = LearnedRule(
                description=hyp.description,
                source_hypothesis_id=hyp.id,
                confidence=hyp.confidence,
                pattern=", ".join(hyp.tags),
                action=(
                    f"Apply learned pattern from hypothesis {hyp.id}: "
                    f"{hyp.description[:100]}"
                ),
                category=category,
                governor_mode_at_creation=gov_mode,
                metadata={
                    "evidence_count": len(hyp.evidence),
                    "context": hyp.context,
                },
            )

            rule_id = self._rules_store.add_rule(rule)

            # Auto-activate in 'act' mode
            if gov_mode == "act":
                self._rules_store.activate_rule(rule_id)

            promoted += 1

            self._ledger.append(EpisodicEvent(
                event_type="RULE_LEARNED",
                summary=(
                    f"Rule {rule_id} from hypothesis {hyp.id}: "
                    f"{hyp.description[:80]}"
                ),
                metadata={
                    "rule_id": rule_id,
                    "hypothesis_id": hyp.id,
                    "confidence": hyp.confidence,
                    "category": category,
                    "auto_activated": gov_mode == "act",
                },
            ))

            logger.info(
                "Rule promoted: %s from %s (confidence=%.2f, auto=%s)",
                rule_id, hyp.id, hyp.confidence, gov_mode == "act",
            )

        return promoted

    # =====================================================================
    # Candidate release
    # =====================================================================

    def _release_quarantined(self) -> List[str]:
        """Release high-confidence quarantined candidates for evaluation."""
        return self._gate.release_for_evaluation(
            min_confidence=CANDIDATE_RELEASE_CONFIDENCE,
        )

    # =====================================================================
    # Event hooks (called from watchdog)
    # =====================================================================

    def on_file_ingested(self, file_path: str, intent: str, confidence: float) -> None:
        """
        Called by watchdog after a file is ingested in ACTIVE_LEARNING mode.

        Feeds new data into the hypothesis validation pipeline.
        """
        if not self.is_active():
            return

        engine = self._get_hypothesis_engine()
        active = engine.get_active()

        # Add observation evidence to matching hypotheses
        for hyp in active:
            if intent in hyp.tags or any(
                intent.lower() in tag.lower() for tag in hyp.tags
            ):
                from core.operator.hypothesis_engine import Evidence, EvidenceType
                engine.add_evidence(
                    hyp.id,
                    Evidence(
                        evidence_type=EvidenceType.OBSERVATION,
                        description=(
                            f"File ingested: {file_path} "
                            f"(intent={intent}, conf={confidence:.2f})"
                        ),
                        weight=confidence * 0.7,
                        supports=True,
                        source="watchdog_active",
                    ),
                )
                engine.resolve(hyp.id)

    # =====================================================================
    # Status
    # =====================================================================

    def status(self) -> Dict[str, Any]:
        """Full status of the active learning subsystem."""
        state = state_manager.load()
        engine = self._get_hypothesis_engine()
        gate_counts = self._gate.count_by_state()
        rules_stats = self._rules_store.stats()
        hyp_stats = engine.stats()

        policy = self._get_governor_policy()

        return {
            "learning_mode": state.get("learning_mode", "OBSERVATION"),
            "activated_at": state.get("learning_activated_at"),
            "learning_cycles": state.get("learning_cycles", 0),
            "governor": {
                "mode": policy.get("mode", "unknown"),
                "autopatch_allowed": policy.get("autopatch_allowed", False),
            },
            "watchdog": {
                "status": "ACTIVE" if self._is_watchdog_running() else "INACTIVE",
            },
            "hypotheses": hyp_stats,
            "candidates": gate_counts,
            "rules": rules_stats,
            "counters": {
                "hypotheses_generated": state.get("hypotheses_generated", 0),
                "rules_learned": state.get("rules_learned", 0),
            },
        }

    @staticmethod
    def _is_watchdog_running() -> bool:
        try:
            from core.learn.watchdog import is_running
            return is_running()
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_orchestrator: Optional[ActiveLearningOrchestrator] = None


def get_orchestrator() -> ActiveLearningOrchestrator:
    """Get the singleton ActiveLearningOrchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = ActiveLearningOrchestrator()
    return _orchestrator


def reset_orchestrator() -> None:
    """Reset for testing. DO NOT use in production."""
    global _orchestrator
    _orchestrator = None
