"""
core/presence_runtime.py — Estado VIVO del Canal del Creador (capa servidor)
============================================================================
Motor TEMPORAL de la presencia: un ÚNICO estado de reacción-difusión (U/V) que
EVOLUCIONA en el tiempo del servidor. NO vive en `core/presence/` (§3): la
presencia consume; el estado (como el cache de la semilla) es de la capa del
servidor.

- **Estado único compartido**: todas las pestañas leen el MISMO U/V → ven el
  mismo instante. No se busca reproducibilidad externa por `(seed, t)`; el
  invariante es que el estado NO pueda mentir (evoluciona de verdad).
- **Semilla = atractor lento**: `universe_field` (cache 5 s + cruce suave) provee
  la semilla; entra como cambio SUAVE de la entrada (el feed del RD), sin saltos.
- **Respiración = oscilación del FEED** (contenido, §4); la tensión modula la
  amplitud → invariante de tensión SOBRE CONTENIDO.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np

from core.presence import life as L
from core.presence.projection import DensityField
from core.presence.style import (
    DEFAULT_STYLE,
    PresenceStyle,
    PULSE_INTENSITY,
    PULSE_RADIUS_FRAC,
    REGISTER_TINTS,
    REGISTER_TINT_STRENGTH,
)

# Pasos RD por frame mostrado (~0.24 ms/paso a grid 180 → ~1.5 ms/frame; sostiene
# 12 fps de sobra). Es TEMPO/forma: cuánto evoluciona el RD por frame.
SPF = 6
# Máximo de frames a avanzar de golpe tras inactividad (evita catch-up gigante).
_MAX_CATCHUP = 30

_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {
    "U": None, "V": None, "last": None, "frame": 0, "size": None,
    "pulse_kernel": None,  # núcleo gaussiano del pulso, cacheado por tamaño
}
# REGISTRO visual actual (fuente de la última locución): tiñe la presencia en la
# FORMA (§ registro por source). None = neutro (sin tinte).
_REGISTER: Dict[str, Any] = {"source": None}
# PULSOS de voz PENDIENTES (§ Vibración): cada palabra hablada añade sustancia,
# depositada en V en el próximo avance. Solo existen por eventos de habla REALES
# (sin habla → sin pulso) → invariante del pulso.
_PENDING: Dict[str, float] = {"pulse": 0.0}


def reset() -> None:
    """Limpia el estado del motor (para tests)."""
    with _LOCK:
        _STATE.update(U=None, V=None, last=None, frame=0, size=None, pulse_kernel=None)
        _REGISTER["source"] = None
        _PENDING["pulse"] = 0.0


def set_register(source: Optional[str]) -> Optional[str]:
    """Fija el REGISTRO visual actual (fuente de la locución en curso). El campo
    lo refleja como un tinte en la FORMA, sin texto ni etiqueta. Devuelve el
    registro normalizado en MAYÚSCULAS, o None.
    """
    norm = (source or "").strip().upper() or None
    with _LOCK:
        _REGISTER["source"] = norm
    return norm


def current_register() -> Optional[str]:
    """Registro visual actual (o None si neutro)."""
    return _REGISTER["source"]


def add_pulse(intensity: Optional[float] = None) -> float:
    """Registra un PULSO de voz (una palabra real hablada). Deposita
    `PULSE_INTENSITY` de sustancia en V en el próximo avance; el RD reacciona por
    su física. Devuelve la intensidad, SIEMPRE > 0: un habla real siempre produce
    un pulso visible (invariante). No se apaga con intensidad 0 — se apaga en el
    cliente dejando de emitir pulsos.
    """
    amt = PULSE_INTENSITY if intensity is None else float(intensity)
    amt = max(amt, 0.0)
    with _LOCK:
        _PENDING["pulse"] += amt
    return amt


def _pulse_kernel(size: int) -> np.ndarray:
    """Núcleo gaussiano del pulso, cacheado por tamaño en el estado."""
    k = _STATE.get("pulse_kernel")
    if k is None or int(k.shape[0]) != int(size):
        k = L.pulse_kernel(int(size), PULSE_RADIUS_FRAC)
        _STATE["pulse_kernel"] = k
    return k


def _apply_register(field: DensityField, source: Optional[str]) -> DensityField:
    """Tiñe el campo según el registro (§8, estado→forma): un tinte suave por
    fuente. Sin registro o fuente desconocida → sin cambio (neutro)."""
    if not source:
        return field
    rgb = REGISTER_TINTS.get(source)
    if not rgb:
        return field
    return L.tint_field(field, rgb, REGISTER_TINT_STRENGTH)


def _advance(
    now: float,
    style: PresenceStyle,
    get_seed: Optional[Callable[[], Tuple[DensityField, float]]],
) -> DensityField:
    """Avanza el estado RD hasta `now` y devuelve el campo a renderizar."""
    if get_seed is not None:
        seed, tension = get_seed()
    else:
        from core.universe_field import get_current  # lazy: sin ciclo de import
        seed, tension = get_current(now=now, style=style)

    seed_norm = L._norm(np.asarray(seed.density, dtype=np.float64))
    fps = max(1, int(style.fps))
    s = _STATE

    # Inicialización (o cambio de tamaño): sembrar el estado desde la semilla.
    if s["U"] is None or s["size"] != int(seed.size):
        U, V = L.seed_to_state(seed_norm)
        s.update(U=U, V=V, last=now, frame=0, size=int(seed.size))
        return L.state_to_field(V, seed.color)

    # Frames transcurridos desde la última actualización (acotado).
    frames = int((now - s["last"]) * fps) if s["last"] is not None else 0
    frames = max(0, min(frames, _MAX_CATCHUP))

    kill = L._dynamics(tension)["kill"]
    U, V = s["U"], s["V"]

    # Pulsos de voz: depositar sustancia ANTES de avanzar (el RD reacciona por su
    # física). Se depositan aunque no haya frames que avanzar, para que un habla
    # real sea visible de inmediato (invariante del pulso).
    pend = _PENDING["pulse"]
    if pend > 0.0:
        V = V + _pulse_kernel(int(seed.size)) * pend
        np.clip(V, 0.0, 1.0, out=V)
        _PENDING["pulse"] = 0.0

    for _ in range(frames):
        s["frame"] += 1
        feed = L.feed_field(seed_norm, tension, s["frame"], style)
        U, V = L.rd_step(U, V, feed, kill, steps=SPF)
    if frames > 0:
        s.update(U=U, V=V, last=now)
    elif pend > 0.0:
        s.update(U=U, V=V)  # persistir el depósito aunque el reloj no avance

    return L.state_to_field(s["V"], seed.color)


def current_field(
    now: Optional[float] = None,
    style: PresenceStyle = DEFAULT_STYLE,
    get_seed: Optional[Callable[[], Tuple[DensityField, float]]] = None,
) -> DensityField:
    """Campo vivo actual: avanza el RD según el reloj del servidor. Estado único.

    Dos llamadas con el MISMO `now` devuelven el mismo estado (la segunda no
    avanza) → dos pestañas ven el mismo instante.
    """
    if now is None:
        now = time.time()
    with _LOCK:
        field = _advance(now, style, get_seed)
        source = _REGISTER["source"]
    # Aplicar el REGISTRO (tinte por fuente) FUERA del lock: op pura sobre el campo.
    return _apply_register(field, source)


def current_frame_png(now: Optional[float] = None, style: PresenceStyle = DEFAULT_STYLE) -> bytes:
    """PNG del frame vivo actual (lo consume el endpoint)."""
    return L.frame_to_png_bytes(current_field(now, style))
