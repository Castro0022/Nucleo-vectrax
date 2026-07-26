"""
core/presence/life.py — Fase 2: la presencia viva (spec §9)
============================================================
"Reacción-difusión sembrada por el campo. Respiración, tensión, deriva."

La Fase 1 (`projection.py`) produce un campo de densidad ESTÁTICO. La Fase 2 lo
vuelve VIVO sin dejar de ser honesta ni determinista:

  - **Sembrado por el campo (§4)**: el patrón se madura con reacción-difusión
    (Gray-Scott) a partir de la densidad de la Fase 1; el patrón mostrado es un
    blend del propio campo + su reacción-difusión (nunca inventa estructura que
    no venga del universo, §5).
  - **Respiración (§9 Fase 2)**: sobre el patrón maduro se aplica una respiración
    global; su amplitud la fija la TENSIÓN. La presencia vive EN SU SITIO: entre
    frames cambia el contenido (respiración), nunca la posición.
  - **Deriva (§4) = fenotipo, NO traslación**: la deriva se expresa en el propio
    patrón (f, k, σ del RD según lo vivido/tensión), no desplazando la imagen.
    No hay `np.roll` ni traslación global.
  - **Tensión = ESTADO** (no estilo, §8): escalar en [0,1] derivado de señales
    REALES del universo + la energía del propio campo. Un ÚNICO mapeo
    `_dynamics(tension)`, monótono y con piso creciente, garantiza el invariante
    del §9: con tensión ALTA no existe ninguna ruta que deje la presencia en
    calma (con tensión baja sí puede —y debe— verse tranquila).

Determinismo y coste (crítico):
  - `evolve(seed, tension, t)` es una FUNCIÓN PURA: sin reloj, sin azar. Mismo
    `(seed, tension, t)` ⇒ mismo frame, byte a byte.
  - La maduración de reacción-difusión usa un número de pasos FIJO (`rd_steps`),
    INDEPENDIENTE de `t`. `t` solo modula la respiración (un `sin`), O(1). Por
    tanto el coste por frame es PLANO en el tiempo y la respiración no se degrada.
  - El origen de `t` es el RELOJ DEL SERVIDOR (`server_frame_index`), de modo que
    la presencia tiene un único estado compartido entre todas las pestañas.
"""
from __future__ import annotations

import math
import time
from typing import Dict, Optional

import numpy as np

from core.presence.projection import DensityField, _encode_png
from core.presence.style import DEFAULT_STYLE, PresenceStyle

# --- Tensión → dinámica (ESTADO) -------------------------------------------
# Un ÚNICO mapeo, monótono no decreciente y con piso creciente. La amplitud de
# respiración CRECE con la tensión: a tensión alta el piso hace imposible (por
# construcción) que la presencia se vea en calma (§9). La deriva (§4) NO es
# traslación: es el fenotipo (feed/kill del RD) cambiando con la tensión — se
# expresa en el patrón, nunca en la posición.
_BREATH_MIN, _BREATH_MAX = 0.05, 0.45   # amplitud de respiración
_FEED_MIN, _FEED_MAX = 0.034, 0.058     # Gray-Scott feed (calma → turbulento)
_KILL_MIN, _KILL_MAX = 0.061, 0.063     # Gray-Scott kill

# Difusividades de Gray-Scott (estables con Euler explícito: du*4 < 1).
_DU, _DV = 0.16, 0.08


def _dynamics(tension: float) -> Dict[str, float]:
    """Mapeo único tensión→parámetros dinámicos (monótono, piso creciente)."""
    x = min(1.0, max(0.0, float(tension)))
    return {
        "breath_amp": _BREATH_MIN + (_BREATH_MAX - _BREATH_MIN) * x,
        "feed": _FEED_MIN + (_FEED_MAX - _FEED_MIN) * x,
        "kill": _KILL_MIN + (_KILL_MAX - _KILL_MIN) * x,
    }


# --- Utilidades numéricas (deterministas) ----------------------------------

def _norm(a: np.ndarray) -> np.ndarray:
    m = float(a.max())
    if m <= 0.0:
        return np.zeros_like(a)
    return a / m


def _laplacian(a: np.ndarray) -> np.ndarray:
    return (
        np.roll(a, 1, 0) + np.roll(a, -1, 0)
        + np.roll(a, 1, 1) + np.roll(a, -1, 1)
        - 4.0 * a
    )


def _react_diffuse(v0: np.ndarray, feed: float, kill: float, steps: int) -> np.ndarray:
    """Gray-Scott determinista, `steps` FIJOS (independiente de t)."""
    U = 1.0 - v0
    V = v0.copy()
    for _ in range(max(0, int(steps))):
        uvv = U * V * V
        U += _DU * _laplacian(U) - uvv + feed * (1.0 - U)
        V += _DV * _laplacian(V) + uvv - (feed + kill) * V
        np.clip(U, 0.0, 1.0, out=U)
        np.clip(V, 0.0, 1.0, out=V)
    return V


# --- El motor (función PURA) -----------------------------------------------

def evolve(
    seed: DensityField,
    tension: float,
    t: int,
    style: PresenceStyle = DEFAULT_STYLE,
) -> DensityField:
    """Frame vivo `t` del campo `seed`, a tensión `tension`. FUNCIÓN PURA.

    Determinista: mismo `(seed, tension, t, style)` ⇒ mismo frame. La maduración
    RD es de pasos FIJOS (no depende de t); `t` solo modula la respiración ⇒
    coste por frame plano en el tiempo. El patrón vive EN SU SITIO: sin deriva
    posicional (no hay traslación ni `np.roll`). Densidad ABSOLUTA en [0,1] (el
    render de Fase 2 NO re-normaliza por pico, para que la respiración se vea).
    """
    dens = np.asarray(seed.density, dtype=np.float64)
    size = int(seed.size)

    base = _norm(dens)
    if base.max() <= 0.0:
        # Campo vacío: nada que animar (honestidad §5) — negro estable.
        return DensityField(
            size=size,
            density=np.zeros((size, size), dtype=np.float64),
            color=np.zeros((size, size, 3), dtype=np.float64),
        )

    d = _dynamics(tension)

    # Patrón maduro (independiente de t): campo + su reacción-difusión.
    rd = _norm(_react_diffuse(base, d["feed"], d["kill"], int(style.rd_steps)))
    pattern = _norm(0.5 * base + 0.5 * rd) * 0.72  # margen para la respiración

    # El patrón vive EN SU SITIO — sin deriva posicional (sin traslación/roll).
    color = np.clip(np.asarray(seed.color, dtype=np.float64), 0.0, 1.0)

    # Respiración (t): modulación global; amplitud = tensión.
    period = max(1, int(style.breathing_period))
    breath = 1.0 + d["breath_amp"] * math.sin(2.0 * math.pi * (float(t) / period))

    density = np.clip(pattern * breath, 0.0, 1.0)
    color[density <= 0.0] = 0.0
    return DensityField(size=size, density=density, color=color)


# --- Render y tiempo del servidor ------------------------------------------
# La TENSIÓN (estado LENTO del universo) NO vive aquí: se computa y cachea en la
# capa del universo (`core.universe_field`), junto al seed. La presencia solo
# consume (seed, tensión) ya servidos (§3).

def frame_to_png_bytes(field: DensityField) -> bytes:
    """PNG del frame. Usa la densidad ABSOLUTA (sin re-normalizar por pico), de
    modo que la respiración global sí modula el brillo visible.
    """
    d = np.clip(np.asarray(field.density, dtype=np.float64), 0.0, 1.0)
    img = np.asarray(field.color, dtype=np.float64) * d[:, :, None]
    rgb8 = np.clip(img * 255.0, 0, 255).astype(np.uint8)
    return _encode_png(rgb8)


def server_frame_index(style: PresenceStyle = DEFAULT_STYLE, now: Optional[float] = None) -> int:
    """`t` derivado del RELOJ DEL SERVIDOR (estado único compartido).

    ACOTADO al ciclo de respiración: `t = int(now*fps) % breathing_period`. Así
    `t` es pequeño (no `time.time()*fps ≈ 2.1e10`) y la respiración es un bucle
    sin costuras (`sin` continuo en el wrap). Dos pestañas con el mismo reloj
    obtienen el mismo `t` ⇒ el mismo frame.
    """
    if now is None:
        now = time.time()
    fps = max(1, int(style.fps))
    period = max(1, int(style.breathing_period))
    return int(now * fps) % period


def render_current_frame_png(
    t: Optional[int] = None,
    style: PresenceStyle = DEFAULT_STYLE,
) -> bytes:
    """Devuelve el PNG del frame vivo `t`.

    El SEED y la TENSIÓN (estado LENTO) los sirve la capa del universo
    (`core.universe_field.get_current`), con cache TTL y cruce suave — la
    presencia NO tiene cache propio (§3). Aquí solo entra el tiempo RÁPIDO
    (`t` del reloj del servidor) y el render puro (`evolve` + encode). Un único
    `now` alimenta seed y `t` para mantener el estado coherente entre pestañas.
    """
    from core.universe_field import get_current  # capa universo (lazy: sin ciclo)

    now = time.time()
    seed, tension = get_current(now=now, style=style)
    if t is None:
        t = server_frame_index(style, now=now)
    return frame_to_png_bytes(evolve(seed, tension, int(t), style))
