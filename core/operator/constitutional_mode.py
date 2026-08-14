"""
Vectrax — Bandera global del Filtro Constitucional
=====================================================
Modo único, global, con reversión inmediata sin reiniciar el proceso.

    shadow  — (default) el filtro evalúa y registra en el ledger, pero
              NUNCA altera el comportamiento del pipeline.
    enforce — el filtro gatea de verdad (BLOCK detiene, CAUTION pasa por
              DecisionAuthority real, PASS continúa).

La transición shadow -> enforce es SIEMPRE manual y explícita (ver
`core.operator.constitutional_readiness`). Este módulo solo expone el
mecanismo de lectura/escritura del flag — nunca decide por sí mismo cuándo
cambiar de modo.

Persistencia: archivo `~/.vectrax/constitutional_mode.json`, mismo patrón
que `~/.vectrax/etoro_auto_config.json`. Se relee (con cache TTL corto) en
cada evaluación, así que revertir a shadow es inmediato — no requiere
reiniciar el proceso.

Creado: 2026-08-14
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict

logger = logging.getLogger("vectrax.operator.constitutional_mode")

SHADOW = "shadow"
ENFORCE = "enforce"
_VALID_MODES = (SHADOW, ENFORCE)

_MODE_PATH = os.path.join(os.path.expanduser("~"), ".vectrax", "constitutional_mode.json")
_CACHE_TTL_S = 5.0

_cache: Dict[str, Any] = {"mode": None, "loaded_at": 0.0}


def _default_state() -> Dict[str, Any]:
    return {
        "mode": SHADOW,
        "changed_at": datetime.now(timezone.utc).isoformat(),
        "changed_by": "default",
    }


def _read_raw() -> Dict[str, Any]:
    try:
        with open(_MODE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("mode") not in _VALID_MODES:
            logger.warning(
                "constitutional_mode.json tiene mode inválido (%r) — fail-safe a shadow",
                data.get("mode"),
            )
            return _default_state()
        return data
    except FileNotFoundError:
        return _default_state()
    except Exception as exc:
        # Fail-safe: cualquier problema leyendo el flag -> shadow, nunca enforce.
        logger.warning("constitutional_mode.json ilegible (%s) — fail-safe a shadow", exc)
        return _default_state()


def get_mode() -> str:
    """Modo actual ('shadow' o 'enforce'). Cache corto; fail-safe a shadow."""
    now = time.time()
    if _cache["mode"] is not None and (now - _cache["loaded_at"]) < _CACHE_TTL_S:
        return _cache["mode"]
    state = _read_raw()
    mode = state.get("mode", SHADOW)
    if mode not in _VALID_MODES:
        mode = SHADOW
    _cache["mode"] = mode
    _cache["loaded_at"] = now
    return mode


def is_shadow() -> bool:
    return get_mode() == SHADOW


def is_enforce() -> bool:
    return get_mode() == ENFORCE


def set_mode(mode: str, *, changed_by: str = "unknown") -> Dict[str, Any]:
    """
    Cambia el modo global. Escritura explícita únicamente — nunca se llama
    automáticamente desde el pipeline. Devuelve el nuevo estado persistido.
    """
    if mode not in _VALID_MODES:
        raise ValueError(f"Modo inválido: {mode!r} (válidos: {_VALID_MODES})")
    state = {
        "mode": mode,
        "changed_at": datetime.now(timezone.utc).isoformat(),
        "changed_by": changed_by,
    }
    os.makedirs(os.path.dirname(_MODE_PATH), exist_ok=True)
    tmp = f"{_MODE_PATH}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _MODE_PATH)
    _cache["mode"] = mode
    _cache["loaded_at"] = time.time()
    logger.warning(
        "CONSTITUTIONAL MODE CHANGE: %s -> %s (by=%s)", "?", mode, changed_by,
    )
    return state


def revert_to_shadow(*, changed_by: str = "kill_switch") -> Dict[str, Any]:
    """Bandera de reversión inmediata — siempre disponible, sin reiniciar el proceso."""
    return set_mode(SHADOW, changed_by=changed_by)


def get_state() -> Dict[str, Any]:
    """Estado completo persistido (mode, changed_at, changed_by), para /v1 o reportes."""
    return _read_raw()
