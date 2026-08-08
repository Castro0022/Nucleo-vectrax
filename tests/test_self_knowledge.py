"""
Tests — core.self_observation.self_knowledge (autoconocimiento con procedencia).

Herméticos: parchean las fuentes (SQLite/census/gravity/convergence) para no
depender del estado real. Verifican:
  - detección de intención (origen / procedencia) sin falsos positivos con "quién soy"
  - get_origin elige el store DURABLE más antiguo; vacío si no hay datos
  - get_milestones ordena por fecha e incluye la primera convergencia
  - trace_provenance liga tema→evidencia; vacío (no inventa) si no hay evidencia
  - build_self_knowledge_context compone SOLO los bloques pertinentes

Run:  .venv/bin/python -m pytest tests/test_self_knowledge.py -v
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.self_observation import self_knowledge as SK


def _rec(**kw):
    """Fake GravityRecord-like object."""
    base = dict(
        fingerprint="nvidia_up", domain="market", hits=7,
        cc_score=0.62, freq=0.4, first_seen="2026-06-01T00:00:00+00:00",
        intent="nvidia_up", summary="",
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


# ── Detección de intención ─────────────────────────────────────────────

def test_is_origin_question_true():
    for q in [
        "¿cuándo naciste?", "¿cuál es tu origen?", "cuéntame tu historia",
        "¿cuánto tiempo llevas funcionando?", "how old are you?",
        "when were you created?",
        # Fraseos naturales que antes fallaban (tuteo/voseo/pronombre/identidad)
        "¿Desde cuándo tú existes?", "desde cuándo vos existís",
        "¿qué edad tenés?", "¿quién sos?", "¿quién eres?", "¿qué sos?",
        "preséntate", "¿quién es vectrax?", "who are you?",
    ]:
        assert SK.is_origin_question(q), q


def test_is_origin_question_ignores_user_identity():
    # "quién soy" es sobre el USUARIO, no debe activar origen de Vectrax.
    assert not SK.is_origin_question("¿quién soy?")
    assert not SK.is_origin_question("¿quién soy yo?")
    assert not SK.is_origin_question("who am i")
    assert not SK.is_origin_question("hola, todo bien")


def test_is_provenance_question_true():
    for q in [
        "¿cómo sabes eso?", "¿en qué te basas?", "¿de dónde sacaste eso?",
        "¿por qué crees eso?", "how do you know?", "based on what?",
        "¿cómo estás tan seguro?",
    ]:
        assert SK.is_provenance_question(q), q


def test_is_provenance_question_false():
    assert not SK.is_provenance_question("¿qué hora es?")
    assert not SK.is_provenance_question("gracias")


# ── get_origin ─────────────────────────────────────────────────────────

def test_get_origin_picks_oldest_durable_store():
    SK.invalidate_cache()

    def _fake_scalar(path, sql, params=()):
        if "FROM stars" in sql:
            return 2000000000.0        # 2033 — el más nuevo
        if "convergence_events" in sql:
            return 1600000000.0        # 2020-09 — el MÁS ANTIGUO
        if "snapshots" in sql:
            return 1700000000.0        # 2023
        return None

    with patch.object(SK, "_sqlite_scalar", side_effect=_fake_scalar):
        origin = SK.get_origin()

    # first_trace = gestación derivada del store durable más antiguo
    ft = origin["first_trace"]
    assert ft["source"] == "convergences"
    assert ft["date"] == "2020-09-13"
    assert ft["age_days"] > 0
    assert set(ft["sources"].keys()) == {"stars", "convergences", "evolution"}
    # Ancla canónica institucional SIEMPRE presente y distinta de la gestación
    assert origin["institutional_birth"]["date"] == "2026-04-07"
    assert origin["institutional_birth"]["entity"] == "Vectrax-Core LLC"


def test_get_origin_first_trace_empty_but_institutional_present():
    # Sin datos durables: no hay gestación, pero el nacimiento institucional
    # (hecho declarado) sigue presente.
    SK.invalidate_cache()
    with patch.object(SK, "_sqlite_scalar", return_value=None):
        origin = SK.get_origin()
    assert origin["first_trace"] == {}
    assert origin["institutional_birth"]["date"] == "2026-04-07"


def test_render_origin_distinguishes_birth_from_gestation():
    # Las dos anclas NO deben presentarse como nacimientos equivalentes.
    origin = {
        "institutional_birth": {"date": "2026-04-07", "entity": "Vectrax-Core LLC", "age_days": 30},
        "first_trace": {"date": "2026-03-06", "age_days": 60, "source": "stars",
                        "sources": {"stars": "2026-03-06"}},
    }
    txt = SK._render_origin(origin, lang="es").lower()
    assert "nacimiento institucional" in txt and "2026-04-07" in txt
    assert "gestación" in txt and "2026-03-06" in txt


# ── get_milestones ─────────────────────────────────────────────────────

def test_get_milestones_orders_and_includes_first_convergence():
    SK.invalidate_cache()

    snaps = [
        {"date": "2026-06-09", "stars": 10},
        {"date": "2026-06-10", "stars": 50},   # salto +40
        {"date": "2026-06-11", "stars": 55},
    ]
    census = types.SimpleNamespace(
        domains={"market": 100, "cybersecurity": 49},
        constellations=3, convergences=2, total=149,
    )
    conv_row = {
        "intent": "cve_family", "combined_cc": 0.55,
        "timestamp": 1749465600.0,  # 2025-06-09
        "star_a": "a", "star_b": "b",
    }

    origin = {
        "institutional_birth": {"date": "2026-04-07", "entity": "Vectrax-Core LLC", "age_days": 30},
        "first_trace": {"date": "2026-03-06", "age_days": 60, "source": "stars",
                        "sources": {"stars": "2026-03-06"}},
    }
    with patch.object(SK, "get_origin", return_value=origin), \
         patch.object(SK, "_sqlite_row", return_value=conv_row), \
         patch("core.self_observation.evolution_memory.get_all_snapshots",
               return_value=snaps), \
         patch("core.universe_census.get_census", return_value=census):
        ms = SK.get_milestones(limit=6)

    whats = " | ".join(m["what"] for m in ms)
    assert "nacimiento institucional" in whats
    assert "gestación" in whats
    assert "primera convergencia" in whats
    assert "+40 estrellas" in whats
    assert "dominios que observo hoy" in whats
    # Los que tienen fecha van antes que los sin fecha
    dated = [m for m in ms if m["date"]]
    undated = [m for m in ms if not m["date"]]
    assert ms[: len(dated)] == dated
    assert ms[len(dated):] == undated


# ── trace_provenance ───────────────────────────────────────────────────

def test_trace_provenance_links_topic_to_evidence():
    SK.invalidate_cache()

    fake_index = types.SimpleNamespace(
        top_stars=lambda n=12, domain=None: [_rec(), _rec(fingerprint="amd", intent="amd_up")]
    )
    census = types.SimpleNamespace(
        domains={"market": 120}, constellations=4, convergences=3, total=200,
    )

    def _fake_scalar(path, sql, params=()):
        if "COUNT(*)" in sql:
            return 42
        if "MIN(timestamp)" in sql:
            return "2026-06-01T00:00:00+00:00"
        return None

    with patch("core.learn.gravity_engine.get_gravity_index", return_value=fake_index), \
         patch("core.self_observation.observation_ledger.get_by_domain",
               return_value=[{"summary": "nvidia subió con fuerza"}]), \
         patch("core.learn.convergence_history.get_active", return_value=[]), \
         patch("core.universe_census.get_census", return_value=census), \
         patch.object(SK, "_sqlite_scalar", side_effect=_fake_scalar):
        prov = SK.trace_provenance(
            topic="nvidia", domain="market", topic_tokens=["nvidia"],
        )

    assert prov["domain"] == "market"
    text = prov["text"]
    assert "[PROCEDENCIA" in text
    assert "market" in text
    assert "hits=" in text
    # La estrella que hace match con el tema aparece
    assert "nvidia" in text
    # Observaciones con conteo real
    assert "42" in text
    # Estructura de evidencia poblada
    assert prov["evidence"]["stars"]
    assert prov["evidence"]["observations"]["count"] == 42


def test_trace_provenance_empty_when_no_evidence():
    SK.invalidate_cache()

    empty_index = types.SimpleNamespace(top_stars=lambda n=12, domain=None: [])
    census = types.SimpleNamespace(
        domains={}, constellations=0, convergences=0, total=0,
    )
    with patch("core.learn.gravity_engine.get_gravity_index", return_value=empty_index), \
         patch("core.self_observation.observation_ledger.get_by_domain", return_value=[]), \
         patch("core.self_observation.observation_ledger.count", return_value=0), \
         patch("core.learn.convergence_history.get_active", return_value=[]), \
         patch("core.universe_census.get_census", return_value=census), \
         patch.object(SK, "_sqlite_scalar", return_value=None):
        prov = SK.trace_provenance(topic="algo raro", domain="market")

    assert prov["text"] == ""


# ── build_self_knowledge_context (orquestador) ─────────────────────────

def test_build_context_origin_question_composes_blocks():
    SK.invalidate_cache()
    origin = {
        "institutional_birth": {"date": "2026-04-07", "entity": "Vectrax-Core LLC", "age_days": 30},
        "first_trace": {"date": "2026-03-06", "age_days": 60, "source": "stars",
                        "sources": {"stars": "2026-03-06"}},
    }
    with patch.object(SK, "get_origin", return_value=origin), \
         patch.object(SK, "get_milestones",
                      return_value=[{"date": "2026-04-07", "what": "nacimiento institucional (Vectrax-Core LLC)"}]):
        out = SK.build_self_knowledge_context("¿cuándo naciste?", lang="es")

    assert "[ORIGEN" in out
    assert "[HITOS" in out


def test_build_context_non_matching_query_returns_empty():
    SK.invalidate_cache()
    assert SK.build_self_knowledge_context("¿qué hora es?", lang="es") == ""
    assert SK.build_self_knowledge_context("", lang="es") == ""
