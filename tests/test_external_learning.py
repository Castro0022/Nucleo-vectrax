"""
Tests — core.operator.external_learning (Fase 4: lazo de aprendizaje externo).

Herméticos: parchean observation_ledger y el learning pipeline. Verifican:
  - is_external_route reconoce rutas online y descarta las internas
  - ingest registra la observación con procedencia source=external
  - ingest alimenta el learning_cycle con InputEvent source='external'
  - no-op si no hay respuesta externa real
  - nunca lanza aunque las dependencias fallen (no rompe la respuesta)

Run:  .venv/bin/python -m pytest tests/test_external_learning.py -v
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.operator import external_learning as XL


# ── is_external_route ───────────────────────────────────────────────────

def test_is_external_route_true_for_online_variants():
    for r in ("online", "local_to_online", "places_to_online"):
        assert XL.is_external_route(r), r


def test_is_external_route_false_for_internal():
    for r in ("memory", "self_aware", "criterion:deterministic", "freight",
              "market", "places", "llm", "", None):
        assert not XL.is_external_route(r), r


# ── ingest_external_result ──────────────────────────────────────────────

def test_ingest_records_observation_with_external_provenance():
    rec = MagicMock()
    with patch("core.self_observation.observation_ledger.record", rec), \
         patch("core.learning_cycle.pipeline.get_learning_pipeline") as gp, \
         patch("core.learn.criterion.detect_domain", return_value="market"):
        ok = XL.ingest_external_result(
            query="¿precio de nvidia?", answer="Ronda los 120 USD.",
            route="online", confidence=0.42, engines=["ddg", "brave"],
        )
    assert ok is True
    assert rec.called
    kwargs = rec.call_args.kwargs
    assert kwargs["domain"] == "market"
    assert kwargs["obs_type"] == "external_answer"
    assert kwargs["evidence"]["source"] == "external"
    assert kwargs["evidence"]["route"] == "online"
    assert kwargs["evidence"]["engines"] == ["ddg", "brave"]
    # También alimentó el learning pipeline
    assert gp.called


def test_ingest_feeds_learning_cycle_tagged_external():
    captured = {}

    class _FakePipeline:
        def process_event(self, event):
            captured["source"] = event.source
            captured["intent"] = event.intent

    with patch("core.self_observation.observation_ledger.record"), \
         patch("core.learning_cycle.pipeline.get_learning_pipeline",
               return_value=_FakePipeline()), \
         patch("core.learn.criterion.detect_domain", return_value="external"):
        XL.ingest_external_result(
            query="algo", answer="respuesta externa", route="local_to_online",
        )
    assert captured.get("source") == "external"
    assert captured.get("intent") == "external_knowledge"


def test_ingest_noop_on_empty_answer():
    rec = MagicMock()
    with patch("core.self_observation.observation_ledger.record", rec):
        assert XL.ingest_external_result("q", "", "online") is False
        assert XL.ingest_external_result("q", "   ", "online") is False
    assert not rec.called


def test_ingest_never_raises_when_dependencies_fail():
    # Ambas dependencias lanzan → la función debe tragarse los errores.
    with patch("core.self_observation.observation_ledger.record",
               side_effect=RuntimeError("boom")), \
         patch("core.learning_cycle.pipeline.get_learning_pipeline",
               side_effect=RuntimeError("boom")), \
         patch("core.learn.criterion.detect_domain",
               side_effect=RuntimeError("boom")):
        # No debe propagar ninguna excepción.
        result = XL.ingest_external_result("q", "a", "online")
    assert result is False
