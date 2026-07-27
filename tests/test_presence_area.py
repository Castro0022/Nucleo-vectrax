"""
Tests — área ∝ masa (§2: el área no puede mentir sobre el estado)
==================================================================
El arreglo tiene dos mecanismos, y se testean directamente:
  1. cada dominio ocupa un ARCO de ancho ∝ su nº de nodos (no un ángulo único)
     → la extensión angular de un dominio ~ su nº de nodos (y su masa);
  2. σ es CONSTANTE (la capa decide el RADIO, no el tamaño de la huella)
     → un nodo outer no pinta más área que un nodo core.
Juntos ⇒ el área pintada por un dominio ~ su nº de nodos, no la geometría.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.presence import projection as P


def test_arc_width_proportional_to_node_count():
    # A: 30 nodos, B: 10 → el arco de A debe ser ~3× el de B.
    nodes = (
        [P.Node(f"a{i}", "A", "MID", 1.0) for i in range(30)]
        + [P.Node(f"b{i}", "B", "MID", 1.0) for i in range(10)]
    )
    angs = P._node_angles(nodes)
    a_ang, b_ang = angs[:30], angs[30:]
    span_a = max(a_ang) - min(a_ang)
    span_b = max(b_ang) - min(b_ang)
    ratio = span_a / span_b
    assert 2.4 < ratio < 4.0, f"arco no ∝ nº nodos: ratio={ratio:.2f} (esperado ~3.2)"


def test_footprint_independent_of_layer():
    # σ constante: la huella (celdas no nulas) de un nodo NO depende de su capa.
    core = P.project([P.Node("x", "d", "CORE", 1.0)])
    outer = P.project([P.Node("y", "d", "OUTER", 1.0)])
    n_core = int((core.density > 0).sum())
    n_outer = int((outer.density > 0).sum())
    assert n_core > 0 and n_outer > 0
    assert abs(n_core - n_outer) <= 0.2 * max(n_core, n_outer), (
        f"la huella depende de la capa (σ no constante): core={n_core} outer={n_outer}"
    )


def test_soft_knee_boundary_decays_not_steps():
    # Borde SUAVE: no debe haber un salto de 0 a floor·máx (corte duro).
    nodes = [P.Node(f"n{i}", "d", ["CORE", "MID", "OUTER"][i % 3], 1.0) for i in range(12)]
    d = P.project(nodes).density
    nz = d[d > 0]
    mx = float(d.max())
    # con corte duro, min_nz/max ≈ floor_frac (0.05); con soft-knee decae ~a 0.
    assert float(nz.min()) / mx < 0.02, "el borde salta (corte duro), no decae"
