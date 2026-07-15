"""
Tests for the Freight Logistics conversational retrieval path
(intents/freight_intents.py). Demonstrates the four required behaviors:

  1. Detection of a Freight query.
  2. Retrieval of freight_logistics evidence (with provenance + domain).
  3. Response grounded EXCLUSIVELY in that evidence.
  4. Explicit abstention when evidence is insufficient.

All external stores are mocked — these tests never touch real data and never
run the observation/ingestion/learning cycle.

Run:  python -m pytest tests/test_freight_intent_retrieval.py -v
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from intents import freight_intents as fi


# ── Fakes mirroring the real store return shapes ──────────────────────────

def _grav(intent, hits, cc, tier="HOT", fp="freight_logistics:load_booking:x",
          summary="load_booking origin=TX dest=CA"):
    return types.SimpleNamespace(
        domain="freight_logistics", intent=intent, fingerprint=fp,
        hits=hits, cc_score=cc, tier=tier, summary=summary,
    )


def _prior(pattern_type, wr, e, n, conf="MEDIUM", tenants=2):
    return types.SimpleNamespace(
        domain="freight_logistics", pattern_type=pattern_type, win_rate=wr,
        expectancy=e, sample_size=n, confidence=conf, contributing_tenants=tenants,
        conditions_signature="sig",
    )


class _FakeGI:
    def __init__(self, recs):
        self._recs = recs

    def by_domain(self, domain):
        return self._recs if domain == "freight_logistics" else []


def _patches(gi_recs, ledger, priors, summary):
    return [
        patch("core.learn.gravity_engine.get_gravity_index", return_value=_FakeGI(gi_recs)),
        patch("core.self_observation.observation_ledger.get_by_domain", return_value=ledger),
        patch("core.domain_knowledge.get_domain_priors", return_value=priors),
        patch("core.domain_knowledge.get_domain_summary", return_value=summary),
    ]


# ── 1) Detection ──────────────────────────────────────────────────────────

def test_detects_freight_query():
    assert fi.detect_freight_intent("¿cómo van las cargas y fletes hoy?")
    assert fi.detect_freight_intent("freight loads and lanes status")
    assert fi.detect_freight_intent("resumen de logística y entregas")
    # Non-freight queries must NOT trigger
    assert not fi.detect_freight_intent("hola, ¿cómo estás?")
    assert not fi.detect_freight_intent("precio de BTC")
    assert not fi.detect_freight_intent("quién soy")


# ── 2) Retrieval of freight_logistics evidence (provenance + domain) ──────

def test_retrieves_freight_evidence():
    gi = [_grav("load_booking", 34, 0.71)]
    ledger = [{
        "timestamp": "2026-07-14T12:00:00", "obs_type": "ingest_delivery_complete",
        "summary": "delivery_complete origin=TX dest=CA", "domain": "freight_logistics",
        "star_id": "freight_logistics:delivery_complete:z",
    }]
    priors = [_prior("fulfillment_event", 62.0, 0.12, 45)]
    summary = {"domain": "freight_logistics", "patterns": 1}

    ps = _patches(gi, ledger, priors, summary)
    for p in ps:
        p.start()
    try:
        ev = fi.retrieve_freight_evidence()
    finally:
        for p in ps:
            p.stop()

    assert ev["domain"] == "freight_logistics"
    assert ev["has_evidence"] is True
    assert ev["counts"] == {"gravity": 1, "ledger": 1, "priors": 1}
    # Provenance (source store) + domain preserved on each item
    assert ev["gravity"][0]["source"] == "gravity_index.by_domain"
    assert ev["gravity"][0]["domain"] == "freight_logistics"
    assert ev["ledger"][0]["source"] == "observation_ledger.get_by_domain"
    assert ev["ledger"][0]["domain"] == "freight_logistics"
    assert ev["priors"][0]["source"] == "domain_library.get_domain_priors"
    assert ev["priors"][0]["domain"] == "freight_logistics"
    assert ev["priors"][0]["pattern_type"] == "fulfillment_event"


# ── 3) Response grounded EXCLUSIVELY in retrieved evidence ────────────────

def test_response_grounded_only_in_evidence():
    gi = [_grav("load_booking", 34, 0.71, summary="UNIQUE_GRAV_MARKER")]
    ledger = [{
        "timestamp": "2026-07-14T12:00:00", "obs_type": "ingest_delivery_complete",
        "summary": "UNIQUE_LEDGER_MARKER", "domain": "freight_logistics", "star_id": "z",
    }]
    priors = [_prior("UNIQUE_PATTERN_MARKER", 62.0, 0.12, 45)]
    summary = {"domain": "freight_logistics", "patterns": 1}

    ps = _patches(gi, ledger, priors, summary)
    for p in ps:
        p.start()
    try:
        resp = fi.resolve_freight_query("estado de las cargas")
    finally:
        for p in ps:
            p.stop()

    # Every retrieved evidence item is surfaced (grounded in real evidence)
    assert "UNIQUE_PATTERN_MARKER" in resp
    assert "UNIQUE_LEDGER_MARKER" in resp
    assert "load_booking" in resp            # gravity intent
    # Domain + provenance preserved in the rendered response
    assert "freight_logistics" in resp
    assert "domain_library.get_domain_priors" in resp
    assert "gravity_index.by_domain" in resp
    assert "observation_ledger.get_by_domain" in resp


# ── 4) Explicit abstention when evidence is insufficient ──────────────────

def test_abstains_when_insufficient():
    ps = _patches([], [], [], {"domain": "freight_logistics", "patterns": 0})
    for p in ps:
        p.start()
    try:
        resp = fi.resolve_freight_query("cómo van las cargas")
    finally:
        for p in ps:
            p.stop()

    low = resp.lower()
    assert "freight_logistics" in resp
    assert "no tengo evidencia" in low and "suficiente" in low
    # Reports the real (zero) counts and fabricates nothing
    assert "0 stars" in resp
    assert "fulfillment_event" not in resp
    assert "UNIQUE_PATTERN_MARKER" not in resp
