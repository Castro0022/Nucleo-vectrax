"""
Tests — core/universe_field.py (capa del universo: cache + cruce del seed)
==========================================================================
Verifica (spec §3 + condiciones del creador):
  - dos clientes con el mismo tiempo del servidor reciben el mismo estado;
  - un refresh del seed NO produce discontinuidad brusca (MEDIBLE: el delta
    máximo por celda durante el cruce ≤ el de la respiración/movimiento normal);
  - los extremos del cruce son seeds REALES (alpha 0→prev, 1→cur);
  - el cache vive en la capa del universo, NO en presence/.
El invariante de tensión (alta nunca calma / baja puede calma) se prueba en
tests/test_presence_phase2.py sobre evolve().
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import universe_field as UF
from core.presence import life as L
from core.presence import projection as P
from core.presence.style import PresenceStyle


def _style() -> PresenceStyle:
    return PresenceStyle(grid=48, rd_steps=24, breathing_period=16, fps=12)


def _seed_a() -> P.DensityField:
    return P.project(
        [P.Node("a", "market", "HOT", 12.0), P.Node("b", "knowledge", "OUTER", 9.0)],
        size=48,
    )


def _seed_b() -> P.DensityField:
    return P.project(
        [
            P.Node("a", "market", "HOT", 14.0),
            P.Node("b", "knowledge", "OUTER", 7.0),
            P.Node("c", "cognition", "CORE", 5.0),
        ],
        size=48,
    )


def test_two_clients_same_server_time_same_state():
    UF.reset()
    A = _seed_a()
    builder = lambda: (A, 0.5)  # noqa: E731 (builder inyectado, hermético)
    UF.get_current(now=100.0, builder=builder, ttl=1000.0)  # init
    s1, t1 = UF.get_current(now=250.0, builder=builder, ttl=1000.0)
    s2, t2 = UF.get_current(now=250.0, builder=builder, ttl=1000.0)
    assert np.array_equal(s1.density, s2.density)
    assert np.array_equal(s1.color, s2.color)
    assert t1 == t2
    # Mismo tiempo del servidor ⇒ mismo frame (byte a byte).
    st = _style()
    ti = L.server_frame_index(st, now=250.0)
    assert L.frame_to_png_bytes(L.evolve(s1, t1, ti, st)) == L.frame_to_png_bytes(L.evolve(s2, t2, ti, st))


def test_blend_endpoints_are_real_seeds():
    A, B = _seed_a(), _seed_b()
    a0 = UF.blend_seeds(A, B, 0.0)
    a1 = UF.blend_seeds(A, B, 1.0)
    assert np.allclose(a0.density, A.density) and np.allclose(a0.color, A.color)
    assert np.allclose(a1.density, B.density) and np.allclose(a1.color, B.color)


# El test de "cruce sin discontinuidad" sobre salidas maduras (evolve, Fase 2) se
# REEMPLAZO por el equivalente sobre la NUEVA entrada del motor temporal (la
# semilla como atractor del RD): ver
# tests/test_presence_runtime.py::test_seed_enters_as_smooth_input.


def test_tension_in_range_and_safe():
    assert 0.0 <= UF.compute_tension(_seed_a()) <= 1.0
    assert 0.0 <= UF.compute_tension(None) <= 1.0  # sin seed: no lanza


def test_cache_lives_in_universe_layer_not_presence():
    assert isinstance(UF._CACHE, dict)
    assert hasattr(UF, "get_current")
    # presence/ NO define cache de seed (sin estado propio, §3). Se excluyen los
    # dunders del módulo (p.ej. __cached__ es la ruta de bytecode de Python).
    for mod in (L, P):
        offenders = [
            n for n in vars(mod)
            if "cache" in n.lower() and not n.startswith("__")
        ]
        assert not offenders, f"{mod.__name__} no debe tener cache: {offenders}"
