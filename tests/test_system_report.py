"""
Tests — Reporte de estado global (core/system_report.py).

Cubre: composición del snapshot (get_global_state) desde fuentes parcheadas,
render determinista con las 5 secciones, ocultamiento de conteos privados en
scope="public", robustez defensiva (si una fuente falla, no rompe) y el detector
estricto is_global_status_request.

Run:  python -m pytest tests/test_system_report.py -v
"""
from __future__ import annotations

import os
import sys
import types
from contextlib import ExitStack
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import system_report as SR


# ── Fakes ──────────────────────────────────────────────────────────────

def _fake_census(**over):
    ns = types.SimpleNamespace(
        total=100, gravitational=60, knowledge=30, users=10,
        domains={"market": 40, "freight_logistics": 20},
        convergences=7, patterns=5, constellations=3,
        mass_total=12.3456, word_gravity_count=15,
        users_total=8, interactions=200, user_facts=12, teams=1,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


_ENGINES = {
    "total": 3, "available": 2,
    "engines": [
        {"name": "gravity_engine", "tier": "core"},
        {"name": "criterion", "tier": "observe"},
        {"name": "voice_engine", "tier": "external"},
    ],
}

_GROWTH = {
    "window_days": 7, "new_stars": 4, "active_stars": 9,
    "growing_stars": 2, "new_by_domain": {"market": 3},
}


class _FakeGI:
    def __init__(self, growth):
        self._g = growth

    def growth_trends(self, days=7):
        return self._g


def _patch_all(stack, *, census=None, engines=None, gi=None,
               births=50, dissolutions=6, active=4, domains=None):
    stack.enter_context(patch(
        "core.universe_census.get_census",
        return_value=census if census is not None else _fake_census(),
    ))
    stack.enter_context(patch(
        "core.orchestration.get_engine_status",
        return_value=engines if engines is not None else _ENGINES,
    ))
    stack.enter_context(patch(
        "core.learn.gravity_engine.get_gravity_index",
        return_value=gi if gi is not None else _FakeGI(_GROWTH),
    ))
    stack.enter_context(patch(
        "core.learn.convergence_history.count_events",
        return_value={"birth": births, "dissolution": dissolutions},
    ))
    stack.enter_context(patch(
        "core.learn.convergence_history.get_active",
        return_value=[{} for _ in range(active)],
    ))
    stack.enter_context(patch(
        "core.domain_knowledge.list_domains",
        return_value=domains if domains is not None else ["market", "freight_logistics"],
    ))


# ── 1. Snapshot estructurado ────────────────────────────────────────────

def test_get_global_state_composes_all_sources():
    with ExitStack() as stack:
        _patch_all(stack)
        state = SR.get_global_state()

    assert state["stars"] == {
        "total": 100, "gravitational": 60, "knowledge": 30, "users": 10,
    }
    assert state["convergences"]["cross_domain"] == 7
    assert state["convergences"]["history"] == {
        "births": 50, "dissolutions": 6, "active": 4,
    }
    eng = state["engines"]
    assert eng["total"] == 3 and eng["available"] == 2
    assert eng["by_tier"] == {"core": 1, "observe": 1, "external": 1}
    assert eng["external"] == ["voice_engine"]
    assert state["domains"]["gravity"] == {"market": 40, "freight_logistics": 20}
    assert state["domains"]["criterion_library"] == ["market", "freight_logistics"]
    assert state["growth"]["new_stars"] == 4
    assert state["extra"]["mass_total"] == 12.3456
    assert state["private"]["users_total"] == 8


# ── 2. Render determinista — 5 secciones ────────────────────────────────

def test_build_report_full_has_all_sections():
    with ExitStack() as stack:
        _patch_all(stack)
        state = SR.get_global_state()
    report = SR.build_global_report(lang="es", scope="full", state=state)

    for token in ("Estrellas:", "Convergencias:", "Dominios:", "Motores:",
                  "Crecimiento", "Usuarios:"):
        assert token in report, f"falta la sección {token!r}"
    assert "100" in report          # total de estrellas
    assert "voice_engine" not in report  # el detalle de motores no se vuelca aquí


# ── 3. scope="public" oculta conteos privados ───────────────────────────

def test_public_scope_hides_private_counts():
    with ExitStack() as stack:
        _patch_all(stack)
        state = SR.get_global_state()

    pub = SR.build_global_report(scope="public", state=state)
    full = SR.build_global_report(scope="full", state=state)

    assert "Usuarios:" not in pub          # sin conteos privados
    assert "interacciones" not in pub
    assert "Estrellas:" in pub             # pero sí la vista del universo
    assert "Motores:" in pub
    assert "Usuarios:" in full             # full sí los incluye


# ── 4. Defensivo: si TODA fuente falla, no rompe ────────────────────────

def test_get_global_state_defensive_on_source_failure():
    targets = [
        "core.universe_census.get_census",
        "core.orchestration.get_engine_status",
        "core.learn.gravity_engine.get_gravity_index",
        "core.learn.convergence_history.count_events",
        "core.learn.convergence_history.get_active",
        "core.domain_knowledge.list_domains",
    ]
    with ExitStack() as stack:
        for t in targets:
            stack.enter_context(patch(t, side_effect=RuntimeError("boom")))
        state = SR.get_global_state()   # no debe lanzar

    assert state["stars"]["total"] == 0
    assert state["engines"]["total"] == 0
    assert state["domains"]["criterion_library"] == []
    # y el render sigue produciendo texto usable
    report = SR.build_global_report(state=state)
    assert report and "VECTRAX" in report


# ── 5. Detector estricto de petición de estado global ───────────────────

def test_is_global_status_request_positive():
    for q in (
        "dame el estado global",
        "¿cuántas estrellas tienes?",
        "reporte del sistema",
        "resumen del universo",
        "¿cómo está el sistema?",
        "crecimiento del universo",
        "universe status please",
        "how many engines are active",
    ):
        assert SR.is_global_status_request(q), q


def test_is_global_status_request_negative():
    for q in (
        "hola, ¿cómo estás?",
        "cuéntame un chiste",
        "¿qué opinas de la bolsa?",
        "guarda esta nota",
        "",
    ):
        assert not SR.is_global_status_request(q), q


# ── 6. Narrativa por-dominio grounded ───────────────────────────────────

class _FakeGIDom:
    def __init__(self, stats, growth, top):
        self._stats, self._growth, self._top = stats, growth, top

    def domain_stats(self):
        return self._stats

    def growth_trends(self, days=7):
        return self._growth

    def top_stars(self, n=10, domain=None):
        return self._top.get(domain, [])[:n]


def test_build_domain_observations_grounded():
    stats = {
        "market": {"count": 40, "total_hits": 1000},
        "freight_logistics": {"count": 20, "total_hits": 500},
        "unknown": {"count": 999, "total_hits": 9999},   # ruido → excluido
    }
    growth = {"new_by_domain": {"market": 3}}
    top = {
        "market": [
            types.SimpleNamespace(intent="AAPL buy", fingerprint="fp1", hits=69),
            types.SimpleNamespace(intent="BTC", fingerprint="fp2", hits=51),
        ],
        "freight_logistics": [
            types.SimpleNamespace(intent="empty_miles", fingerprint="fp3", hits=40),
        ],
    }
    gi = _FakeGIDom(stats, growth, top)
    with patch("core.learn.gravity_engine.get_gravity_index", return_value=gi):
        out = SR.build_domain_observations(lang="es")

    assert "market" in out and "freight_logistics" in out
    assert "unknown" not in out                 # dominio de ruido excluido
    assert "40 estrellas" in out and "1000 activaciones" in out
    assert "AAPL buy" in out and "hits 69" in out
    assert "+3 nuevas esta semana" in out       # crecimiento real inyectado


def test_build_domain_observations_empty_when_no_stats():
    gi = _FakeGIDom({}, {}, {})
    with patch("core.learn.gravity_engine.get_gravity_index", return_value=gi):
        assert SR.build_domain_observations() == ""


def test_build_domain_observations_defensive():
    with patch("core.learn.gravity_engine.get_gravity_index",
               side_effect=RuntimeError("boom")):
        assert SR.build_domain_observations() == ""
