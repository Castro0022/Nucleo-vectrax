"""
Tests — NucleusDecision + Strategy.ANSWER_FROM_EVIDENCE (ticket 2026-09-17)
=====================================================================
Cubre los 3 casos del criterio de aceptación:
  1. Evidencia suficiente ya conocida -> ANSWER_FROM_EVIDENCE, cero
     resolvers externos.
  2. Pregunta desconocida pero con capacidad disponible -> Strategy
     correspondiente (no ANSWER_FROM_EVIDENCE).
  3. candidate_strategy=None -> comportamiento actual de SmartRouter.route()
     sin cambios (select_strategy() SIGUE ejecut\u00e1ndose).

Ademas verifica, v\u00eda spy sobre `SmartRouter.select_strategy`, que cuando
hay una candidata preseleccionada `select_strategy()` NO se invoca (criterio
de aceptaci\u00f3n "verificable en logs/tracing").
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.smart_router import SmartRouter, Strategy
from core.nucleus.nucleus_decision import NucleusDecision
from core.nucleus.total_convergence import (
    ConvergenceRecord,
    TotalConvergenceEngine,
    _HIGH_COHERENCE_THRESHOLD,
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
        """Caso (2): pregunta desconocida, el N\u00facleo propone RESOLVE_ONLINE
        porque la capacidad estaba disponible \u2014 tampoco reselecciona desde
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
        `select_strategy()` SIGUE ejecut\u00e1ndose \u2014 comportamiento id\u00e9ntico."""
        router = SmartRouter()
        with patch.object(
            SmartRouter, "select_strategy", wraps=router.select_strategy,
        ) as mock_select:
            route = router.route("hola que tal", "user", "tg:3", nucleus_decision=None)

        mock_select.assert_called_once()
        assert route.metadata["nucleus_preselected"] is False
        assert route.metadata["nucleus_decision"] is None

    def test_missing_nucleus_decision_param_unchanged(self):
        """Llamar route() SIN el par\u00e1metro (como todo el c\u00f3digo existente
        sigue haci\u00e9ndolo) no rompe nada \u2014 default None."""
        router = SmartRouter()
        with patch.object(
            SmartRouter, "select_strategy", wraps=router.select_strategy,
        ) as mock_select:
            route = router.route("hola", "user", "tg:4")

        mock_select.assert_called_once()
        assert route.metadata["nucleus_preselected"] is False

    def test_candidate_strategy_none_field_falls_back_to_select_strategy(self):
        """Un `NucleusDecision` real pero con `candidate_strategy=None`
        (el N\u00facleo no propuso nada) tambi\u00e9n preserva el comportamiento
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
