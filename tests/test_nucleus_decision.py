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
        """Victoria B: contrato actualizado de `capability_snapshot`
        (`intent_primary` + `capability_available`), no ya el
        `fallback_sources` crudo (que ni siquiera incluye "places_search"/
        "llm_providers" — ver comentario junto a `_TRACKED_CAPABILITY_NAMES`
        en total_convergence.py)."""
        record = self._record(
            capability_snapshot={
                "intent_primary": "online",
                "capability_available": {"online_search": True},
            },
        )
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy == Strategy.RESOLVE_ONLINE

    def test_unknown_question_market_domain_with_market_capability(self):
        record = self._record(
            domain="market",
            capability_snapshot={
                "intent_primary": "market",
                "capability_available": {"market_observer": True},
            },
        )
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy == Strategy.RESOLVE_MARKET

    def test_no_evidence_no_capability_yields_none(self):
        record = self._record()
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy is None
        assert decision.has_candidate is False

    def test_fingerprint_always_set_regardless_of_candidate(self):
        """Victoria C: `fingerprint` viaja SIEMPRE, incluso en la rama de
        ambigüedad (`candidate_strategy=None`) — el cable de retorno de
        evidencia externa lo necesita independientemente de la decisión."""
        record = self._record(input_fingerprint="fp-ambiguous")
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy is None
        assert decision.fingerprint == "fp-ambiguous"

        record2 = self._record(
            input_fingerprint="fp-evidence",
            is_novel=False,
            prior_patterns_found=3,
            coherence_score=_HIGH_COHERENCE_THRESHOLD + 0.05,
            memory_evidence={"cc_entry": {"cc_score": 0.9}},
        )
        decision2 = TotalConvergenceEngine._build_nucleus_decision(record2)
        assert decision2.candidate_strategy == Strategy.ANSWER_FROM_EVIDENCE
        assert decision2.fingerprint == "fp-evidence"


# ---------------------------------------------------------------------------
# Victoria B (2026-09-17) — selección de capacidad por Strategy cuando la
# evidencia es insuficiente, y prioridad de petición explícita.
# ---------------------------------------------------------------------------

class TestVictoriaBCapabilitySelection:
    """Cubre, a nivel de `_build_nucleus_decision()`, los 6 casos de
    aceptación del ticket Victoria B (verificación end-to-end real en
    producción por separado)."""

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

    def test_case1_online_new_question_selects_resolve_online(self):
        record = self._record(
            capability_snapshot={
                "intent_primary": "online",
                "capability_available": {"online_search": True},
            },
        )
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy == Strategy.RESOLVE_ONLINE
        assert decision.evidence["capability_selected"] == "online_search"

    def test_case2_places_selects_resolve_places(self):
        record = self._record(
            capability_snapshot={
                "intent_primary": "place_search",
                "capability_available": {"places_search": True},
            },
        )
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy == Strategy.RESOLVE_PLACES
        assert decision.evidence["capability_selected"] == "places_search"

    def test_case3_market_selects_resolve_market(self):
        record = self._record(
            capability_snapshot={
                "intent_primary": "market",
                "capability_available": {"market_observer": True},
            },
        )
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy == Strategy.RESOLVE_MARKET
        assert decision.evidence["capability_selected"] == "market_observer"

    def test_case4_explicit_online_wins_over_sufficient_evidence(self):
        """Petición explícita ('online' mencionado textualmente) tiene
        prioridad incluso cuando la evidencia retenida SERÍA suficiente
        para ANSWER_FROM_EVIDENCE — nunca se sustituye silenciosamente."""
        record = self._record(
            is_novel=False,
            prior_patterns_found=5,
            coherence_score=_HIGH_COHERENCE_THRESHOLD + 0.1,
            memory_evidence={"cc_entry": {"cc_score": 0.9}},
            capability_snapshot={
                "intent_primary": "online",
                "capability_available": {"online_search": True},
                "explicit_online_request": True,
            },
        )
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy == Strategy.RESOLVE_ONLINE
        assert "explícita" in decision.reason

    def test_case4b_explicit_market_wins_over_sufficient_evidence_without_keyword(self):
        """market/place_search NO necesitan mención textual explícita —
        intent_ssot ya las trata como señal específica (nunca el fallback
        genérico de cualquier pregunta), así que también ganan sobre
        evidencia suficiente."""
        record = self._record(
            is_novel=False,
            prior_patterns_found=5,
            coherence_score=_HIGH_COHERENCE_THRESHOLD + 0.1,
            memory_evidence={"cc_entry": {"cc_score": 0.9}},
            capability_snapshot={
                "intent_primary": "market",
                "capability_available": {"market_observer": True},
            },
        )
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy == Strategy.RESOLVE_MARKET

    def test_case4c_online_without_explicit_keyword_does_not_override_evidence(self):
        """Regresión de Victoria A (implícita en el caso 4): intent=online
        SIN mención textual explícita NO debe robarle la respuesta a
        evidencia suficiente — de lo contrario cualquier pregunta factual
        repetida (clasificada online por defecto) rompería ANSWER_FROM_EVIDENCE."""
        record = self._record(
            is_novel=False,
            prior_patterns_found=5,
            coherence_score=_HIGH_COHERENCE_THRESHOLD + 0.1,
            memory_evidence={"cc_entry": {"cc_score": 0.9}},
            capability_snapshot={
                "intent_primary": "online",
                "capability_available": {"online_search": True},
                "explicit_online_request": False,
            },
        )
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy == Strategy.ANSWER_FROM_EVIDENCE

    def test_case5_ambiguous_intent_yields_none(self):
        """intent fuera del mapeo (memory/local/identity/...) -> ambigüedad,
        candidate_strategy=None -> comportamiento legacy."""
        record = self._record(
            capability_snapshot={
                "intent_primary": "memory",
                "capability_available": {"online_search": True},
            },
        )
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy is None

    def test_case5b_capability_unavailable_yields_none(self):
        """intent=online pero la capacidad NO está disponible/autorizada ->
        ambigüedad, nunca se inventa una Strategy sin respaldo real."""
        record = self._record(
            capability_snapshot={
                "intent_primary": "online",
                "capability_available": {"online_search": False},
            },
        )
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy is None

    def test_case6_regression_answer_from_evidence_unaffected(self):
        """Regresión de Victoria A explícita: evidencia suficiente sin
        ninguna señal de intent_ssot (capability_snapshot vacío, como si
        intent_ssot no estuviera disponible) sigue produciendo
        ANSWER_FROM_EVIDENCE exactamente igual."""
        record = self._record(
            is_novel=False,
            prior_patterns_found=3,
            coherence_score=_HIGH_COHERENCE_THRESHOLD + 0.05,
            memory_evidence={"cc_entry": {"cc_score": 0.9}},
            capability_snapshot={},
        )
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy == Strategy.ANSWER_FROM_EVIDENCE

    def test_cognitive_requires_low_risk_from_reasoning(self):
        """cognitive solo se propone si ReasoningEngine YA calculó riesgo
        LOW en este ciclo — nunca reimplementa la degradación a multi-modelo
        que ya hace select_strategy()/evaluate_policy()."""
        record = self._record(
            capability_snapshot={
                "intent_primary": "cognitive",
                "capability_available": {"llm_providers": True},
            },
            reasoning_ran=True,
            reasoning_risk_level="HIGH",
        )
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy is None

        record_low_risk = self._record(
            capability_snapshot={
                "intent_primary": "cognitive",
                "capability_available": {"llm_providers": True},
            },
            reasoning_ran=True,
            reasoning_risk_level="LOW",
        )
        decision_low_risk = TotalConvergenceEngine._build_nucleus_decision(record_low_risk)
        assert decision_low_risk.candidate_strategy == Strategy.ROUTE_COGNITIVE


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

    # -- Victoria C (Corte 3): provenance real de external_evidence --------

    def test_external_evidence_items_surfaces_real_source_reference(self):
        """Con `external_evidence.items[].source_reference`, la respuesta
        DEBE conservar/exponer la provenance real almacenada (RESOLVE_ONLINE/
        PLACES/MARKET/ROUTE_COGNITIVE ya capturados por `_capture_evidence()`),
        no solo un conteo abstracto."""
        from core.operator.external_gateway import ExternalGateway
        evidence = {
            "cc_entry": {"cc_score": 0.87},
            "external_evidence": {
                "count": 1,
                "items": [{
                    "content": "La capital de Botsuana es Gaborone.",
                    "source_type": "online",
                    "source": "tavily",
                    "source_reference": "https://es.wikipedia.org/wiki/Gaborone,https://otra.example",
                    "observed_at": "2026-09-18T00:12:29+00:00",
                    "correlation_id": "corr-1",
                    "confidence": None,
                }],
            },
        }
        text = ExternalGateway._build_answer_from_evidence(evidence)
        assert text
        assert "tavily" in text
        assert "https://es.wikipedia.org/wiki/Gaborone" in text
        assert "2026-09-18T00:12:29+00:00" in text
        # Solo la primera referencia (evita citar todas las URLs crudas).
        assert "otra.example" not in text

    def test_external_evidence_alone_still_produces_text(self):
        """Si `external_evidence` es la ÚNICA señal presente (sin cc_entry/
        structural_memory/gravity_similar/matched_rules), la respuesta ya
        no debe quedar vacía — antes de esta corrección, `parts` quedaba
        vacío y se devolvía ""."""
        from core.operator.external_gateway import ExternalGateway
        evidence = {
            "external_evidence": {
                "count": 1,
                "items": [{
                    "source": "tavily",
                    "source_reference": "https://example.com/page",
                    "observed_at": "2026-01-01T00:00:00+00:00",
                }],
            },
        }
        text = ExternalGateway._build_answer_from_evidence(evidence)
        assert text
        assert "tavily" in text
        assert "https://example.com/page" in text

    def test_without_external_evidence_behavior_unchanged(self):
        """Sin `external_evidence` en absoluto, el comportamiento previo
        queda intacto — mismo texto que antes de esta corrección."""
        from core.operator.external_gateway import ExternalGateway
        text = ExternalGateway._build_answer_from_evidence(
            {"cc_entry": {"cc_score": 0.87}},
        )
        assert text == "Ya tengo esto identificado por evidencia previa (coherencia=0.87)."

    def test_empty_external_evidence_items_does_not_add_provenance(self):
        """`external_evidence` presente pero sin `items` (o vacío) no debe
        agregar ningún texto de provenance — fail-safe defensivo."""
        from core.operator.external_gateway import ExternalGateway
        evidence = {
            "cc_entry": {"cc_score": 0.87},
            "external_evidence": {"count": 0, "items": []},
        }
        text = ExternalGateway._build_answer_from_evidence(evidence)
        assert text == "Ya tengo esto identificado por evidencia previa (coherencia=0.87)."

    def test_external_evidence_english_provenance_label(self):
        from core.operator.external_gateway import ExternalGateway
        evidence = {
            "external_evidence": {
                "count": 1,
                "items": [{
                    "source": "tavily",
                    "source_reference": "https://example.com/page",
                    "observed_at": "",
                }],
            },
        }
        text = ExternalGateway._build_answer_from_evidence(evidence, lang="en")
        assert "prior source" in text
        assert "tavily" in text


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


# ---------------------------------------------------------------------------
# Victoria C — _phase_memory() external evidence lookup bridge
# ---------------------------------------------------------------------------

class TestPhaseMemoryExternalEvidenceLookup:
    """`_phase_memory()` debe incorporar evidencia externa ya almacenada
    (RESOLVE_ONLINE/PLACES/MARKET/ROUTE_COGNITIVE de un mensaje previo con
    el MISMO fingerprint) en `record.memory_evidence['external_evidence']`
    de forma PURAMENTE INFORMATIVA — nunca madurativa.

    Corrección post-review de PR #120: la sola existencia de evidencia
    cacheada NO puede fabricar novedad, patrones, conexiones ni maduración.
    Esas señales siguen perteneciendo exclusivamente a Gravity/CCTracker/
    Memory Engine (Victoria A). El gate de `ANSWER_FROM_EVIDENCE` en
    `_build_nucleus_decision()`, `_HIGH_COHERENCE_THRESHOLD` y
    `_compute_cc_observation_score()` permanecen intocados.
    """

    @staticmethod
    def _isolated_engine() -> TotalConvergenceEngine:
        """`TotalConvergenceEngine()` usa singletons LAZY reales (Gravity,
        Memory Engine, hypothesis engine, rules store, perception) que en
        este entorno apuntan a datos REALES de producción — no aislados por
        test. Para probar el bloque de evidencia externa de forma pura (sin
        contaminación de patrones/gravedad reales que ya existan para el
        texto de prueba), se fuerzan esos getters a `None` — exactamente el
        mismo camino ya defensivo que el motor usa cuando esos módulos no
        están disponibles (`except ImportError: return None`)."""
        engine = TotalConvergenceEngine()
        engine._get_perception = lambda: None
        engine._get_gravity = lambda: None
        engine._get_memory = lambda: None
        engine._get_hypothesis_engine = lambda: None
        engine._get_rules_store = lambda: None
        return engine

    def test_stored_evidence_does_not_flip_is_novel(self):
        """Una sola evidencia externa almacenada NO cambia `is_novel` —
        `ConvergenceRecord` por defecto nace `is_novel=True`, y debe seguir
        así tras el lookup si ningún otro mecanismo (Gravity/CC/Memory) lo
        cambió."""
        from core.self_observation import observation_ledger as ol
        ol.init_ledger()
        fp = "phase-memory-fp-novelty"
        ol.record_evidence(fp, "online", "respuesta previa", source="ddg")

        engine = self._isolated_engine()
        record = ConvergenceRecord(input_fingerprint=fp, intent="unknown")
        assert record.is_novel is True  # baseline antes del ciclo
        with patch("core.learn.constitution.get_cc_tracker", side_effect=ImportError):
            result = engine._phase_memory(record, "cualquier contenido")

        assert "external_evidence" in result.memory_evidence
        assert result.is_novel is True, (
            "La evidencia externa cacheada NO debe fabricar novedad — "
            "is_novel solo lo cambian Gravity/CCTracker/Memory Engine"
        )

    def test_stored_evidence_does_not_increment_prior_patterns_found(self):
        from core.self_observation import observation_ledger as ol
        ol.init_ledger()
        fp = "phase-memory-fp-patterns"
        ol.record_evidence(fp, "online", "respuesta previa", source="ddg")

        engine = self._isolated_engine()
        record = ConvergenceRecord(input_fingerprint=fp, intent="unknown")
        with patch("core.learn.constitution.get_cc_tracker", side_effect=ImportError):
            result = engine._phase_memory(record, "cualquier contenido")

        assert "external_evidence" in result.memory_evidence
        assert result.prior_patterns_found == 0, (
            "La evidencia externa cacheada NO debe incrementar "
            "prior_patterns_found — esa señal es exclusiva de Gravity/Memory Engine"
        )

    def test_stored_evidence_does_not_increment_memory_connections(self):
        from core.self_observation import observation_ledger as ol
        ol.init_ledger()
        fp = "phase-memory-fp-connections"
        ol.record_evidence(fp, "online", "respuesta previa", source="ddg")

        engine = self._isolated_engine()
        record = ConvergenceRecord(input_fingerprint=fp, intent="unknown")
        with patch("core.learn.constitution.get_cc_tracker", side_effect=ImportError):
            result = engine._phase_memory(record, "cualquier contenido")

        assert "external_evidence" in result.memory_evidence
        assert result.memory_connections == 0, (
            "La evidencia externa cacheada NO debe incrementar "
            "memory_connections — esa señal es exclusiva de los mecanismos "
            "ya existentes (immediate/gravity/cc/structural_memory/hyp/rules)"
        )

    def test_stored_evidence_appears_complete_in_memory_evidence(self):
        """La evidencia recuperada debe entrar COMPLETA (no colapsada a
        conteos) en `memory_evidence['external_evidence']['items']`: content,
        source_type, source, source_reference, observed_at, correlation_id,
        confidence."""
        from core.self_observation import observation_ledger as ol
        ol.init_ledger()
        fp = "phase-memory-fp-complete"
        ol.record_evidence(
            fp, "online", "La capital de Botsuana es Gaborone.",
            correlation_id="corr-xyz", source="tavily",
            source_reference="https://es.wikipedia.org/wiki/Gaborone",
            query="capital de Botsuana", confidence=0.9,
        )

        engine = self._isolated_engine()
        record = ConvergenceRecord(input_fingerprint=fp, intent="unknown")
        with patch("core.learn.constitution.get_cc_tracker", side_effect=ImportError):
            result = engine._phase_memory(record, "cualquier contenido")

        ext = result.memory_evidence["external_evidence"]
        assert ext["count"] == 1
        item = ext["items"][0]
        assert item["content"] == "La capital de Botsuana es Gaborone."
        assert item["source_type"] == "online"
        assert item["source"] == "tavily"
        assert item["source_reference"] == "https://es.wikipedia.org/wiki/Gaborone"
        assert item["correlation_id"] == "corr-xyz"
        assert item["confidence"] == 0.9
        assert item["observed_at"]  # no vacío
        # Aislado de Gravity/CC/Memory reales: is_novel/prior_patterns/
        # connections siguen en su baseline, la evidencia es SOLO informativa.
        assert result.is_novel is True
        assert result.prior_patterns_found == 0
        assert result.memory_connections == 0

    def test_no_stored_evidence_leaves_memory_evidence_unaffected(self):
        engine = TotalConvergenceEngine()
        record = ConvergenceRecord(
            input_fingerprint="phase-memory-fp-nonexistent", intent="unknown",
        )
        result = engine._phase_memory(record, "contenido nuevo")
        assert "external_evidence" not in result.memory_evidence

    def test_lookup_failure_is_fail_safe(self):
        """Si observation_ledger falla al consultar, _phase_memory() no debe
        romperse — el resto del ciclo sigue funcionando sin evidencia
        externa (comportamiento equivalente a no tener evidencia)."""
        engine = TotalConvergenceEngine()
        record = ConvergenceRecord(input_fingerprint="fp-boom", intent="unknown")
        with patch(
            "core.self_observation.observation_ledger.get_evidence",
            side_effect=RuntimeError("boom"),
        ):
            result = engine._phase_memory(record, "contenido")
        assert "external_evidence" not in result.memory_evidence


# ---------------------------------------------------------------------------
# Victoria C — Victoria A's gate remains the ONLY mechanism that decides
# ANSWER_FROM_EVIDENCE, even when external_evidence is present.
# ---------------------------------------------------------------------------

class TestExternalEvidenceNeverBypassesVictoriaAGate:
    """`_build_nucleus_decision()` sigue usándo EXCLUSIVAMENTE
    `is_novel`/`prior_patterns_found`/`coherence_score` (poblados por
    Gravity/CCTracker/Memory Engine) para decidir `ANSWER_FROM_EVIDENCE`.
    La presencia de `external_evidence` en `memory_evidence` NUNCA
    sustituye ese gate por sí sola."""

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

    def test_external_evidence_alone_does_not_trigger_answer_from_evidence(self):
        """memory_evidence contiene SOLO 'external_evidence' (is_novel sigue
        True, prior_patterns_found sigue 0, coherence_score sigue 0.0 —
        exactamente lo que produce ahora _phase_memory()). El gate de
        Victoria A NO debe activarse: sigue exigiendo is_novel=False +
        prior_patterns_found>0 + coherence_score>=threshold."""
        record = self._record(
            memory_evidence={
                "external_evidence": {
                    "count": 1,
                    "items": [{
                        "content": "algo", "source_type": "online",
                        "source": "tavily", "source_reference": "https://x",
                        "observed_at": "2026-01-01T00:00:00+00:00",
                        "correlation_id": "c1", "confidence": None,
                    }],
                },
            },
        )
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy != Strategy.ANSWER_FROM_EVIDENCE

    def test_gate_only_fires_when_gravity_cc_memory_independently_qualify(self):
        """Con external_evidence presente PERO is_novel/prior_patterns/
        coherence YA satisfechos por los mecanismos reales (Gravity/CC/
        Memory) — el mismo criterio de siempre, sin cambios —
        ANSWER_FROM_EVIDENCE sí se activa, exactamente igual que si
        external_evidence no existiera."""
        record = self._record(
            is_novel=False,
            prior_patterns_found=3,
            coherence_score=_HIGH_COHERENCE_THRESHOLD + 0.05,
            memory_evidence={
                "cc_entry": {"cc_score": 0.9},
                "external_evidence": {"count": 1, "items": [{"content": "algo"}]},
            },
        )
        decision = TotalConvergenceEngine._build_nucleus_decision(record)
        assert decision.candidate_strategy == Strategy.ANSWER_FROM_EVIDENCE
        assert decision.evidence == record.memory_evidence


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
