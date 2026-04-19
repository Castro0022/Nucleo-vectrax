"""
Vectrax — Política de ingesta ARGOS
======================================
ARGOS no es un vertedero. Es un corpus curado. Todo lo que entra pasa por este
filtro y se marca con:

  - origen     : voz, telegram, importación, web, archivo
  - intención  : principio | hecho | nota | referencia | borrador
  - peso       : [0.0, 1.0] — decide proximidad al núcleo gravitacional
  - veredicto  : ACEPTAR | DESCARTAR | CUARENTENA

Reglas duras (sin excepciones):
  1. Nada entra sin (origen, intención).
  2. Archivos del sistema (logs, caches, shell history, paths de SO) → DESCARTAR.
  3. Duplicados semánticos exactos → DESCARTAR con referencia al original.
  4. Ruido conversacional ("ok", "jaja", "gracias") → DESCARTAR.
  5. Todo lo demás → CUARENTENA con peso < 0.30 hasta que una señal explícita
     lo promueva (usuario marca, convergencia gravitacional, uso repetido).

Este módulo es la política. La ingesta real ocurre en la capa ARGOS del nodo
Vultr; aquí solo se decide si aceptar.

Creado: 2026-04-19
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger("vectrax.argos_ingesta")


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------

class Origen(str, Enum):
    VOZ = "voz"
    TELEGRAM = "telegram"
    IMPORTACION = "importacion"
    WEB = "web"
    ARCHIVO = "archivo"
    SISTEMA = "sistema"


class Intencion(str, Enum):
    PRINCIPIO = "principio"       # ley / norma personal
    HECHO = "hecho"               # dato verificable (nombre, cifra, fecha)
    NOTA = "nota"                 # observación, reflexión
    REFERENCIA = "referencia"     # link, cita, documento
    BORRADOR = "borrador"         # ambiguo, requiere revisión


class Veredicto(str, Enum):
    ACEPTAR = "aceptar"
    CUARENTENA = "cuarentena"
    DESCARTAR = "descartar"


@dataclass
class IngestaResult:
    veredicto: Veredicto
    origen: Origen
    intencion: Intencion
    peso: float
    razon: str
    dedup_key: Optional[str] = None  # hash semántico para detección de duplicados

    def to_dict(self) -> dict:
        return {
            "veredicto": self.veredicto.value,
            "origen": self.origen.value,
            "intencion": self.intencion.value,
            "peso": self.peso,
            "razon": self.razon,
            "dedup_key": self.dedup_key,
        }


# ---------------------------------------------------------------------------
# Reglas de descarte (sin excepciones)
# ---------------------------------------------------------------------------

# Rutas del sistema operativo — nunca son conocimiento del usuario
_SYSTEM_PATHS = re.compile(
    r"(?:"
    r"^/(?:System|Library|private|usr|bin|sbin|etc|var|opt|tmp)/"
    r"|^/Users/[^/]+/Library/"
    r"|\.cache|\.npm|\.pyenv|\.venv|\.rbenv|node_modules"
    r"|__pycache__|\.DS_Store|\.pyc$|\.log$"
    r")",
    re.IGNORECASE,
)

# Historial de shell — telemetría, no conocimiento
_SHELL_HISTORY = re.compile(
    r"(?:zsh_history|bash_history|fish_history|\.history)",
    re.IGNORECASE,
)

# Ruido conversacional (complementa intake_filter._NOISE)
_CONVERSATIONAL_NOISE = re.compile(
    r"^(?:ok+|okey|jaja|haha|lol|xd|hmm+|ah+|oh+|s[ií]+|no+|"
    r"gracias|thanks|thx|nada|bien|mal|\.{1,}|\?{1,}|!{1,})\s*$",
    re.IGNORECASE,
)


def _is_system_garbage(content: str, source_path: Optional[str]) -> bool:
    """True si el contenido es claramente basura de sistema."""
    if source_path:
        if _SYSTEM_PATHS.search(source_path):
            return True
        if _SHELL_HISTORY.search(source_path):
            return True
        if os.path.basename(source_path).startswith("."):
            return True
    # Contenido de apariencia log/stack
    if content.count("\n") > 50 and re.search(r"Traceback|ERROR|WARN\b", content):
        return True
    return False


def _is_noise(content: str) -> bool:
    """True si el contenido es ruido conversacional."""
    stripped = content.strip()
    if len(stripped) < 3:
        return True
    if _CONVERSATIONAL_NOISE.match(stripped):
        return True
    # Menos de 3 palabras útiles
    words = [w for w in re.findall(r"\w+", stripped) if len(w) > 1]
    if len(words) < 2:
        return True
    return False


# ---------------------------------------------------------------------------
# Clasificación de intención
# ---------------------------------------------------------------------------

_PRINCIPLE_MARKERS = re.compile(
    r"\b(?:principio|ley|regla|norma|decisi[oó]n|principle|rule|"
    r"never forget|no olvides|recuerda siempre|para siempre)\b",
    re.IGNORECASE,
)

_FACT_MARKERS = re.compile(
    r"(?:me llamo|mi nombre|tengo\s+\d+|vivo en|trabajo en|"
    r"mi (?:correo|email|tel[eé]fono|empresa|cumplea[ñn]os)|"
    r"\b\d{1,3}(?:[.,]\d{3})+\b|\b\d{4}-\d{2}-\d{2}\b)",
    re.IGNORECASE,
)

_REFERENCE_MARKERS = re.compile(
    r"(?:https?://|www\.|\bdoc\b|\barticulo\b|art[ií]culo|paper|referencia)",
    re.IGNORECASE,
)


def _classify_intention(content: str) -> Intencion:
    if _PRINCIPLE_MARKERS.search(content):
        return Intencion.PRINCIPIO
    if _FACT_MARKERS.search(content):
        return Intencion.HECHO
    if _REFERENCE_MARKERS.search(content):
        return Intencion.REFERENCIA
    # Heurística: párrafo corto y coherente = nota
    n_words = len(content.split())
    if 5 <= n_words <= 200:
        return Intencion.NOTA
    return Intencion.BORRADOR


# ---------------------------------------------------------------------------
# Peso inicial
# ---------------------------------------------------------------------------

def _initial_weight(intencion: Intencion, origen: Origen) -> float:
    base = {
        Intencion.PRINCIPIO: 0.75,
        Intencion.HECHO: 0.55,
        Intencion.REFERENCIA: 0.40,
        Intencion.NOTA: 0.30,
        Intencion.BORRADOR: 0.15,
    }[intencion]

    # La voz directa del usuario pesa más que una importación masiva
    if origen == Origen.VOZ or origen == Origen.TELEGRAM:
        base += 0.10
    elif origen == Origen.IMPORTACION:
        base -= 0.10

    return max(0.0, min(1.0, base))


# ---------------------------------------------------------------------------
# Clave de deduplicación semántica (simple, estable)
# ---------------------------------------------------------------------------

def _dedup_key(content: str) -> str:
    import hashlib
    normalized = re.sub(r"\s+", " ", content.strip().lower())
    return hashlib.sha1(normalized.encode("utf-8", "ignore")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def evaluar_ingesta(
    content: str,
    *,
    origen: Origen,
    source_path: Optional[str] = None,
    intencion_hint: Optional[Intencion] = None,
) -> IngestaResult:
    """
    Decide si un fragmento de información puede entrar a ARGOS.

    Args:
        content:        el texto a ingerir.
        origen:         de dónde viene (voz, telegram, importación, …).
        source_path:    ruta de origen si aplica (útil para descartar rutas de SO).
        intencion_hint: pista opcional de intención (p.ej. el usuario marcó "principio").

    Returns:
        IngestaResult con veredicto, intención, peso y razón.
    """
    if not content or not content.strip():
        return IngestaResult(
            veredicto=Veredicto.DESCARTAR,
            origen=origen,
            intencion=Intencion.BORRADOR,
            peso=0.0,
            razon="vacío",
        )

    if _is_system_garbage(content, source_path):
        return IngestaResult(
            veredicto=Veredicto.DESCARTAR,
            origen=origen,
            intencion=Intencion.BORRADOR,
            peso=0.0,
            razon="ruta de sistema o log",
        )

    if _is_noise(content):
        return IngestaResult(
            veredicto=Veredicto.DESCARTAR,
            origen=origen,
            intencion=Intencion.BORRADOR,
            peso=0.0,
            razon="ruido conversacional",
        )

    intencion = intencion_hint or _classify_intention(content)
    peso = _initial_weight(intencion, origen)
    dedup = _dedup_key(content)

    # Borradores siempre van a cuarentena, aunque vengan de voz.
    if intencion == Intencion.BORRADOR or peso < 0.30:
        veredicto = Veredicto.CUARENTENA
        razon = "cuarentena: peso bajo o intención ambigua"
    else:
        veredicto = Veredicto.ACEPTAR
        razon = f"aceptado como {intencion.value}"

    result = IngestaResult(
        veredicto=veredicto,
        origen=origen,
        intencion=intencion,
        peso=peso,
        razon=razon,
        dedup_key=dedup,
    )
    logger.info(
        "Ingesta | veredicto=%s | origen=%s | intencion=%s | peso=%.2f | razon=%s",
        veredicto.value, origen.value, intencion.value, peso, razon,
    )
    return result


def purgar_corpus_callback(existing_paths, *, dry_run: bool = True) -> dict:
    """
    Utilidad para barrer un corpus ARGOS existente y marcar basura.
    NO borra nada por defecto (dry_run=True). Devuelve un resumen.
    """
    kept = 0
    dropped = []
    for p in existing_paths:
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
        except Exception:
            dropped.append((p, "unreadable"))
            continue
        r = evaluar_ingesta(content, origen=Origen.IMPORTACION, source_path=p)
        if r.veredicto == Veredicto.DESCARTAR:
            dropped.append((p, r.razon))
            if not dry_run:
                try:
                    os.remove(p)
                except Exception as exc:
                    logger.warning("No pude eliminar %s: %s", p, exc)
        else:
            kept += 1
    return {
        "kept": kept,
        "dropped_count": len(dropped),
        "dropped_sample": dropped[:20],
        "dry_run": dry_run,
    }
