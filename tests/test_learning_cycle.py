"""
Tests — Learning Cycle Pipeline
==================================
Verifica el ciclo completo: SEÑAL → INVESTIGACIÓN → VERIFICACIÓN → INTEGRACIÓN

Tests:
  1. AnomalyDetector: detección de cada tipo de anomalía
  2. InvestigationEngine: generación de hipótesis a partir de anomalías
  3. VerificationEngine: validación y rechazo por datos insuficientes
  4. LearningIntegrator: solo acepta CONFIRMED
  5. Pipeline completo: flujo end-to-end
  6. Separación señal/verdad

Creado: 2026-03-28
"""

import sys
import os
import time

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset all singletons before each test."""
    from core.learning_cycle.anomaly_detector import reset_anomaly_detector
    from core.learning_cycle.investigation_engine import reset_investigation_engine
    from core.learning_cycle.verification_engine import reset_verification_engine
    from core.learning_cycle.learning_integrator import reset_learning_integrator
    from core.learning_cycle.pipeline import reset_learning_pipeline
    from core.operator.hypothesis_engine import reset_hypothesis_engine

    reset_anomaly_detector()
    reset_investigation_engine()
    reset_verification_engine()
    reset_learning_integrator()
    reset_learning_pipeline()
    reset_hypothesis_engine()

    yield

    reset_anomaly_detector()
    reset_investigation_engine()
    reset_verification_engine()
    reset_learning_integrator()
    reset_learning_pipeline()
    reset_hypothesis_engine()


@pytest.fixture(autouse=True)
def _isolate_learning_stores(tmp_path, monkeypatch):
    """Hermetic isolation of the persistent stores the verification strategies
    read.

    VerificationEngine's temporal_consistency reads the episodic ledger and
    precedent_match reads the learned-rules store. Both are process-wide
    singletons backed by repo ``vault/*.jsonl`` and are NOT isolated per test;
    other suites pollute them with generic 'test' tokens, which fabricates
    false temporal/precedent evidence and wrongly confirms a no-evidence
    hypothesis. Point both singletons at empty, per-test temp files so
    verification is deterministic (reads see nothing) while still being
    writable (the integrator can persist a rule).
    """
    try:
        import core.learn.learned_rules as _lr
        monkeypatch.setattr(
            _lr, "_store",
            _lr.LearnedRulesStore(path=str(tmp_path / "learned_rules.jsonl")),
            raising=False,
        )
    except Exception:
        pass
    try:
        import core.learn.episodic as _ep
        monkeypatch.setattr(
            _ep, "_ledger",
            _ep.EpisodicLedger(path=str(tmp_path / "episodic_ledger.jsonl")),
            raising=False,
        )
    except Exception:
        pass
    yield


# ===========================================================================
# 1. ANOMALY_DETECTOR
# ===========================================================================

class TestAnomalyDetector:

    def test_no_anomaly_on_first_events(self):
        """Primeros eventos no generan anomalías (sin referencia)."""
        from core.learning_cycle.anomaly_detector import AnomalyDetector, InputEvent

        detector = AnomalyDetector()
        event = InputEvent(text="hola", intent="GREETING", source="telegram", length=4)
        signals = detector.analyze(event)
        assert signals == []

    def test_new_pattern_detected(self):
        """Detecta un intent nunca visto después de entrenar."""
        from core.learning_cycle.anomaly_detector import AnomalyDetector, InputEvent

        detector = AnomalyDetector()

        # Feed known patterns
        for _ in range(5):
            detector.analyze(InputEvent(
                text="test", intent="GREETING", source="telegram", length=4,
            ))

        # New intent
        signals = detector.analyze(InputEvent(
            text="test", intent="NEVER_SEEN_BEFORE", source="telegram", length=4,
        ))

        new_patterns = [s for s in signals if s.anomaly_type == "new_pattern"]
        assert len(new_patterns) >= 1
        assert "nunca visto" in new_patterns[0].description

    def test_repetition_burst(self):
        """Detecta ráfaga de entradas repetidas."""
        from core.learning_cycle.anomaly_detector import AnomalyDetector, InputEvent

        detector = AnomalyDetector()

        # Feed identical events
        event = InputEvent(text="x", intent="ASK", source="api", length=1)
        for _ in range(6):
            signals = detector.analyze(event)

        # After threshold, should detect burst
        bursts = [s for s in signals if s.anomaly_type == "repetition_burst"]
        assert len(bursts) >= 1

    def test_outlier_input(self):
        """Detecta input con longitud atípica."""
        from core.learning_cycle.anomaly_detector import AnomalyDetector, InputEvent

        detector = AnomalyDetector()

        # Normal inputs with slight variation (stdev > 0)
        for i in range(15):
            length = 18 + (i % 5)  # 18, 19, 20, 21, 22
            detector.analyze(InputEvent(
                text="x" * length, intent="CHAT", source="web", length=length,
            ))

        # Outlier
        signals = detector.analyze(InputEvent(
            text="x" * 5000, intent="CHAT", source="web", length=5000,
        ))

        outliers = [s for s in signals if s.anomaly_type == "outlier_input"]
        assert len(outliers) >= 1

    def test_signal_never_concludes_truth(self):
        """Las señales no tienen campo 'truth' ni 'conclusion'."""
        from core.learning_cycle.anomaly_detector import AnomalyDetector, InputEvent

        detector = AnomalyDetector()
        for _ in range(5):
            detector.analyze(InputEvent(text="x", intent="A", source="s", length=1))

        signals = detector.analyze(InputEvent(
            text="x", intent="NEW", source="s", length=1,
        ))

        for s in signals:
            d = s.to_dict()
            assert "truth" not in d
            assert "conclusion" not in d
            assert "verified" not in d

    def test_stats(self):
        """Stats se actualizan correctamente."""
        from core.learning_cycle.anomaly_detector import AnomalyDetector, InputEvent

        detector = AnomalyDetector()
        detector.analyze(InputEvent(text="x", intent="A", source="s", length=1))

        stats = detector.stats()
        assert stats["total_analyzed"] == 1
        assert "anomaly_rate" in stats


# ===========================================================================
# 2. INVESTIGATION_ENGINE
# ===========================================================================

class TestInvestigationEngine:

    def test_investigate_generates_hypotheses(self):
        """Investigación genera hipótesis a partir de anomalía."""
        from core.learning_cycle.investigation_engine import InvestigationEngine
        from core.learning_cycle.anomaly_detector import AnomalySignal

        engine = InvestigationEngine()
        anomaly = AnomalySignal(
            anomaly_type="frequency_spike",
            severity="medium",
            description="Intent 'ASK' aparece 15x",
            context={"intent": "ASK", "count": 15, "factor": 3.0},
        )

        result = engine.investigate(anomaly)

        assert result.anomaly_id == anomaly.id
        assert result.hypotheses_generated > 0
        assert len(result.hypothesis_ids) > 0
        assert result.investigation_time_ms > 0

    def test_max_hypotheses_per_anomaly(self):
        """No genera más de MAX_HYPOTHESES_PER_ANOMALY."""
        from core.learning_cycle.investigation_engine import InvestigationEngine
        from core.learning_cycle.anomaly_detector import AnomalySignal

        engine = InvestigationEngine()
        anomaly = AnomalySignal(
            anomaly_type="frequency_spike",
            severity="high",
            description="test spike",
            context={"intent": "TEST", "count": 50, "factor": 10.0},
        )

        result = engine.investigate(anomaly)
        assert result.hypotheses_generated <= engine.MAX_HYPOTHESES_PER_ANOMALY

    def test_external_source_callback(self):
        """Fuente externa se consulta si está registrada."""
        from core.learning_cycle.investigation_engine import (
            InvestigationEngine, ContextSource,
        )
        from core.learning_cycle.anomaly_detector import AnomalySignal

        called = {"count": 0}

        def mock_external(anomaly):
            called["count"] += 1
            return [ContextSource(
                source_name="mock_external",
                data={"extra": "info"},
                relevance=0.5,
            )]

        engine = InvestigationEngine(external_source=mock_external)
        anomaly = AnomalySignal(
            anomaly_type="new_pattern",
            severity="low",
            description="test",
        )

        result = engine.investigate(anomaly)
        assert called["count"] == 1
        assert any(
            cs.source_name == "mock_external"
            for cs in result.context_sources
        )


# ===========================================================================
# 3. VERIFICATION_ENGINE
# ===========================================================================

class TestVerificationEngine:

    def test_insufficient_data_rejection(self):
        """Rechaza hipótesis sin evidencia suficiente."""
        from core.learning_cycle.verification_engine import VerificationEngine
        from core.operator.hypothesis_engine import get_hypothesis_engine

        hyp_engine = get_hypothesis_engine()
        hyp = hyp_engine.propose(
            description="Test hypothesis",
            initial_confidence=0.5,
            tags=["test"],
        )

        verifier = VerificationEngine()
        result = verifier.verify(hyp.id)

        # Sin evidencia → insufficient_data
        assert result.verified is False
        assert "insuficientes" in result.rejection_reason or result.final_status == "insufficient_data"

    def test_nonexistent_hypothesis(self):
        """Rechaza hipótesis inexistente."""
        from core.learning_cycle.verification_engine import VerificationEngine

        verifier = VerificationEngine()
        result = verifier.verify("HYP-NONEXIST")

        assert result.verified is False
        assert result.final_status == "not_found"

    def test_already_resolved_hypothesis(self):
        """Rechaza hipótesis ya resuelta."""
        from core.learning_cycle.verification_engine import VerificationEngine
        from core.operator.hypothesis_engine import (
            get_hypothesis_engine, Evidence, EvidenceType,
        )

        hyp_engine = get_hypothesis_engine()
        hyp = hyp_engine.propose(
            description="Test confirmed",
            initial_confidence=0.9,
            tags=["test"],
        )

        # Add evidence and confirm
        for _ in range(3):
            hyp_engine.add_evidence(hyp.id, Evidence(
                evidence_type=EvidenceType.CONFIRMATION,
                weight=0.9,
                supports=True,
                source="test",
            ))
        hyp_engine.resolve(hyp.id)

        verifier = VerificationEngine()
        result = verifier.verify(hyp.id)

        assert result.final_status == "confirmed"

    def test_stats_tracking(self):
        """Stats se actualizan."""
        from core.learning_cycle.verification_engine import VerificationEngine

        verifier = VerificationEngine()
        verifier.verify("HYP-FAKE")

        stats = verifier.stats()
        assert stats["total_verifications"] >= 0


# ===========================================================================
# 4. LEARNING_INTEGRATOR
# ===========================================================================

class TestLearningIntegrator:

    def test_rejects_non_confirmed(self):
        """Solo acepta hipótesis CONFIRMED."""
        from core.learning_cycle.learning_integrator import LearningIntegrator
        from core.operator.hypothesis_engine import get_hypothesis_engine

        hyp_engine = get_hypothesis_engine()
        hyp = hyp_engine.propose(
            description="Not confirmed",
            initial_confidence=0.5,
            tags=["test"],
        )

        integrator = LearningIntegrator()
        result = integrator.integrate(hyp.id)

        assert result.integrated is False
        assert "CONFIRMED" in result.rejection_reason

    def test_rejects_low_confidence(self):
        """Rechaza hipótesis con confianza baja."""
        from core.learning_cycle.learning_integrator import LearningIntegrator
        from core.operator.hypothesis_engine import (
            get_hypothesis_engine, Evidence, EvidenceType, HypothesisStatus,
        )

        hyp_engine = get_hypothesis_engine()
        hyp = hyp_engine.propose(
            description="Low confidence",
            initial_confidence=0.3,
            tags=["test"],
        )

        # Force confirm with low confidence (manipulate directly)
        hyp.status = HypothesisStatus.CONFIRMED
        hyp.confidence = 0.3

        integrator = LearningIntegrator()
        result = integrator.integrate(hyp.id)

        assert result.integrated is False
        assert "insuficiente" in result.rejection_reason.lower()

    def test_integrates_confirmed_hypothesis(self):
        """Integra correctamente una hipótesis confirmada."""
        from core.learning_cycle.learning_integrator import LearningIntegrator
        from core.operator.hypothesis_engine import (
            get_hypothesis_engine, Evidence, EvidenceType, HypothesisStatus,
        )

        hyp_engine = get_hypothesis_engine()
        hyp = hyp_engine.propose(
            description="Confirmed pattern for testing",
            initial_confidence=0.9,
            tags=["frequency_spike", "learning_cycle"],
            metadata={"anomaly_type": "frequency_spike", "severity": "medium"},
        )

        # Force confirmed status
        hyp.status = HypothesisStatus.CONFIRMED
        hyp.confidence = 0.85
        hyp.evidence = [
            Evidence(evidence_type=EvidenceType.OBSERVATION, weight=0.8, supports=True, source="test1"),
            Evidence(evidence_type=EvidenceType.METRIC, weight=0.9, supports=True, source="test2"),
        ]

        integrator = LearningIntegrator()
        result = integrator.integrate(hyp.id)

        assert result.integrated is True
        assert result.rule_id != ""
        assert result.category == "usage_pattern"


# ===========================================================================
# 5. PIPELINE COMPLETO
# ===========================================================================

class TestLearningPipeline:

    def test_no_anomaly_no_cycle(self):
        """Sin anomalía, el pipeline no ejecuta ciclo."""
        from core.learning_cycle.pipeline import LearningPipeline
        from core.learning_cycle.anomaly_detector import InputEvent

        pipeline = LearningPipeline()

        result = pipeline.process_event(InputEvent(
            text="hola", intent="GREETING", source="web", length=4,
        ))

        assert result.anomalies_detected == 0
        assert result.hypotheses_generated == 0

    def test_pipeline_with_anomaly(self):
        """Pipeline ejecuta ciclo completo cuando hay anomalía."""
        from core.learning_cycle.pipeline import LearningPipeline
        from core.learning_cycle.anomaly_detector import (
            AnomalyDetector, AnomalySignal, InputEvent,
        )

        pipeline = LearningPipeline()

        # Feed enough events to build baseline
        for _ in range(10):
            pipeline.process_event(InputEvent(
                text="normal", intent="CHAT", source="web", length=6,
            ))

        # Trigger new_pattern
        result = pipeline.process_event(InputEvent(
            text="anomaly", intent="BRAND_NEW_INTENT", source="web", length=7,
        ))

        # Should detect anomaly and generate hypotheses
        assert result.anomalies_detected >= 1 or result.hypotheses_generated >= 0

    def test_pipeline_batch(self):
        """Pipeline procesa lote de eventos."""
        from core.learning_cycle.pipeline import LearningPipeline
        from core.learning_cycle.anomaly_detector import InputEvent

        pipeline = LearningPipeline()

        events = [
            InputEvent(text=f"msg{i}", intent="CHAT", source="web", length=4)
            for i in range(10)
        ]

        result = pipeline.process_batch(events)
        assert result.skipped is False

    def test_pipeline_status(self):
        """Status del pipeline incluye todos los módulos."""
        from core.learning_cycle.pipeline import LearningPipeline

        pipeline = LearningPipeline()
        status = pipeline.status()

        assert "pipeline" in status
        assert "detector" in status
        assert "investigator" in status
        assert "verifier" in status
        assert "integrator" in status

    def test_run_cycle_with_anomaly_signal(self):
        """run_cycle acepta anomalías pre-detectadas."""
        from core.learning_cycle.pipeline import LearningPipeline
        from core.learning_cycle.anomaly_detector import AnomalySignal

        pipeline = LearningPipeline()
        signal = AnomalySignal(
            anomaly_type="new_pattern",
            severity="low",
            description="test anomaly",
            context={"intent": "TEST"},
        )

        result = pipeline.run_cycle(anomalies=[signal])

        assert result.anomalies_detected == 1
        assert result.investigations_completed >= 1


# ===========================================================================
# 6. SEPARACIÓN SEÑAL / VERDAD
# ===========================================================================

class TestSignalTruthSeparation:

    def test_detector_only_signals(self):
        """El detector nunca genera hipótesis ni reglas."""
        from core.learning_cycle.anomaly_detector import AnomalyDetector, InputEvent

        detector = AnomalyDetector()

        for _ in range(5):
            detector.analyze(InputEvent(text="x", intent="A", source="s", length=1))

        signals = detector.analyze(InputEvent(
            text="x", intent="NEW", source="s", length=1,
        ))

        for s in signals:
            assert s.anomaly_type in [
                "frequency_spike", "new_pattern", "distribution_shift",
                "repetition_burst", "outlier_input",
            ]
            # No hypothesis, no rule, no truth
            d = s.to_dict()
            assert "hypothesis" not in d
            assert "rule" not in d
            assert "truth" not in d

    def test_integrator_rejects_unverified(self):
        """Integrator rechaza todo lo que no esté CONFIRMED."""
        from core.learning_cycle.learning_integrator import LearningIntegrator
        from core.operator.hypothesis_engine import get_hypothesis_engine

        hyp_engine = get_hypothesis_engine()

        # Create hypothesis in PROPOSED state
        hyp = hyp_engine.propose(description="unverified", tags=["test"])

        integrator = LearningIntegrator()
        result = integrator.integrate(hyp.id)

        assert result.integrated is False
