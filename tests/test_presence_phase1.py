"""
Tests — Presencia Fase 1: la proyección estática (spec §9 Fase 1)
==================================================================
Aceptación (verbatim del spec):
  (a) mismos datos producen el mismo campo, SIEMPRE.
  (b) al cambiar la masa de un dominio, el campo cambia de forma visible.
  (c) NO existe ningún elemento discreto en la salida.

Hermético: nodos sintéticos, sin tocar el gravity index real.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.presence import projection as P


def _nodes():
    # dominios y capas variados; masas distintas
    return [
        P.Node("n1", "market", "HOT", 12.0),
        P.Node("n2", "market", "WARM", 4.0),
        P.Node("n3", "cognition", "COLD", 6.0),
        P.Node("n4", "identity", "HOT", 9.0),
        P.Node("n5", "freight", "DEEP", 2.0),
        P.Node("n6", "cognition", "WARM", 5.0),
    ]


# --- (a) determinismo -------------------------------------------------------

def test_same_data_same_field_always():
    a = P.project(_nodes())
    b = P.project(_nodes())
    assert np.array_equal(a.density, b.density), "misma data ⇒ mismo campo de densidad"
    assert np.array_equal(a.color, b.color), "misma data ⇒ mismo campo de color"


def test_render_is_deterministic():
    a = P.field_to_png_bytes(P.project(_nodes()))
    b = P.field_to_png_bytes(P.project(_nodes()))
    assert a == b, "el render (PNG) también es determinista, byte a byte"


def test_node_order_does_not_matter():
    ns = _nodes()
    f1 = P.project(ns)
    f2 = P.project(list(reversed(ns)))
    assert np.array_equal(f1.density, f2.density), "el orden de entrada no cambia el campo"


# --- (b) sensibilidad a la masa por dominio --------------------------------

def test_domain_mass_change_changes_field_visibly():
    base = P.project(_nodes())
    heavier = _nodes()
    # duplica la masa del dominio 'market'
    heavier = [
        P.Node(n.id, n.domain, n.tier, n.mass * 2 if n.domain == "market" else n.mass)
        for n in heavier
    ]
    changed = P.project(heavier)
    assert not np.array_equal(base.density, changed.density), \
        "cambiar la masa de un dominio DEBE cambiar el campo"
    # y el cambio debe ser visible (no ruido numérico)
    delta = float(np.abs(changed.density - base.density).max())
    assert delta > 1e-6, f"el cambio debe ser visible, delta={delta}"


def test_dominant_domain_shows_in_color():
    # un dominio con masa aplastante debe teñir el pico del campo hacia su color
    dom = "market"
    ns = [P.Node("big", dom, "HOT", 1000.0), P.Node("small", "cognition", "HOT", 0.1)]
    f = P.project(ns)
    peak = np.unravel_index(int(np.argmax(f.density)), f.density.shape)
    got = tuple(round(c, 3) for c in f.color[peak])
    want = tuple(round(c, 3) for c in P._domain_rgb(dom))
    assert got == want, f"el dominio dominante tiñe el pico: got={got} want={want}"


# --- (c) no existe ningún elemento discreto en la salida -------------------

def test_output_has_no_discrete_elements():
    f = P.project(_nodes())
    # La salida son SOLO rejillas continuas; nada que se pueda contar como nodos.
    assert not hasattr(f, "nodes")
    assert not hasattr(f, "count")
    assert not hasattr(f, "points")
    # density/color son rejillas 2D/3D continuas del tamaño de la rejilla.
    assert f.density.shape == (P.GRID, P.GRID)
    assert f.color.shape == (P.GRID, P.GRID, 3)
    # el número de celdas NO revela el número de nodos (6 nodos → 180*180 celdas)
    assert f.density.size == P.GRID * P.GRID


def test_render_is_continuous_raster_png():
    png = P.field_to_png_bytes(P.project(_nodes()))
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "la salida es un raster PNG (campo, no nodos)"
    assert len(png) > 100


# --- robustez: vacío no rompe ---------------------------------------------

def test_empty_universe_is_valid_black_field():
    f = P.project([])
    assert f.density.shape == (P.GRID, P.GRID)
    assert float(f.density.max()) == 0.0
    png = P.field_to_png_bytes(f)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
