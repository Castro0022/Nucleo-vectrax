"""
Tests — Motor de Criterio Aprendido (core/learn/criterion.py).

Cubre: detección de pedido de opinión, detección de dominio, ranking por
métricas reales, opinión grounded que expresa preferencia (no fija), abstención
constructiva ante entidades ausentes (caso route_A), y el verificador +
fallback determinista cuando el LLM no está grounded.

Run:  python -m pytest tests/test_criterion.py -v
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.learn import criterion as C


def _prior(pt, wr, e, n, conf="HIGH"):
    return types.SimpleNamespace(
        pattern_type=pt, win_rate=wr, expectancy=e, sample_size=n,
        confidence=conf, contributing_tenants=2,
    )


def _grav(intent, hits, cc=0.9, tier="HOT"):
    return types.SimpleNamespace(
        intent=intent, hits=hits, cc_score=cc, tier=tier, fingerprint="fp", summary="",
    )


class _FakeGI:
    def __init__(self, by_domain_map=None, domain_stats=None):
        self._bd = by_domain_map or {}
        self._ds = domain_stats or {}

    def by_domain(self, d):
        return self._bd.get(d, [])

    def domain_stats(self):
        return self._ds


def _patch_stores(priors_map, domains, gi):
    return [
        patch("core.domain_knowledge.list_domains", return_value=domains),
        patch("core.domain_knowledge.get_domain_priors",
              side_effect=lambda d: priors_map.get(d, [])),
        patch("core.domain_knowledge.get_domain_summary",
              side_effect=lambda d: {"domain": d, "total_observations": 1000}),
        patch("core.learn.gravity_engine.get_gravity_index", return_value=gi),
    ]


# ── 1. Detección de pedido de opinión/criterio ────────────────────────────

def test_detect_criterion_request():
    assert C.detect_criterion_request("¿qué opinas de la bolsa?")
    assert C.detect_criterion_request("según lo que aprendiste, ¿qué es mejor?")
    assert C.detect_criterion_request("compara los patrones de logística")
    assert C.detect_criterion_request("¿cuál preferirías?")
    assert not C.detect_criterion_request("hola, ¿cómo estás?")
    assert not C.detect_criterion_request("guarda esta nota")


# ── 2. Detección de dominio ───────────────────────────────────────────────

def test_detect_domain():
    gi = _FakeGI(domain_stats={"market": {}, "freight_logistics": {}})
    with patch("core.domain_knowledge.list_domains",
               return_value=["market", "freight_logistics"]), \
         patch("core.learn.gravity_engine.get_gravity_index", return_value=gi):
        assert C.detect_domain("¿qué opinas de la bolsa y las acciones?") == "market"
        assert C.detect_domain("¿cuál es la mejor carga en logística?") == "freight_logistics"
        assert C.detect_domain("háblame del clima") is None


# ── 3. Ranking por métricas reales ────────────────────────────────────────

def test_rank_orders_by_metrics():
    priors = {"market": [
        _prior("AAPL", 100.0, 16.0, 221, "HIGH"),
        _prior("WEAKONE", 60.0, 1.0, 20, "LOW"),
    ]}
    gi = _FakeGI(by_domain_map={"market": []}, domain_stats={"market": {}})
    ps = _patch_stores(priors, ["market"], gi)
    for p in ps:
        p.start()
    try:
        ranked = C.rank_domain_evidence("market")
    finally:
        for p in ps:
            p.stop()
    assert ranked[0]["name"] == "AAPL"          # mayor score por E×LB×conf
    assert ranked[0]["score"] > ranked[1]["score"]


# ── 4. Opinión grounded que expresa preferencia (no fija) ─────────────────

def test_build_criterion_expresses_preference():
    priors = {"market": [
        _prior("AAPL", 100.0, 16.0, 221, "HIGH"),
        _prior("TSLA", 90.0, 8.0, 100, "HIGH"),
    ]}
    gi = _FakeGI(by_domain_map={"market": [_grav("AAPL", 69)]},
                 domain_stats={"market": {}})
    ps = _patch_stores(priors, ["market"], gi)
    for p in ps:
        p.start()
    try:
        # sin LLM inyectado y sin bridge listo → texto determinista grounded
        resp = C.build_criterion("market", "¿qué opinas de la bolsa?")
    finally:
        for p in ps:
            p.stop()
    low = resp.lower()
    assert "aapl" in low                        # ancla la posición en el patrón real
    assert "emerge" in low                       # posición emergente (no "me inclino"/"elijo")
    assert ("expectancy" in low or "wr" in low)          # cita métrica real
    assert "no es una regla" in low              # ni regla preprogramada ni elección de menú


# ── 5. Abstención constructiva ante entidades ausentes (caso route_A) ─────

def test_build_criterion_route_a_constructive():
    priors = {"freight_logistics": [
        _prior("empty_miles", 90.0, 49.7, 56, "HIGH"),
        _prior("load_booking", 90.0, 42.5, 48, "HIGH"),
    ]}
    gi = _FakeGI(by_domain_map={"freight_logistics": []},
                 domain_stats={"freight_logistics": {}})
    q = ("¿Por qué route_A y no route_B o route_C? Defiende esa decisión "
         "exclusivamente con la evidencia persistida en freight_logistics")
    ps = _patch_stores(priors, ["freight_logistics"], gi)
    for p in ps:
        p.start()
    try:
        resp = C.build_criterion("freight_logistics", q)
    finally:
        for p in ps:
            p.stop()
    low = resp.lower()
    # Reconoce que route_A/B/C no existen…
    assert "route_a" in low and "route_b" in low and "route_c" in low
    assert "no tengo" in low
    assert "no opino" not in low                 # ya NO se calla: siempre da posición
    # …y AUN ASÍ da su posición emergente sobre lo observado
    assert "empty_miles" in low
    assert "emerge" in low
    # sin reintroducir las afirmaciones fabricadas del incidente
    for claim in ("repeticiones exitosas", "menor variabilidad", "más consistentes"):
        assert claim not in low


# ── 6. Verificador de grounding ───────────────────────────────────────────

def test_verify_grounded():
    ranked = [{"name": "load_booking", "win_rate": 90, "wilson_lb": 80}]
    dom = "freight_logistics"
    assert C._verify_grounded("Prefiero load_booking con WR 90%.", ranked, dom)
    # entidad no soportada
    assert not C._verify_grounded("Creo que route_x es mejor.", ranked, dom)
    # porcentaje que no coincide con la evidencia
    assert not C._verify_grounded("load_booking tiene WR 50%.", ranked, dom)


# ── 7. LLM no-grounded → fallback determinista ────────────────────────────

def test_build_criterion_falls_back_when_llm_ungrounded():
    priors = {"market": [_prior("AAPL", 100.0, 16.0, 221, "HIGH")]}
    gi = _FakeGI(by_domain_map={"market": []}, domain_stats={"market": {}})
    ps = _patch_stores(priors, ["market"], gi)
    for p in ps:
        p.start()
    try:
        # LLM inventa una entidad ausente → verificador rechaza → determinista
        resp = C.build_criterion(
            "market", "¿qué opinas?",
            llm=lambda prompt: "Creo que route_x es mejor por sus convergencias.",
        )
    finally:
        for p in ps:
            p.stop()
    assert "route_x" not in resp.lower()        # no se propaga la fabricación
    assert "aapl" in resp.lower()               # cae al criterio determinista real


# ── 8. LLM grounded → se usa su fraseo ────────────────────────────────────

def test_build_criterion_uses_grounded_llm():
    priors = {"market": [_prior("AAPL", 100.0, 16.0, 221, "HIGH")]}
    gi = _FakeGI(by_domain_map={"market": []}, domain_stats={"market": {}})
    ps = _patch_stores(priors, ["market"], gi)
    for p in ps:
        p.start()
    try:
        resp = C.build_criterion(
            "market", "¿qué opinas de la bolsa?",
            llm=lambda prompt: "Por lo aprendido, prefiero AAPL (WR 100%).",
        )
    finally:
        for p in ps:
            p.stop()
    assert "prefiero AAPL (WR 100%)" in resp


# ── 9. Tema concreto: extracción y criterio centrado en el tema ───────────

def test_extract_topic_tokens():
    assert "nvda" in C.extract_topic_tokens("¿qué opinas de NVDA?")
    # indicador de dominio se descarta (no es el tema concreto)
    assert "bolsa" not in C.extract_topic_tokens("¿qué opinas de la bolsa?")
    # sinónimo ES→esquema (carga → load/booking)
    assert "load" in C.extract_topic_tokens("¿cuál es la mejor carga?")
    assert "route_a" in C.extract_topic_tokens("por qué route_A")


def test_build_criterion_topic_scoped():
    # AAPL es el top global por score, pero la pregunta es sobre NVDA → el
    # criterio debe CENTRARSE en NVDA (experiencia relacionada al tema),
    # no arrastrar el top global del dominio.
    priors = {"market": [
        _prior("AAPL", 100.0, 16.0, 221, "HIGH"),
        _prior("NVDA", 90.0, 5.0, 60, "HIGH"),
    ]}
    gi = _FakeGI(by_domain_map={"market": []}, domain_stats={"market": {}})
    ps = _patch_stores(priors, ["market"], gi)
    for p in ps:
        p.start()
    try:
        resp = C.build_criterion("market", "¿qué opinas de NVDA?")
    finally:
        for p in ps:
            p.stop()
    low = resp.lower()
    assert "«nvda»" in low                   # posición centrada en el tema
    assert "emerge" in low                   # posición emergente
    assert "aapl" not in low                # no arrastra el top global


def test_build_criterion_always_opines_low_data():
    # Con MUY poca evidencia (1 patrón, N bajo, confianza LOW) igual da su
    # posición — no se abstiene por "datos insuficientes".
    priors = {"market": [_prior("AAPL", 60.0, 2.0, 3, "LOW")]}
    gi = _FakeGI(by_domain_map={"market": []}, domain_stats={"market": {}})
    ps = _patch_stores(priors, ["market"], gi)
    for p in ps:
        p.start()
    try:
        resp = C.build_criterion("market", "¿qué opinas?")
    finally:
        for p in ps:
            p.stop()
    low = resp.lower()
    assert "aapl" in low                         # opina con lo que tiene
    assert "emerge" in low                        # como posición emergente
    assert "todavía no tengo datos" not in low    # NO se abstiene
