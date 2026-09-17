"""
Tests — NucleusDecision + Strategy.ANSWER_FROM_EVIDENCE (ticket 2026-09-17)
=====================================================================
Cubre los 3 casos del criterio de aceptación:
  1. Evidencia suficiente ya conocida -> ANSWER_FROM_EVIDENCE, cero
     resolvers externos.
  2. Pregunta desconocida pero con capacidad disponible -> Strategy
     correspondiente (no ANSWER_FROM_EVIDENCE).
  3. candidate_strategy=None -> comportamiento actual de SmartRouter.route()
     sin cambios (select_strategy() SIGUE ejecutándose).

Ademas verifica, vía spy sobre `SmartRouter.select_strategy`, que cuando
hay una candidata preseleccionada `select_strategy()` NO se invoca (criterio
de aceptación "verificable en logs/tracing").
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.smart_router import SmartRouter, Strategy
from core.nucleus.nucleus_decision import NucleusDecision
from core.nucleus.total_convergence import (
    ConvergenceRecord,
    TotalConvergenceEngine,
    _HIGH_COHERENCE_THRESHOLD,
    _CC_OBS_FIRST_SIGHTING,
    _CC_OBS_CONFIRMED,
    _CC_OBS_CONTRADICTED,
)


# ---------------------------------------------------------------------------
# SmartRouter.route() — bypass contract
# ---------------------------------------------------------------------------

class TestSmartRouterNucleusBypass:

    def test_candidate_strategy_bypasses_select_strategy(self):
        """Caso (1)/(2) combinados a nivel de contrato: cuando
        `nucleus_decision.candidate_strategy` viene poblado,
        `select_strategy()` NO se ejecuta para esa request."""
        router = SmartRouter()
        nd = NucleusDecision(
            candidate_strategy=Strategy.ANSWER_FROM_EVIDENCE,
            evidence={"cc_entry": {"cc_score": 0.9}},
            confidence=0.9,
            reason="test evidence sufficient",
        )
        with patch.object(SmartRouter, "select_strategy") as mock_select:
            route = router.route("cualquier texto", "user", "tg:1", nucleus_decision=nd)

        mock_select.assert_not_called()
        assert route.strategy == Strategy.ANSWER_FROM_EVIDENCE
        assert route.confidence == 0.9
        assert route.metadata["nucleus_preselected"] is True
        assert route.metadata["nucleus_decision"]["candidate_strategy"] == "answer_from_evidence"

    def test_candidate_strategy_resolve_online_from_capability(self):
        """Caso (2): pregunta desconocida, el Núcleo propone RESOLVE_ONLINE
        porque la capacidad estaba disponible — tampoco reselecciona desde
        texto."""
        router = SmartRouter()
        nd = NucleusDecision(
            candidate_strategy=Strategy.RESOLVE_ONLINE,
            capability={"fallback_sources": ["online_search"]},
            confidence=0.4,
            reason="sin evidencia suficiente; online_search disponible",
        )
        with patch.object(SmartRouter, "select_strategy") as mock_select:
            route = router.route("pregunta totalmente nueva", "user", "tg:2", nucleus_decision=nd)

        mock_select.assert_not_called()
        assert route.strategy == Strategy.RESOLVE_ONLINE

    def test_none_nucleus_decision_preserves_current_behavior(self):
        """Caso (3): sin candidata (o `nucleus_decision=None`),
        `select_strategy()` SIGUE ejecutándose — comportamiento idéntico."""
        router = SmartRouter()
        with patch.object(
            SmartRouter, "select_strategy", wraps=router.select_strategy,
        ) as mock_select:
            route = router.route("hola que tal", "user", "tg:3", nucleus_decision=None)

        mock_select.assert_called_once()
        assert route.metadata["nucleus_preselected"] is False
        assert route.metadata["nucleus_decision"] is None

    def test_missing_nucleus_decision_param_unchanged(self):
        """Llamar route() SIN el parámetro (como todo el código existente
        sigue haciéndolo) no rompe nada — default None."""
        router = SmartRouter()
        with patch.object(
            SmartRouter, "select_strategy", wraps=router.select_strategy,
        ) as mock_select:
            route = router.route("hola", "user", "tg:4")

        mock_select.assert_called_once()
        assert route.metadata["nucleus_preselected"] is False

    def test_candidate_strategy_none_field_falls_back_to_select_strategy(self):
        """Un `NucleusDecision` real pero con `candidate_strategy=None`
        (el Núcleo no propuso nada) también preserva el comportamiento
        actual."""
        router = SmartRouter()
        nd = NucleusDecision(candidate_strategy=None, reason="sin evidencia ni capacidad")
        with patch.object(
            SmartRouter, "select_strategy", wraps=router.select_strategy,
        ) as mock_select:
            route = router.route("hola de nuevo", "user", "tg:5", nucleus_decision=nd)

        mock_select.assert_called_once()
        assert route.metadata["nucleus_preselected"] is False


# ---------------------------------------------------------------------------
# TotalConvergenceEngine._build_nucleus_decision() — decision logic
# ---------------------------------------------------------------------------

class TestBuildNucleusDecision:

    def _record(self, **overrides) -> ConvergenceRecord:
        base = dict(
            domain="unknown",
            is_novel=True,
            prior_patterns_found=0,
            coherence_score=0.0,
            memory_evidence={},
            capability_snapshot={},
        )
        base.update(overrides)
        return ConvergenceRecord(**base)

    def test_sufficient_evidence_yields_answer_from_evidence(self):
        record = self._record(
            is_novel=False,
            prior_patterns_found=3,
            coherence_score=_HIGH_COHERENCE_THRESHOLD + 0.05,
            memory_evidence={"cc_entry": {"cc_score": 0.9}},
        )
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy == Strategy.ANSWER_FROM_EVIDENCE
        assert decision.evidence == record.memory_evidence
        assert decision.confidence > 0

    def test_low_coherence_does_not_yield_answer_from_evidence(self):
        record = self._record(
            is_novel=False,
            prior_patterns_found=3,
            coherence_score=_HIGH_COHERENCE_THRESHOLD - 0.2,
            memory_evidence={"cc_entry": {"cc_score": 0.5}},
        )
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy != Strategy.ANSWER_FROM_EVIDENCE

    def test_unknown_question_with_online_capability_available(self):
        record = self._record(
            capability_snapshot={"fallback_sources": ["online_search"]},
        )
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy == Strategy.RESOLVE_ONLINE

    def test_unknown_question_market_domain_with_market_capability(self):
        record = self._record(
            domain="market",
            capability_snapshot={"fallback_sources": ["market_observer"]},
        )
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy == Strategy.RESOLVE_MARKET

    def test_no_evidence_no_capability_yields_none(self):
        record = self._record()
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy is None
        assert decision.has_candidate is False


# ---------------------------------------------------------------------------
# ExternalGateway._build_answer_from_evidence() — zero external I/O builder
# ---------------------------------------------------------------------------

class TestBuildAnswerFromEvidence:

    def test_empty_evidence_returns_empty_string(self):
        from core.operator.external_gateway import ExternalGateway
        assert ExternalGateway._build_answer_from_evidence({}) == ""

    def test_evidence_with_coherence_produces_text(self):
        from core.operator.external_gateway import ExternalGateway
        text = ExternalGateway._build_answer_from_evidence(
            {"cc_entry": {"cc_score": 0.87}},
        )
        assert text
        assert "0.87" in text

    def test_evidence_english(self):
        from core.operator.external_gateway import ExternalGateway
        text = ExternalGateway._build_answer_from_evidence(
            {"gravity_similar": {"count": 2}}, lang="en",
        )
        assert text
        assert "grounded" in text.lower()


# ---------------------------------------------------------------------------
# _compute_cc_observation_score() — root-cause fix (2026-09-17)
# ---------------------------------------------------------------------------

class TestCCObservationScore:
    """Unidad: `TotalConvergenceEngine._compute_cc_observation_score()`.
    Cubre exactamente los 3 casos pedidos, a nivel de la función pura
    (sin depender de CCTracker/EMA), más la progresión EMA real end-to-end
    contra un `CCTracker` con archivo temporal (abajo)."""

    def _record(self, **overrides) -> ConvergenceRecord:
        base = dict(exact_repeat_count=0, contradictions=0)
        base.update(overrides)
        return ConvergenceRecord(**base)

    def test_first_sighting_uses_historical_floor(self):
        """exact_repeat_count == 0 -> mismo piso histórico de siempre (0.5),
        comportamiento idéntico al código anterior para el primer mensaje."""
        record = self._record(exact_repeat_count=0, contradictions=0)
        score = TotalConvergenceEngine._compute_cc_observation_score(record)
        assert score == _CC_OBS_FIRST_SIGHTING == 0.5

    def test_confirmed_repeat_uses_high_score(self):
        """Repetición EXACTA sin contradicciones -> score alto (confirmación)."""
        record = self._record(exact_repeat_count=1, contradictions=0)
        score = TotalConvergenceEngine._compute_cc_observation_score(record)
        assert score == _CC_OBS_CONFIRMED
        assert score > _HIGH_COHERENCE_THRESHOLD

    def test_contradicted_repeat_uses_low_score(self):
        """Repetición EXACTA CON contradicciones -> score bajo (no refuerza)."""
        record = self._record(exact_repeat_count=3, contradictions=1)
        score = TotalConvergenceEngine._compute_cc_observation_score(record)
        assert score == _CC_OBS_CONTRADICTED
        assert score < _CC_OBS_FIRST_SIGHTING


class TestCoherenceScoreProgressionEndToEnd:
    """Integración: progresión REAL del EMA de `CCTracker`
    (core/learn/constitution.py) alimentado por
    `_compute_cc_observation_score()`, sin mocks del tracker — archivo
    temporal aislado para no tocar datos de producción. Cubre los 3 casos
    exactos pedidos por el ticket de causa raíz (2026-09-17):
      1. Evidencia única no alcanza 0.75.
      2. Evidencia repetida consistente SI puede alcanzarlo.
      3. Evidencia contradictoria no debe aumentarlo.
    """

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cc_path = os.path.join(self.tmpdir, "cc.jsonl")

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _tracker(self):
        from core.learn.constitution import CCTracker
        return CCTracker(path=self.cc_path)

    def test_single_evidence_does_not_reach_threshold(self):
        """Caso 1: una sola observación (exact_repeat_count=0) nunca alcanza
        _HIGH_COHERENCE_THRESHOLD — mismo piso 0.5 de siempre."""
        tracker = self._tracker()
        record = ConvergenceRecord(exact_repeat_count=0, contradictions=0)
        score = TotalConvergenceEngine._compute_cc_observation_score(record)
        entry = tracker.update("fp_single", observation_score=score)
        assert entry.cc_score < _HIGH_COHERENCE_THRESHOLD
        assert entry.cc_score == 0.5

    def test_repeated_consistent_evidence_can_reach_threshold(self):
        """Caso 2: repeticiones EXACTAS consistentes (sin contradicciones)
        SI pueden cruzar 0.75, tras varias confirmaciones reales — nunca en
        la primera observación."""
        tracker = self._tracker()
        fp = "fp_confirmed"

        # Observación 1: primer contacto -> piso 0.5 (no cruza el umbral).
        entry = tracker.update(fp, observation_score=_CC_OBS_FIRST_SIGHTING)
        assert entry.cc_score < _HIGH_COHERENCE_THRESHOLD

        # Observaciones 2..N: repetición EXACTA consistente. Confirmamos que
        # el umbral se cruza eventualmente, y NUNCA en la primera repetición
        # (exige >=2 confirmaciones consecutivas, no una sola).
        crossed_at = None
        for i in range(2, 8):
            record = ConvergenceRecord(
                exact_repeat_count=i - 1, contradictions=0,
            )
            score = TotalConvergenceEngine._compute_cc_observation_score(record)
            assert score == _CC_OBS_CONFIRMED
            entry = tracker.update(fp, observation_score=score)
            if entry.cc_score >= _HIGH_COHERENCE_THRESHOLD and crossed_at is None:
                crossed_at = i

        assert crossed_at is not None, (
            "La evidencia repetida consistente nunca cruzó el umbral"
        )
        assert crossed_at > 2, (
            "No debe cruzar el umbral en la primera repetición — "
            "requiere confirmación consistente a través de múltiples "
            "observaciones"
        )

    def test_contradictory_evidence_does_not_increase_score(self):
        """Caso 3: una vez que hay evidencia contradictoria, cc_score NO debe
        aumentar respecto al valor previo — el score contradictorio empuja
        hacia abajo, nunca refuerza."""
        tracker = self._tracker()
        fp = "fp_contradicted"

        # Construir algo de confirmación consistente primero (2 obs).
        tracker.update(fp, observation_score=_CC_OBS_FIRST_SIGHTING)
        entry = tracker.update(fp, observation_score=_CC_OBS_CONFIRMED)
        cc_before_contradiction = entry.cc_score

        # Ahora ReasoningEngine detecta una contradicción real en esta
        # repetición — el score debe caer, nunca subir.
        record = ConvergenceRecord(exact_repeat_count=2, contradictions=1)
        score = TotalConvergenceEngine._compute_cc_observation_score(record)
        assert score == _CC_OBS_CONTRADICTED
        entry = tracker.update(fp, observation_score=score)

        assert entry.cc_score < cc_before_contradiction, (
            "La evidencia contradictoria no debe aumentar coherence_score"
        )

        # Contradicciones repetidas deben seguir sin aumentarlo.
        prev = entry.cc_score
        for _ in range(3):
            record = ConvergenceRecord(exact_repeat_count=5, contradictions=1)
            score = TotalConvergenceEngine._compute_cc_observation_score(record)
            entry = tracker.update(fp, observation_score=score)
            assert entry.cc_score <= prev, (
                "Contradicciones repetidas no deben incrementar cc_score"
            )
            prev = entry.cc_score
        assert prev < _HIGH_COHERENCE_THRESHOLD


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
