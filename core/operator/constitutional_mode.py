"""
Vectrax — Bandera global del Filtro Constitucional
=====================================================
Modo único, global, con cambio inmediato sin reiniciar el proceso.

    active — (default) el filtro GATEA de verdad: BLOCK detiene la acción y
             explica la causa, CAUTION pasa por `DecisionAuthority`, PASS
             continúa.
    paused — parada de emergencia EXPLÍCITA: las acciones sujetas al control
             se rechazan diciendo que el control está pausado.

NO EXISTE UN MODO DE OBSERVACIÓN
--------------------------------
Antes había un modo `shadow` en el que el filtro evaluaba, registraba y no
hacía nada con el resultado. Un control que se consulta y se descarta no es un
control: es un registro que aparenta serlo. Se retiró junto con
`shadow_check()`.

FALLA CERRADO
-------------
Cualquier problema leyendo la bandera —archivo corrupto, valor desconocido,
error de E/S— resuelve a `paused`, no a "seguir como si nada". Antes el
fail-safe era `shadow`, que en la práctica significaba dejar pasar todo sin
control mientras nadie miraba el log. Pausar es ruidoso y detiene; degradar a
observación es silencioso y no detiene.

`paused` NO es una puerta abierta: una acción sujeta al control se rechaza
mientras dure la pausa. Esa es la diferencia con el modo que se retiró.

Persistencia: archivo `~/.vectrax/constitutional_mode.json`, mismo patrón que
`~/.vectrax/etoro_auto_config.json`. Se relee con cache TTL corto en cada
evaluación, así que pausar o reanudar es inmediato.

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

ACTIVE = "active"
PAUSED = "paused"
_VALID_MODES = (ACTIVE, PAUSED)

#: Modo por defecto cuando no hay archivo: el control está ENCENDIDO. Un
#: control que nace apagado es un control que nadie enciende.
DEFAULT_MODE = ACTIVE

#: Modo al que se resuelve cualquier fallo de lectura. Falla cerrado.
FAILSAFE_MODE = PAUSED

_MODE_PATH = os.path.join(os.path.expanduser("~"), ".vectrax", "constitutional_mode.json")
_CACHE_TTL_S = 5.0

_cache: Dict[str, Any] = {"mode": None, "loaded_at": 0.0}


def _default_state() -> Dict[str, Any]:
    return {
        "mode": DEFAULT_MODE,
        "changed_at": datetime.now(timezone.utc).isoformat(),
        "changed_by": "default",
        "reason": "sin archivo de estado: el control nace activo",
    }


def _failsafe_state(reason: str) -> Dict[str, Any]:
    return {
        "mode": FAILSAFE_MODE,
        "changed_at": datetime.now(timezone.utc).isoformat(),
        "changed_by": "failsafe",
        "reason": reason,
    }


def _read_raw() -> Dict[str, Any]:
    try:
        with open(_MODE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        # Ausencia de archivo no es un fallo: es el estado inicial.
        return _default_state()
    except Exception as exc:
        logger.error(
            "constitutional_mode.json ilegible (%s) — PAUSA de seguridad", exc,
        )
        return _failsafe_state(f"archivo ilegible: {exc}")

    if data.get("mode") not in _VALID_MODES:
        logger.error(
            "constitutional_mode.json tiene mode inválido (%r) — PAUSA de seguridad",
            data.get("mode"),
        )
        return _failsafe_state(f"mode inválido: {data.get('mode')!r}")
    return data


def get_mode() -> str:
    """Modo actual ('active' o 'paused'). Cache corto; falla a 'paused'."""
    now = time.time()
    if _cache["mode"] is not None and (now - _cache["loaded_at"]) < _CACHE_TTL_S:
        return _cache["mode"]
    mode = _read_raw().get("mode", FAILSAFE_MODE)
    if mode not in _VALID_MODES:
        mode = FAILSAFE_MODE
    _cache["mode"] = mode
    _cache["loaded_at"] = now
    return mode


def is_active() -> bool:
    return get_mode() == ACTIVE


def is_paused() -> bool:
    return get_mode() == PAUSED


def set_mode(mode: str, *, changed_by: str = "unknown", reason: str = "") -> Dict[str, Any]:
    """
    Cambia el modo global. Escritura explícita únicamente — nunca se llama
    automáticamente desde el pipeline. Devuelve el nuevo estado persistido.
    """
    if mode not in _VALID_MODES:
        raise ValueError(f"Modo inválido: {mode!r} (válidos: {_VALID_MODES})")
    previous = get_mode()
    state = {
        "mode": mode,
        "changed_at": datetime.now(timezone.utc).isoformat(),
        "changed_by": changed_by,
        "reason": reason,
    }
    os.makedirs(os.path.dirname(_MODE_PATH), exist_ok=True)
    tmp = f"{_MODE_PATH}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _MODE_PATH)
    _cache["mode"] = mode
    _cache["loaded_at"] = time.time()
    logger.warning(
        "CONSTITUTIONAL MODE CHANGE: %s -> %s (by=%s) %s",
        previous, mode, changed_by, reason,
    )
    return state


def pause(*, changed_by: str = "kill_switch", reason: str = "") -> Dict[str, Any]:
    """Parada de emergencia. Detiene las acciones sujetas al control.

    Es el único mecanismo de emergencia: no existe forma de dejar el control
    consultándose sin efecto. Pausar detiene; no abre la puerta.
    """
    return set_mode(PAUSED, changed_by=changed_by, reason=reason or "pausa de emergencia")


def resume(*, changed_by: str = "operator", reason: str = "") -> Dict[str, Any]:
    """Reanuda el control tras una pausa."""
    return set_mode(ACTIVE, changed_by=changed_by, reason=reason or "reanudado")


def get_state() -> Dict[str, Any]:
    """Estado completo persistido (mode, changed_at, changed_by, reason)."""
    return _read_raw()
