"""
core/universe_field.py — Semilla de campo del universo (con cache) [capa universo]
==================================================================================
Vive en la CAPA DEL UNIVERSO (junto a `universe_census`), NO dentro de
`presence/`. Sirve la "semilla" de campo (más la tensión) que el Canal del
Creador consume. Aquí reside el ESTADO/caché: la presencia no tiene estado ni
caché propios (spec §3).

Dos frecuencias separadas (decisión del creador):
  - LENTA — el universo, su seed y su tensión: se reconstruyen por TTL.
  - RÁPIDA — `t` y la respiración: las gestiona la presencia, por frame.

Refresco suave (un cambio real del universo debe percibirse como movimiento
continuo, no como un corte): al expirar el TTL se CONSERVA el seed que se está
mostrando y se cruza linealmente hacia el nuevo durante `TRANSITION_SECONDS`.
  - Los DOS extremos del cruce son seeds REALES derivados del universo.
  - El estado intermedio es únicamente la transición matemática entre ellos
    (no una segunda fuente de verdad).
  - No se persiste ningún estado propio de la presencia.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np

from core.presence.projection import DensityField, nodes_from_universe, project
from core.presence.style import DEFAULT_STYLE, PresenceStyle

# Frecuencia LENTA. TTL = cada cuánto se reconstruye el seed desde el universo.
# TRANSITION = ventana de cruce suave old→new (debe ser ≤ TTL para no solapar).
SEED_TTL_SECONDS = 5.0
TRANSITION_SECONDS = 3.0

# Estado del universo (cache). ESTA es la única fuente de estado; la presencia
# no guarda nada. `prev`/`cur` son seeds reales; el cruce se calcula al vuelo.
_CACHE: Dict[str, Any] = {
    "cur_seed": None,
    "cur_tension": None,
    "prev_seed": None,
    "prev_tension": None,
    "built_at": 0.0,
    "refreshed_at": 0.0,
}


# --- Tensión (ESTADO lento del universo) -----------------------------------

def _operational_tension() -> float:
    """Tensión operacional [0,1] desde el snapshot del universo (defensivo)."""
    try:
        from core.self_observation.universe_observer import observe_universe
        s = observe_universe()
        q = min(1.0, float(getattr(s, "queue_pending", 0)) / 20.0)
        e = min(1.0, float(getattr(s, "recent_error_count_24h", 0)) / 50.0)
        af = float((getattr(s, "quality_counts", {}) or {}).get("active_failures", 0))
        qf = min(1.0, af / 5.0)
        w = 0.0 if getattr(s, "worker_alive", False) else 1.0
        return min(1.0, max(0.0, 0.35 * q + 0.30 * e + 0.20 * qf + 0.15 * w))
    except Exception:
        return 0.5


def _field_tension(seed: Optional[DensityField]) -> float:
    """Tensión del propio campo [0,1] = energía de gradiente normalizada."""
    if seed is None:
        return 0.0
    try:
        d = np.asarray(seed.density, dtype=np.float64)
        if d.max() <= 0.0:
            return 0.0
        gx = float(np.abs(np.diff(d, axis=1)).mean())
        gy = float(np.abs(np.diff(d, axis=0)).mean())
        g = 0.5 * (gx + gy)
        m = float(d.mean()) or 1e-9
        return min(1.0, max(0.0, (g / m) / 0.5))
    except Exception:
        return 0.0


def compute_tension(seed: Optional[DensityField]) -> float:
    """Tensión actual [0,1]: 0.6·operacional + 0.4·campo (decisión (c))."""
    return min(1.0, max(0.0, 0.6 * _operational_tension() + 0.4 * _field_tension(seed)))


# --- Construcción (LENTA) y mezcla (PURA) ----------------------------------

def build_seed_and_tension(style: PresenceStyle = DEFAULT_STYLE) -> Tuple[DensityField, float]:
    """Reconstruye el seed desde el universo + su tensión. Coste alto (cache miss)."""
    seed = project(nodes_from_universe(), size=style.grid)
    return seed, compute_tension(seed)


def blend_seeds(a: DensityField, b: DensityField, alpha: float) -> DensityField:
    """Mezcla lineal de dos seeds REALES. `alpha=0`→a, `alpha=1`→b. PURA.

    El resultado NO es una nueva fuente de verdad: es solo la transición
    matemática entre dos extremos reales derivados del universo.
    """
    al = min(1.0, max(0.0, float(alpha)))
    density = (1.0 - al) * np.asarray(a.density, dtype=np.float64) + al * np.asarray(b.density, dtype=np.float64)
    color = (1.0 - al) * np.asarray(a.color, dtype=np.float64) + al * np.asarray(b.color, dtype=np.float64)
    return DensityField(size=a.size, density=density, color=color)


def _alpha(now: float, refreshed_at: float, transition: float) -> float:
    """Progreso del cruce en [0,1]. `transition<=0` ⇒ salto inmediato (1.0)."""
    if transition <= 0.0:
        return 1.0
    return min(1.0, max(0.0, (now - refreshed_at) / transition))


def _effective(now: float, transition: float) -> Tuple[DensityField, float]:
    """Seed + tensión EFECTIVOS ahora: cruce prev→cur según el reloj."""
    c = _CACHE
    al = _alpha(now, c["refreshed_at"], transition)
    seed = blend_seeds(c["prev_seed"], c["cur_seed"], al)
    tension = (1.0 - al) * float(c["prev_tension"]) + al * float(c["cur_tension"])
    return seed, tension


# --- API pública -----------------------------------------------------------

def get_current(
    now: Optional[float] = None,
    style: PresenceStyle = DEFAULT_STYLE,
    builder: Optional[Callable[[], Tuple[DensityField, float]]] = None,
    ttl: Optional[float] = None,
    transition: Optional[float] = None,
) -> Tuple[DensityField, float]:
    """Devuelve el (seed, tensión) EFECTIVOS para el tiempo del servidor `now`.

    Determinista respecto al estado del cache + `now`: dos clientes con el mismo
    `now` obtienen exactamente el mismo (seed, tensión) → el mismo frame. Al
    expirar el TTL se reconstruye el seed, conservando el seed EFECTIVO actual
    como extremo `prev` y cruzando suavemente hacia el nuevo.
    """
    if now is None:
        now = time.time()
    ttl = SEED_TTL_SECONDS if ttl is None else float(ttl)
    transition = TRANSITION_SECONDS if transition is None else float(transition)
    build = builder or (lambda: build_seed_and_tension(style))
    c = _CACHE

    if c["cur_seed"] is None:
        seed, tension = build()
        c.update(
            cur_seed=seed, cur_tension=tension,
            prev_seed=seed, prev_tension=tension,
            built_at=now, refreshed_at=now,
        )
        return _effective(now, transition)

    if now - float(c["built_at"]) >= ttl:
        # Antes de reemplazar: el nuevo `prev` es el seed que se ve AHORA (que a
        # su vez puede estar a mitad de un cruce anterior) → continuidad total.
        eff_seed, eff_tension = _effective(now, transition)
        seed, tension = build()
        c.update(
            prev_seed=eff_seed, prev_tension=eff_tension,
            cur_seed=seed, cur_tension=tension,
            built_at=now, refreshed_at=now,
        )

    return _effective(now, transition)


def reset() -> None:
    """Limpia el cache (para tests)."""
    _CACHE.update(
        cur_seed=None, cur_tension=None,
        prev_seed=None, prev_tension=None,
        built_at=0.0, refreshed_at=0.0,
    )
