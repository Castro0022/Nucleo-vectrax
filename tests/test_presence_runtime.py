"""
Tests — motor temporal de la presencia (core/presence_runtime.py + life RD step)
=================================================================================
Invariantes decididos con el creador:
  - UN SOLO estado compartido: dos lecturas al mismo `now` dan el mismo estado.
  - Invariante de tensión SOBRE CONTENIDO: a tensión alta el contenido cambia
    frame a frame (nunca calma); a tensión baja puede estar en calma.
  - Respiración = feed (contenido), no brillo.
  - La semilla entra como cambio SUAVE de la entrada (sin saltos) — reemplaza el
    antiguo test de "cruce sin discontinuidad" (que medía blend de salidas).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import presence_runtime as R
from core import universe_field as UF
from core.presence import life as L
from core.presence import projection as P
from core.presence.style import PresenceStyle


def _style() -> PresenceStyle:
    return PresenceStyle(grid=48, rd_steps=24, breathing_period=16, fps=12)


def _seed(masses=(12.0, 6.0, 9.0)) -> P.DensityField:
    return P.project(
        [
            P.Node("a", "market", "HOT", masses[0]),
            P.Node("b", "cognition", "WARM", masses[1]),
            P.Node("c", "knowledge", "OUTER", masses[2]),
        ],
        size=48,
    )


def _getter(seed, tension=0.5):
    return lambda: (seed, tension)


# --- primitivos puros -------------------------------------------------------

def test_rd_step_is_pure_and_deterministic():
    s = L._norm(_seed().density)
    U, V = L.seed_to_state(s)
    feed = L.feed_field(s, 0.5, 3, _style())
    kill = L._dynamics(0.5)["kill"]
    a = L.rd_step(U, V, feed, kill, steps=6)
    b = L.rd_step(U, V, feed, kill, steps=6)
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])
    assert not np.shares_memory(a[1], V)  # no muta la entrada


def test_feed_field_breathes_and_attracts_to_seed():
    s = L._norm(_seed().density)
    st = _style()
    f0 = L.feed_field(s, 0.6, 0, st)
    f4 = L.feed_field(s, 0.6, 4, st)
    assert not np.allclose(f0, f4)  # respiración: el feed cambia con el frame
    assert float(np.corrcoef(f0.ravel(), s.ravel())[0, 1]) > 0.5  # atractor a la semilla


# --- estado único compartido ------------------------------------------------

def test_single_shared_state_same_now():
    st = _style()
    R.reset()
    g = _getter(_seed())
    R.current_field(now=100.0, style=st, get_seed=g)  # init
    R.current_field(now=101.0, style=st, get_seed=g)  # avanza
    f1 = R.current_field(now=101.0, style=st, get_seed=g)
    f2 = R.current_field(now=101.0, style=st, get_seed=g)
    assert np.array_equal(f1.density, f2.density)  # mismo now ⇒ mismo estado


def test_state_evolves_content_not_just_scale():
    st = _style()
    R.reset()
    g = _getter(_seed())
    a = R.current_field(now=200.0, style=st, get_seed=g)
    b = R.current_field(now=203.0, style=st, get_seed=g)  # +3 s
    assert not np.array_equal(a.density, b.density)  # el contenido evoluciona
    m = a.density > 0.02
    if int(m.sum()) > 10:
        ratio = b.density[m] / a.density[m]
        assert float(ratio.std()) > 1e-3  # cambia el contenido, no solo el brillo


# --- invariante de tensión SOBRE CONTENIDO ---------------------------------

def _content_agitation(seed_norm, tension, st, frames=8, spf=6):
    U, V = L.seed_to_state(seed_norm)
    kill = L._dynamics(tension)["kill"]
    vs = []
    for fr in range(1, frames + 1):
        feed = L.feed_field(seed_norm, tension, fr, st)
        U, V = L.rd_step(U, V, feed, kill, steps=spf)
        vs.append(V.copy())
    return float(np.mean([np.abs(vs[i + 1] - vs[i]).mean() for i in range(len(vs) - 1)]))


def test_high_tension_never_calm_content():
    st = _style()
    s = L._norm(_seed().density)
    calm = _content_agitation(s, 0.05, st)
    offenders = []
    for tension in (0.70, 0.80, 0.90, 1.00):
        a = _content_agitation(s, tension, st)
        if not (a > calm and a > 1e-6):
            offenders.append((tension, round(a, 8)))
    assert not offenders, f"calma con tensión alta (calm={calm:.2e}): {offenders}"


def test_low_tension_may_be_calm_content():
    st = _style()
    s = L._norm(_seed().density)
    assert _content_agitation(s, 0.05, st) >= 0.0  # calma baja válida (no crashea)


# --- semilla = cambio SUAVE de la entrada (reemplaza discontinuidad) --------

def test_seed_enters_as_smooth_input():
    st = _style()
    A = L._norm(_seed((12.0, 6.0, 9.0)).density)
    B = L._norm(_seed((3.0, 14.0, 5.0)).density)
    tension = 0.4
    kill = L._dynamics(tension)["kill"]
    spf = 6
    frames = max(1, int(UF.TRANSITION_SECONDS * st.fps))

    def run(seed_seq):
        U, V = L.seed_to_state(seed_seq[0])
        prev = V
        mx = 0.0
        for i, sd in enumerate(seed_seq[1:], start=1):
            feed = L.feed_field(sd, tension, i, st)
            U, V = L.rd_step(U, V, feed, kill, steps=spf)
            mx = max(mx, float(np.abs(V - prev).max()))
            prev = V.copy()
        return mx

    normal = run([A] * (frames + 1))
    smooth = run([(1.0 - min(1.0, i / frames)) * A + min(1.0, i / frames) * B for i in range(frames + 1)])
    abrupt = run([A] * (frames // 2) + [B] * (frames - frames // 2 + 1))

    # El cruce suave nunca es más brusco que un salto, ni supera el cambio normal.
    assert smooth <= abrupt + 1e-9, f"cruce no suave: smooth={smooth:.6f} abrupt={abrupt:.6f}"
    assert smooth <= normal * 1.5, f"entrada brusca: smooth={smooth:.6f} normal={normal:.6f}"


# --- robustez ---------------------------------------------------------------

def test_empty_seed_stays_black():
    st = _style()
    R.reset()
    z = P.DensityField(size=32, density=np.zeros((32, 32)), color=np.zeros((32, 32, 3)))
    f = R.current_field(now=10.0, style=st, get_seed=_getter(z, 0.9))
    assert float(f.density.max()) == 0.0  # nada que inventar (§5)


def test_png_render_is_valid():
    st = _style()
    R.reset()
    png = L.frame_to_png_bytes(R.current_field(now=5.0, style=st, get_seed=_getter(_seed())))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
