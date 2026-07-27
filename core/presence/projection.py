"""
core/presence/projection.py — Fase 1: la proyección estática (spec §4, §9)
==========================================================================
Universo → **campo continuo de densidad** → render. Sin vida, sin movimiento.

Esto NO es mover/interpolar nodos hacia el centro (eso deja una pelota de
nodos, todavía contable — §2). Es una **reducción real** (§4):

  1. Cada nodo DEPOSITA su masa en un campo de densidad continuo 2D
     (acumulación gaussiana en una rejilla ~180×180). Radio según su capa
     (Core→centro, Outer→periferia); ángulo dentro de un ARCO por dominio de
     ancho ∝ nº de nodos (el área que pinta un dominio ~ su nº de nodos/masa).
  2. Cada nodo deposita también color: se mezcla al final por peso de masa
     (si un dominio domina en masa, se nota — no es un tinte global).
  3. Se renderiza el CAMPO, no los nodos. A partir de aquí no queda nada
     discreto que contar; el contorno emerge de la distribución real de masa.

Determinismo (aceptación §9a): la proyección es una función PURA de los nodos
+ parámetros fijos. Sin LLM, sin azar, sin reloj. Mismos nodos ⇒ mismo campo,
byte a byte. El mapeo dominio→ángulo/color usa un hash estable (md5), no el
`hash()` de Python (que está salado por PYTHONHASHSEED).

Fase 1 termina aquí. La reacción-difusión que da vida (Fase 2) usará este
campo como semilla; NO se implementa en este módulo.
"""
from __future__ import annotations

import colorsys
import hashlib
import math
import struct
import zlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.presence.style import DEFAULT_STYLE

# Rejilla del campo (~180×180, §4). La FORMA vive en la capa de estilo §8;
# `GRID` es solo un alias de conveniencia hacia `DEFAULT_STYLE.grid`.
GRID = DEFAULT_STYLE.grid

# Capa → radio (fracción del semi-lado). Core al centro, Outer/Deep a la
# periferia. Determinista. Tiers reales del gravity index: HOT/WARM/COLD/DEEP.
_TIER_RADIUS = {
    # gravity index
    "HOT": 0.12,
    "WARM": 0.42,
    "COLD": 0.72,
    "DEEP": 0.90,
    # sinónimos de otras fuentes (p.ej. patrones de mercado)
    "HIGH": 0.12,
    "MEDIUM": 0.42,
    "LOW": 0.72,
    # user_stars (Materializadas) y knowledge stars usan layer core/mid/outer
    "CORE": 0.12,
    "MID": 0.45,
    "OUTER": 0.80,
}
_DEFAULT_RADIUS = 0.55

# σ CONSTANTE (fracción del lado): la CAPA decide el RADIO, no el tamaño de la
# huella. Antes σ escalaba con la capa y los nodos OUTER inflaban área → el área
# pintada mentía sobre la masa (§2). Ahora todos los nodos tienen la misma huella.
_SIGMA_FRAC = 0.045


@dataclass(frozen=True)
class Node:
    """Un nodo del universo, reducido a lo que la proyección necesita (§4)."""

    id: str
    domain: str
    tier: str
    mass: float


@dataclass
class DensityField:
    """El resultado de la proyección: campos CONTINUOS, nada discreto (§9c).

    Deliberadamente NO guarda la lista de nodos ni conteo alguno: la salida es
    el campo. Lo único que se puede leer son rejillas continuas.
    """

    size: int
    density: np.ndarray  # (size, size) float — masa acumulada
    color: np.ndarray    # (size, size, 3) float 0..1 — color mezclado por masa


# --- Mapeos deterministas (hash estable, NO hash() de Python) --------------

def _stable_unit(s: str) -> float:
    """Float determinista en [0,1) a partir de un string (md5)."""
    h = int(hashlib.md5(s.encode("utf-8")).hexdigest(), 16)
    return (h % 10_000_019) / 10_000_019.0


def _domain_angle(domain: str) -> float:
    return _stable_unit("angle:" + (domain or "unknown")) * 2.0 * math.pi


def _domain_rgb(domain: str) -> Tuple[float, float, float]:
    hue = _stable_unit("hue:" + (domain or "unknown"))
    return colorsys.hsv_to_rgb(hue, 0.85, 1.0)


def _tier_radius(tier: str) -> float:
    return _TIER_RADIUS.get((tier or "").upper(), _DEFAULT_RADIUS)


def _node_angles(nodes: Sequence[Node]) -> List[float]:
    """Ángulo de cada nodo repartiendo cada DOMINIO en un ARCO de ancho ∝ su nº
    de nodos (no un ángulo único). Así el ÁREA que pinta un dominio es ~∝ su nº
    de nodos —y por tanto a su masa—, no a la geometría (§2: el área no puede
    mentir sobre el estado). Determinista: arcos ordenados por hash estable y
    nodos repartidos uniformemente dentro de su arco.
    """
    counts: Dict[str, int] = {}
    for n in nodes:
        counts[n.domain] = counts.get(n.domain, 0) + 1
    total = sum(counts.values())
    if total <= 0:
        return [0.0 for _ in nodes]
    order = sorted(counts, key=lambda d: _stable_unit("arc:" + (d or "unknown")))
    start: Dict[str, float] = {}
    cursor = 0.0
    for d in order:
        start[d] = cursor
        cursor += 2.0 * math.pi * counts[d] / total
    seen: Dict[str, int] = {}
    out: List[float] = []
    for n in nodes:
        d = n.domain
        i = seen.get(d, 0)
        seen[d] = i + 1
        width = 2.0 * math.pi * counts[d] / total
        out.append(start[d] + (i + 0.5) / counts[d] * width)
    return out


# --- La proyección (núcleo) -------------------------------------------------

# §3 (contorno): el depósito gaussiano se corta a `sigma_cut`·σ (fuera → 0) y,
# tras acumular, se pone a cero por debajo de `floor_frac`·máximo. Ambos son
# parámetros de FORMA y viven en la capa de estilo §8 (core.presence.style),
# no aquí — la proyección solo los consume.


def project(
    nodes: Sequence[Node],
    size: Optional[int] = None,
    sigma_cut: Optional[float] = None,
    floor_frac: Optional[float] = None,
) -> DensityField:
    """Reduce el universo a un campo continuo de densidad + color (§4 pasos 1-2).

    Función pura y determinista: iteramos los nodos en orden estable (por id) y
    acumulamos gaussianas cortadas a `sigma_cut`·σ y NORMALIZADAS a integral
    unidad (cada nodo deposita su masa total, sin importar σ). Tras acumular se
    aplica el umbral de contorno (`floor_frac`·máximo → 0). El color es la media
    ponderada por masa donde queda densidad.

    Los parámetros de FORMA (`size`, `sigma_cut`, `floor_frac`) NO se derivan de
    los datos: si no se pasan, se toman de la capa de estilo §8
    (`core.presence.style.DEFAULT_STYLE`).
    """
    if size is None:
        size = DEFAULT_STYLE.grid
    if sigma_cut is None:
        sigma_cut = DEFAULT_STYLE.sigma_cut
    if floor_frac is None:
        floor_frac = DEFAULT_STYLE.floor_frac

    density = np.zeros((size, size), dtype=np.float64)
    color_num = np.zeros((size, size, 3), dtype=np.float64)

    center = (size - 1) / 2.0
    half = size / 2.0
    yy, xx = np.mgrid[0:size, 0:size]

    nodes_sorted = sorted(nodes, key=lambda z: str(z.id))
    angles = _node_angles(nodes_sorted)
    # σ CONSTANTE: la capa decide el RADIO, no el tamaño de la huella (§2).
    sigma = max(2.0, _SIGMA_FRAC * size)
    cut2 = (sigma_cut * sigma) ** 2

    for idx, n in enumerate(nodes_sorted):
        mass = max(float(n.mass), 0.0)
        if mass <= 0.0:
            continue
        r_frac = _tier_radius(n.tier)          # capa → RADIO
        ang = angles[idx]                      # ángulo dentro del arco del dominio
        cx = center + math.cos(ang) * r_frac * half * 0.9
        cy = center + math.sin(ang) * r_frac * half * 0.9

        dist2 = ((xx - cx) ** 2) + ((yy - cy) ** 2)
        g = np.exp(-dist2 / (2.0 * sigma * sigma))
        g[dist2 > cut2] = 0.0                   # corte a sigma_cut·σ (sin colas)
        # Kernel a integral unidad: cada nodo deposita `mass` total sin importar σ.
        ksum = float(g.sum())
        if ksum > 0.0:
            g = g / ksum
        deposit = g * mass
        density += deposit

        r, gr, b = _domain_rgb(n.domain)
        color_num[:, :, 0] += deposit * r
        color_num[:, :, 1] += deposit * gr
        color_num[:, :, 2] += deposit * b

    # Contorno con borde SUAVE (soft-knee): en vez de un corte duro por debajo de
    # floor_frac·máximo (que dejaba dientes de sierra), se atenúa con smoothstep
    # → la frontera DECAE, no salta. El soporte lo acota el corte a sigma_cut·σ.
    if floor_frac > 0.0:
        peak = float(density.max())
        if peak > 0.0:
            thr = floor_frac * peak
            x = np.clip(density / thr, 0.0, 1.0)
            density = density * (x * x * (3.0 - 2.0 * x))

    # Mezcla de color por peso de masa donde queda densidad (evita 0/0).
    color = np.zeros_like(color_num)
    mask = density > 0
    for i in range(3):
        color[:, :, i][mask] = color_num[:, :, i][mask] / density[mask]

    return DensityField(size=size, density=density, color=color)


# --- Render del campo (§9 "render"; §2 "no imagen generada") ---------------
# Raster DETERMINISTA del campo (no un modelo generativo). El PNG es continuo:
# no contiene ningún elemento discreto que se pueda contar.

def _encode_png(rgb: np.ndarray) -> bytes:
    """Codifica un array (H,W,3) uint8 a PNG RGB con solo stdlib (zlib)."""
    h, w, _ = rgb.shape
    raw = bytearray()
    row_bytes = rgb.reshape(h, w * 3)
    for y in range(h):
        raw.append(0)  # filtro 0 (None)
        raw.extend(row_bytes[y].tobytes())
    compressed = zlib.compress(bytes(raw), 9)

    def _chunk(typ: bytes, data: bytes) -> bytes:
        out = struct.pack(">I", len(data)) + typ + data
        crc = zlib.crc32(typ + data) & 0xFFFFFFFF
        return out + struct.pack(">I", crc)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit, RGB
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", compressed) + _chunk(b"IEND", b"")


def field_to_png_bytes(field: DensityField) -> bytes:
    """Renderiza el campo a PNG: luminosidad = densidad normalizada, tinte = color."""
    d = field.density
    peak = float(d.max()) if d.size else 0.0
    norm = (d / peak) if peak > 0 else d
    img = field.color * norm[:, :, None]
    rgb8 = np.clip(img * 255.0, 0, 255).astype(np.uint8)
    return _encode_png(rgb8)


# --- Adaptador de datos reales (§3: misma fuente que el universo) ----------

def _rank_normalized(raw: List[Tuple[str, str, str, float]]) -> List[Node]:
    """Sustituye la masa cruda por su PERCENTIL DE RANGO en [0,1] DENTRO de la
    fuente (normalización opción a, por rango). Descarta la unidad y conserva
    la estructura interna (qué nodos pesan más dentro de su fuente). Hazen
    (rank+0.5)/N; orden estable → determinista. El promedio queda ~0.5, de modo
    que la contribución de un dominio al campo es ~proporcional a su NÚMERO de
    nodos (no a su masa bruta) — eso sí es comparable entre dominios.
    """
    n = len(raw)
    if n == 0:
        return []
    masses = np.array([x[3] for x in raw], dtype=np.float64)
    order = np.argsort(masses, kind="stable")  # ascendente, determinista
    ranks = np.empty(n, dtype=np.float64)
    ranks[order] = np.arange(n, dtype=np.float64)
    perc = (ranks + 0.5) / n
    return [
        Node(id=raw[i][0], domain=raw[i][1], tier=raw[i][2], mass=float(perc[i]))
        for i in range(n)
    ]


def nodes_from_universe() -> List[Node]:
    """Une las TRES fuentes del universo visible (§1, §3), cada una
    RANK-NORMALIZADA por separado (normalización a, por rango):

      1. Gravity index — dominios cognitivos/mercado; capa = tier.
      2. User stars / **Materializadas** — capa core/mid/outer; dominio 'materializadas'.
      3. Knowledge stars (tabla `stars`) — capa core/mid/outer; dominio 'knowledge'.

    Cada fuente defensiva por separado; ids con prefijo; misma fuente que el
    universo, sin caché propia (§3); no inventa (§5).
    """
    out: List[Node] = []

    # 1) Gravity index (cognitivo + mercado)
    raw_g: List[Tuple[str, str, str, float]] = []
    try:
        from core.learn.gravity_engine import get_gravity_index

        gi = get_gravity_index()
        try:
            records = gi.all_records()
        except AttributeError:
            records = list(gi._load().values())  # fallback defensivo
        for r in records:
            mass = (
                float(getattr(r, "hits", 0))
                * max(float(getattr(r, "cc_score", 0.0)), 0.01)
                * max(float(getattr(r, "freq", 0.0)), 0.01)
                * float(getattr(r, "decay_factor", 1.0) or 1.0)
            )
            raw_g.append((
                "grav:" + str(getattr(r, "fingerprint", "")),
                str(getattr(r, "domain", "") or "unknown"),
                str(getattr(r, "tier", "") or ""),
                mass,
            ))
    except Exception:
        pass
    out.extend(_rank_normalized(raw_g))

    # 2) User stars — Materializadas
    raw_u: List[Tuple[str, str, str, float]] = []
    try:
        from vectrax.db import get_all_user_stars

        for s in get_all_user_stars():
            raw_u.append((
                "user:" + str(getattr(s, "user_id", "")),
                "materializadas",
                str(getattr(s, "layer", "") or "outer"),
                float(getattr(s, "mass", 0.0) or 0.0),
            ))
    except Exception:
        pass
    out.extend(_rank_normalized(raw_u))

    # 3) Knowledge stars (tabla stars)
    raw_k: List[Tuple[str, str, str, float]] = []
    try:
        from vectrax.db import get_all_stars

        for s in get_all_stars():
            m = float(getattr(s, "mass", 0.0) or 0.0)
            if m <= 0.0:
                m = float(getattr(s, "gravity_score", 0.0) or 0.0)
            raw_k.append((
                "know:" + str(getattr(s, "id", "")),
                "knowledge",
                str(getattr(s, "layer", "") or "outer"),
                m,
            ))
    except Exception:
        pass
    out.extend(_rank_normalized(raw_k))

    return out


def render_current_field_png() -> bytes:
    """Proyecta el universo actual y devuelve el PNG del campo (para el canal)."""
    return field_to_png_bytes(project(nodes_from_universe()))
