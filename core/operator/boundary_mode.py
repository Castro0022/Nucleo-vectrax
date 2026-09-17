"""
Vectrax — Bandera de modo por frontera de ejecución PRE-ejecución
=====================================================================
`core.operator.constitutional_mode` es un flag GLOBAL ÚNICO, usado
exclusivamente por el gate `respond` (`external_gateway.py::_constitutional_gate`).
No soporta modos independientes por frontera y NO debe reutilizarse ni
modificarse para esta tarea — las 4 fronteras de ejecución externa (LLM,
Online, Places, Market) deben promoverse de SHADOW a ENFORCE de forma
completamente independiente entre sí.

Este módulo replica el mismo patrón de persistencia de
`constitutional_mode.py` (mismo directorio, fail-safe a shadow, cache TTL
corto, reversión inmediata sin reiniciar el proceso), pero keyed por
boundary.

    shadow  — (default) el pre-gate evalúa y registra en el ledger, pero
              NUNCA altera el comportamiento del executor real.
    enforce — el pre-gate gatea de verdad para ESA frontera (BLOCK detiene,
              CAUTION pasa por DecisionAuthority real, PASS continúa).

Creado: 2026-09-17
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict

logger = logging.getLogger("vectrax.operator.boundary_mode")

SHADOW = "shadow"
ENFORCE = "enforce"
_VALID_MODES = (SHADOW, ENFORCE)

# Las 4 fronteras gobernadas por este PR. Cualquier otro nombre de boundary
# se trata como desconocido y cae fail-safe a shadow (nunca enforce).
BOUNDARIES = ("llm", "online", "places", "market")

_MODE_PATH = os.path.join(
    os.path.expanduser("~"), ".vectrax", "execution_boundary_mode.json",
)
_CACHE_TTL_S = 5.0

_cache: Dict[str, Any] = {"state": None, "loaded_at": 0.0}


def _default_state() -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        boundary: {"mode": SHADOW, "changed_at": now, "changed_by": "default"}
        for boundary in BOUNDARIES
    }


def _read_raw() -> Dict[str, Any]:
    try:
        with open(_MODE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _default_state()
        # Fail-safe por entrada: cualquier boundary con mode inválido o
        # ausente cae a shadow individualmente, sin invalidar las demás.
        state = _default_state()
        for boundary in BOUNDARIES:
            entry = data.get(boundary)
            if isinstance(entry, dict) and entry.get("mode") in _VALID_MODES:
                state[boundary] = entry
        return state
    except FileNotFoundError:
        return _default_state()
    except Exception as exc:
        logger.warning(
            "execution_boundary_mode.json ilegible (%s) — fail-safe a shadow "
            "en las 4 fronteras", exc,
        )
        return _default_state()


def _load_state() -> Dict[str, Any]:
    now = time.time()
    if _cache["state"] is not None and (now - _cache["loaded_at"]) < _CACHE_TTL_S:
        return _cache["state"]
    state = _read_raw()
    _cache["state"] = state
    _cache["loaded_at"] = now
    return state


def get_mode(boundary: str) -> str:
    """Modo actual de `boundary` ('shadow' o 'enforce'). Fail-safe a shadow
    para boundaries desconocidos o estado ilegible."""
    if boundary not in BOUNDARIES:
        logger.warning("boundary_mode.get_mode: boundary desconocido %r — shadow", boundary)
        return SHADOW
    state = _load_state()
    entry = state.get(boundary) or {}
    mode = entry.get("mode", SHADOW)
    return mode if mode in _VALID_MODES else SHADOW


def is_shadow(boundary: str) -> bool:
    return get_mode(boundary) == SHADOW


def is_enforce(boundary: str) -> bool:
    return get_mode(boundary) == ENFORCE


def set_mode(boundary: str, mode: str, *, changed_by: str = "unknown") -> Dict[str, Any]:
    """Cambia el modo de UNA frontera. Escritura explícita únicamente — nunca
    se llama automáticamente desde el pipeline ni desde `authorize()`.
    No afecta a las otras 3 fronteras."""
    if boundary not in BOUNDARIES:
        raise ValueError(f"Boundary inválido: {boundary!r} (válidos: {BOUNDARIES})")
    if mode not in _VALID_MODES:
        raise ValueError(f"Modo inválido: {mode!r} (válidos: {_VALID_MODES})")

    state = _read_raw()
    state[boundary] = {
        "mode": mode,
        "changed_at": datetime.now(timezone.utc).isoformat(),
        "changed_by": changed_by,
    }
    os.makedirs(os.path.dirname(_MODE_PATH), exist_ok=True)
    tmp = f"{_MODE_PATH}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _MODE_PATH)
    _cache["state"] = state
    _cache["loaded_at"] = time.time()
    logger.warning(
        "EXECUTION BOUNDARY MODE CHANGE: %s -> %s (boundary=%s, by=%s)",
        "?", mode, boundary, changed_by,
    )
    return state[boundary]


def revert_to_shadow(boundary: str, *, changed_by: str = "kill_switch") -> Dict[str, Any]:
    """Reversión inmediata de UNA frontera — siempre disponible."""
    return set_mode(boundary, SHADOW, changed_by=changed_by)


def get_state() -> Dict[str, Any]:
    """Estado completo persistido de las 4 fronteras."""
    return _read_raw()
