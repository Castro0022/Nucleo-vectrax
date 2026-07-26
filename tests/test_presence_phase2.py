"""
Tests — Presencia Fase 2: la presencia viva (spec §9 Fase 2)
=============================================================
Aceptación (spec, aclarada por el creador):
  - Determinista: mismo (seed, tension, t) ⇒ mismo frame, byte a byte.
  - Sembrado por el campo: sin campo no hay estructura; el campo cambia la salida.
  - INVARIANTE de tensión (§9): con tensión BAJA puede —y debe— verse tranquila;
    con tensión ALTA NO debe existir ninguna ruta que la deje en calma. El barrido
    prueba esa relación; NO marca como fallo la calma con tensión baja.

Hermético y rápido: estilo pequeño y nodos sintéticos (no toca el universo real).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.presence import life as L
from core.presence import projection as P
from core.presence.style import PresenceStyle


def _style() -> PresenceStyle:
    # Pequeño y rápido, pero con dinámica suficiente para el barrido.
    return PresenceStyle(grid=48, rd_steps=24, breathing_period=16, fps=12)


def _seed(size: int = 48) -> P.DensityField:
    return P.project(
        [
            P.Node("n1", "market", "HOT", 12.0),
            P.Node("n2", "cognition", "WARM", 6.0),
            P.Node("n3", "knowledge", "OUTER", 9.0),
            P.Node("n4", "materializadas", "CORE", 5.0),
        ],
        size=size,
    )


# --- Determinismo -----------------------------------------------------------

def test_evolve_is_deterministic():
    s, st = _seed(), _style()
    a = L.evolve(s, 0.5, 7, st)
    b = L.evolve(s, 0.5, 7, st)
    assert np.array_equal(a.density, b.density)
    assert np.array_equal(a.color, b.color)


def test_png_is_deterministic():
    s, st = _seed(), _style()
    p1 = L.frame_to_png_bytes(L.evolve(s, 0.7, 3, st))
    p2 = L.frame_to_png_bytes(L.evolve(s, 0.7, 3, st))
    assert p1 == p2
    assert p1[:8] == b"\x89PNG\r\n\x1a\n"


# --- Sembrado por el campo (§4/§5) -----------------------------------------

def test_empty_seed_stays_black():
    z = P.DensityField(
        size=32,
        density=np.zeros((32, 32)),
        color=np.zeros((32, 32, 3)),
    )
    f = L.evolve(z, 0.9, 10, _style())  # tensión alta, pero no hay campo
    assert float(f.density.max()) == 0.0  # nada que inventar (§5)


def test_seed_changes_output():
    st = _style()
    f1 = L.evolve(_seed(), 0.5, 4, st)
    f2 = L.evolve(P.project([P.Node("z", "freight", "DEEP", 50.0)], size=48), 0.5, 4, st)
    assert not np.array_equal(f1.density, f2.density)


# --- Continuidad (§2/§9c) ---------------------------------------------------

def test_output_is_continuous_grid():
    f = L.evolve(_seed(), 0.5, 2, _style())
    assert f.density.shape == (48, 48)
    assert f.color.shape == (48, 48, 3)
    assert not hasattr(f, "nodes")
    assert not hasattr(f, "count")


# --- Invariante de tensión (§9, aclarado) ----------------------------------

def _agitation(seed, tension, style, n_frames: int = 6) -> float:
    """Agitación = media de |frame(t+1) - frame(t)| sobre una ventana."""
    ds = [L.evolve(seed, tension, t, style).density for t in range(n_frames)]
    return float(np.mean([np.abs(ds[i + 1] - ds[i]).mean() for i in range(len(ds) - 1)]))


def test_dynamics_is_single_monotonic_mapping():
    # UN solo mapeo tensión→parámetros; amplitud y deriva monótonas no decrecientes.
    xs = [i / 20.0 for i in range(21)]
    amps = [L._dynamics(x)["breath_amp"] for x in xs]
    drifts = [L._dynamics(x)["drift"] for x in xs]
    assert all(amps[i + 1] >= amps[i] for i in range(len(amps) - 1))
    assert all(drifts[i + 1] >= drifts[i] for i in range(len(drifts) - 1))
    assert amps[-1] > amps[0] and drifts[-1] > drifts[0]


def test_low_tension_may_be_calm():
    # La calma con tensión BAJA es válida — NO es fallo. Solo exige que no crashee.
    a = _agitation(_seed(), 0.05, _style())
    assert a >= 0.0


def test_high_tension_never_calm():
    # Con tensión ALTA no debe existir ruta que la deje en calma. Barre todo el
    # rango alto y REPORTA cualquier ofensor (calma con tensión alta).
    st, seed = _style(), _seed()
    calm = _agitation(seed, 0.05, st)
    offenders = []
    for tension in (0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.00):
        a = _agitation(seed, tension, st)
        if not (a > calm and a > 1e-4):
            offenders.append((tension, round(a, 6)))
    assert not offenders, f"calma con tensión alta (calm={calm:.6f}): {offenders}"


def test_agitation_increases_from_low_to_high():
    st, seed = _style(), _seed()
    assert _agitation(seed, 0.90, st) > _agitation(seed, 0.10, st)


# La tensión (ESTADO lento) se computa/cachea en core/universe_field.py (capa
# del universo); su test vive en tests/test_universe_field.py.
