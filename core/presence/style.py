"""
core/presence/style.py — Capa de ESTILO (spec §8: estilo separado del estado)
=============================================================================
§8 separa la FORMA visual del ESTADO (los datos y el campo). Aquí viven los
parámetros de forma de la proyección; NINGUNO se deriva de los datos.

Honestidad (§5, §10): `sigma_cut` y `floor_frac` NO salen de ninguna fórmula.
Son parámetros de FORMA elegidos por BARRIDO para que el contorno del campo
quede legible:

  - floor_frac = 0.05 → fraccion_no_vacia ≈ 0.18, dentro del criterio [0.15, 0.60].
    Elegido por COMPARACIÓN VISUAL de un abanico de candidatos al mismo instante
    del campo (0.03→0.25, 0.05→0.18, 0.08→0.07, 0.12→0.05, 0.20→0.04): 0.05 da un
    anillo lleno y legible sin halo. Es el umbral del contorno: por debajo → negro
    (fondo/silueta); en [umbral, umbral·(1+knee)] el borde decae (soft-knee), no salta.
  - sigma_cut = 3.0 → el depósito gaussiano se corta a 3σ (≈99.7% de la masa),
    dando un contorno acotado en vez de colas que llenan toda la rejilla.

Cambiar estos valores cambia la FORMA del campo, nunca su CONTENIDO (qué nodos
hay ni cuánto pesa cada uno). Por eso viven en esta capa y no en la lógica de
proyección (`core/presence/projection.py`).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PresenceStyle:
    """Parámetros de FORMA de la proyección (§8). No derivan de los datos."""

    # Resolución de la rejilla del campo (~180×180, §4).
    grid: int = 180
    # Corte del depósito gaussiano, en múltiplos de σ (parámetro de forma).
    sigma_cut: float = 3.0
    # Umbral de contorno como fracción del máximo (elegido por comparación visual
    # → fraccion ≈ 0.18). Por debajo → negro; el borde decae con soft-knee (no salta).
    floor_frac: float = 0.05

    # --- Fase 2 (presencia viva): TEMPO y FORMA del patrón, NO estado. -----
    # El ESTADO (tensión y sus parámetros derivados: feed/kill/amplitud/deriva)
    # vive en core/presence/life.py. Aquí solo van tempo/resolución (§8).
    # Pasos de maduración de la reacción-difusión (forma del patrón). FIJO:
    # no depende de t, así el coste por frame es plano en el tiempo.
    rd_steps: int = 120
    # Frames por ciclo de respiración (tempo de la "respiración").
    breathing_period: int = 60
    # Frames/segundo con que el RELOJ DEL SERVIDOR avanza t (cadencia única).
    fps: int = 12


# Estilo por defecto del canal: única fuente de verdad de la FORMA.
DEFAULT_STYLE = PresenceStyle()


# --- Fase 3: REGISTRO visual por FUENTE (§8, FORMA) -------------------------
# Cada registro (source) tiñe la presencia distinto — en la FORMA/color, sin
# texto ni etiqueta (§2). Sesgo suave mezclado con el color del universo
# (preserva la estructura). Honestidad: los grounded van cálidos/cerca del oro
# del core; el sin-respaldo (MODEL) va frío/pálido — se nota.
REGISTER_TINTS = {
    "SELF_DETERMINISTIC": (1.00, 0.82, 0.35),  # oro — datos propios, no puede mentir
    "MEMORY":             (0.45, 0.95, 0.70),  # verde-teal — memoria compartida
    "ONLINE":             (0.40, 0.75, 1.00),  # cian — traído de fuera ("lo busqué")
    "MODEL":              (0.72, 0.66, 0.88),  # violeta pálido — sin respaldo, se nota
}
# Fuerza del tinte (0..1): mezcla registro ↔ color del universo.
REGISTER_TINT_STRENGTH = 0.45
