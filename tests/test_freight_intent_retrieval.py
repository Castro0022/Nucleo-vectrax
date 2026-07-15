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


# ── Regression: the exact route_A incident ────────────────────────────────

_ROUTE_A_QUERY = (
    "\u00bfPor qu\u00e9 route_A y no route_B o route_C? Defiende esa decisi\u00f3n "
    "exclusivamente con la evidencia persistida en freight_logistics"
)


def test_detects_underscored_freight_tokens():
    """Antes evadían la compuerta: `\\b` no matchea tokens con guion bajo."""
    assert fi.detect_freight_intent("freight_logistics")
    assert fi.detect_freight_intent("patron de mayor masa en freight_logistics")
    assert fi.detect_freight_intent(_ROUTE_A_QUERY)
    # Negativos claros siguen siendo negativos
    assert not fi.detect_freight_intent("precio de BTC")
    assert not fi.detect_freight_intent("hola, \u00bfc\u00f3mo est\u00e1s?")


def test_abstains_on_unsupported_decisional_subjects():
    """Caso real: hay evidencia freight (event-type patterns) pero el query exige
    defender route_A/B/C, que NO existen en la evidencia. Debe abstenerse
    nombrando los sujetos y SIN reintroducir las afirmaciones fabricadas."""
    gi = [_grav("load_booking", 77, 0.9), _grav("delivery_complete", 89, 0.9)]
    ledger = [{
        "timestamp": "2026-07-14T20:26:00", "obs_type": "ingest_load_booking",
        "summary": "Load booked: New York->Chicago | carrier=EliteLogistics | rate=$510.45",
        "domain": "freight_logistics", "star_id": "freight_logistics:load_booking:z",
    }]
    priors = [_prior("empty_miles", 90.0, 49.7, 56), _prior("load_booking", 90.0, 42.5, 48)]
    summary = {"domain": "freight_logistics", "patterns": 2}

    ps = _patches(gi, ledger, priors, summary)
    for p in ps:
        p.start()
    try:
        resp = fi.resolve_freight_query(_ROUTE_A_QUERY)
    finally:
        for p in ps:
            p.stop()

    low = resp.lower()
    # Abstención explícita que nombra los sujetos NO soportados por evidencia
    assert "route_a" in low and "route_b" in low and "route_c" in low
    assert "me abstengo" in low or "no tengo evidencia" in low
    assert "freight_logistics" in resp
    # NO reintroduce ninguna de las afirmaciones fabricadas del incidente
    for claim in ("repeticiones exitosas", "menor variabilidad",
                  "m\u00e1s consistentes", "convergencias m\u00e1s"):
        assert claim not in low
    # No es el bloque grounded: se abstuvo (no lleva las etiquetas de
    # procedencia por-sección que solo aparecen en la respuesta grounded)
    assert "gravity_index.by_domain" not in resp
    assert "domain_library.get_domain_priors" not in resp


def test_grounds_when_subject_is_supported():
    """Si el sujeto referido SÍ está en la evidencia recuperada, responde
    grounded (no abstención)."""
    gi = [_grav("load_booking", 77, 0.9)]
    ledger = [{
        "timestamp": "2026-07-14T20:26:00", "obs_type": "ingest_load_booking",
        "summary": "Load booked", "domain": "freight_logistics", "star_id": "z",
    }]
    priors = [_prior("load_booking", 90.0, 42.5, 48)]
    summary = {"domain": "freight_logistics", "patterns": 1}
    q = "\u00bfpor qu\u00e9 load_booking tiene tanta masa en freight_logistics?"

    ps = _patches(gi, ledger, priors, summary)
    for p in ps:
        p.start()
    try:
        resp = fi.resolve_freight_query(q)
    finally:
        for p in ps:
            p.stop()

    assert "load_booking" in resp
    assert "evidencia observada" in resp.lower()   # grounded, no abstención
    assert "me abstengo" not in resp.lower()
