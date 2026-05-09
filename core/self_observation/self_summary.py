"""
core/self_observation/self_summary.py — composición final de la
percepción para inyectar al prompt del LLM.

Toma la salida de operational_reflection.reflect_now() y la convierte
en un bloque de texto compacto, en español, que se prepended al
contexto del LLM cuando el user es el creador.

API pública:
    compose_self_summary() -> str
    compose_self_summary_for_prompt(max_chars=1200) -> str

Forma:
    [PERCEPCIÓN OPERACIONAL — núcleo Vectrax]
    Estabilidad: ...
    Memoria: ...
    Modos: ...
    Hoy cambió: ...
    Resuelto: ...
    Inestable: ...
    Patrones: ...
    Módulos vivos: ...
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from core.self_observation.operational_reflection import reflect_now

logger = logging.getLogger("vectrax.self_observation.summary")

# === TTL cache para evitar 5+ subprocess git por mensaje ====================
# reflect_now() corre git log + git show por commit + 3 SQLite scans + log
# parse en cada call. Por mensaje del creador eso suma 80-300ms cold.
# El estado del sistema cambia raramente entre mensajes consecutivos, así
# que cacheamos la composición por VECTRAX_SELF_SUMMARY_TTL_S segundos
# (default 60s). Threadsafe via Lock.
_CACHE_TTL = float(os.environ.get("VECTRAX_SELF_SUMMARY_TTL_S", "60"))
_cache: Dict[str, tuple] = {}   # key -> (value, expires_at)
_cache_lock = threading.Lock()


def invalidate_cache() -> None:
    """Test/utility: limpia el cache. Útil tras un deploy importante."""
    with _cache_lock:
        _cache.clear()


def _bullets(lines: List[str], max_lines: int = 5) -> str:
    if not lines:
        return "—"
    out = lines[: max(1, int(max_lines))]
    return "\n  · " + "\n  · ".join(s.strip() for s in out if s.strip())


def compose_self_summary(
    reflection: Optional[Dict[str, Any]] = None,
    use_cache: bool = True,
) -> str:
    """Devuelve un bloque compacto en español. Defensive.

    `use_cache=True` (default): respeta TTL cache (~60s). Para tests o
    para forzar recompute, pasar use_cache=False.
    Si `reflection` se pasa explícitamente, ese path no usa cache.
    """
    # Cache hit (solo cuando NO se pasa una reflection externa)
    if reflection is None and use_cache:
        with _cache_lock:
            entry = _cache.get("compose")
            if entry and entry[1] > time.time():
                return entry[0]

    try:
        r = reflection if reflection is not None else reflect_now()
    except Exception as exc:
        logger.debug("reflect_now failed: %s", exc)
        return ""

    head = ""
    if r.get("current_branch") or r.get("current_head"):
        head = (
            f" · branch={r.get('current_branch') or '?'}"
            f" head={r.get('current_head') or '?'}"
        )

    parts = [
        f"[PERCEPCIÓN OPERACIONAL — núcleo Vectrax]{head}",
        f"Estabilidad: {r.get('stability_line', '')}",
        f"Memoria:     {r.get('memory_line', '')}",
        f"Modos:       {r.get('modes_line', '')}",
        f"Hoy cambió: {_bullets(r.get('what_changed_today') or [], 5)}",
        f"Resuelto:   {_bullets(r.get('problems_solved') or [], 5)}",
        f"Inestable:  {_bullets(r.get('still_unstable') or [], 5)}",
        f"Patrones:   {_bullets(r.get('patterns_detected') or [], 5)}",
        f"Módulos:    {_bullets(r.get('modules_evolved') or [], 6)}",
    ]
    out = "\n".join(parts)

    # Persistir en cache si fue computed (no fue pasado por el caller)
    if reflection is None and use_cache:
        with _cache_lock:
            _cache["compose"] = (out, time.time() + _CACHE_TTL)

    return out


def compose_self_summary_for_prompt(max_chars: int = 1200) -> str:
    """Versión truncada para inyectar al prompt sin inflar el context.

    Si la salida supera max_chars, recorta al último \\n natural antes
    del límite. Devuelve cadena vacía si la composición falla.
    """
    s = compose_self_summary()
    if not s:
        return ""
    if len(s) <= max_chars:
        return s
    cut = s[: max_chars]
    last_nl = cut.rfind("\n")
    if last_nl > max_chars // 2:
        return cut[: last_nl].rstrip()
    return cut.rstrip()
