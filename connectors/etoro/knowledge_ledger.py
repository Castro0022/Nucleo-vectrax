"""
connectors/etoro/knowledge_ledger.py — Conocimiento técnico por señal, aparte.

Mismo patrón que `core/learn/verification_ledger.py` (que ya usa este mismo
dominio vía `verification_cycle.py`): un archivo JSONL APPEND-ONLY en el
vault, nunca reescrito por completo, con caché de lectura invalidada por
mtime. Existe porque `signal_recorder.update_signal()` SÍ reescribe el
archivo entero en cada llamada (por diseño, bajo la premisa explícita de
que ese archivo es pequeño) — una instantánea de conocimiento técnico
(~24KB, 5 timeframes) por señal rompería esa premisa y volvería el backfill
cuadrático en I/O. Aquí se separa: `MarketSignal` sigue siendo pequeña y
mutable; el conocimiento técnico vive en su propio archivo, unido por
`signal_id` en tiempo de lectura — igual que ya se une `Outcome.prediction_id`
con `signal_id` en `verification_cycle.py`.

Append-only real: `record_features` NUNCA reescribe una línea existente. Si
una señal se recalcula (backfill con `force=True`), se escribe una línea
NUEVA; la lectura (`get_features`) siempre devuelve la más reciente para ese
`signal_id` — el historial de recálculos queda preservado, no se pierde.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("vectrax.etoro.knowledge_ledger")

_lock = threading.Lock()

# Caché de lectura: {signal_id: última entrada}, invalidada por mtime del
# archivo — mismo mecanismo exacto que `verification_ledger._load_cache`.
_load_cache_lock = threading.Lock()
_load_cache: Dict[str, tuple] = {}  # path -> (mtime, {signal_id: entry})


def _vault_dir() -> str:
    return os.environ.get(
        "VECTRAX_VAULT_DIR",
        os.path.join(os.path.expanduser("~"), "Vectrax", "vault"),
    )


def _dir() -> str:
    return os.path.join(_vault_dir(), "market_knowledge")


def _path() -> str:
    return os.path.join(_dir(), "features.jsonl")


# ── Escritura (append-only, nunca reescribe) ──────────────────────────

def record_features(
    signal_id: str,
    features: Dict[str, Any],
    computed_at: Optional[float] = None,
) -> bool:
    """Adjunta una instantánea de conocimiento técnico a `signal_id`.

    Append puro — nunca busca ni modifica una línea existente. Nunca lanza;
    devuelve True si escribió."""
    if not signal_id:
        return False
    try:
        entry = {
            "signal_id": signal_id,
            "computed_at": computed_at if computed_at is not None else time.time(),
            "features": features,
        }
        with _lock:
            os.makedirs(_dir(), exist_ok=True)
            with open(_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except Exception as exc:
        logger.warning("knowledge_ledger record failed for %s: %s", signal_id, exc)
        return False


# ── Lectura ────────────────────────────────────────────────────────────

def _load_latest_by_signal() -> Dict[str, Dict[str, Any]]:
    """Última entrada por `signal_id`, servida desde caché si el archivo no
    cambió desde la última lectura (mismo patrón que
    `verification_ledger._load_all_outcomes_cached`)."""
    path = _path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        with _load_cache_lock:
            _load_cache.pop(path, None)
        return {}

    with _load_cache_lock:
        cached = _load_cache.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]

    latest: Dict[str, Dict[str, Any]] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    sid = entry.get("signal_id")
                    if sid:
                        latest[sid] = entry  # la última línea de este id gana
                except Exception:
                    continue
    except Exception as exc:
        logger.debug("knowledge_ledger load failed: %s", exc)

    with _load_cache_lock:
        _load_cache[path] = (mtime, latest)
    return latest


def get_features(signal_id: str) -> Optional[Dict[str, Any]]:
    """Última instantánea de conocimiento técnico de `signal_id`, o None si
    nunca se calculó. Es el punto de unión con `MarketSignal` — por
    `signal_id`, en tiempo de lectura, nunca embebido en la señal misma."""
    return _load_latest_by_signal().get(signal_id)


def has_features(signal_id: str) -> bool:
    """Para el backfill: ¿esta señal ya tiene conocimiento adjunto?"""
    return signal_id in _load_latest_by_signal()


def clear() -> None:
    """Solo para tests / reinicio controlado."""
    try:
        path = _path()
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
    with _load_cache_lock:
        _load_cache.pop(_path(), None)
