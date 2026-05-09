"""
core/self_observation/behavior_diff.py — diff vs baseline.

Compara un SystemSnapshot actual contra una baseline persistida y
devuelve los cambios significativos. Esto le permite a Baytracks
percibir lo que cambió entre dos momentos: qué se degradó, qué se
estabilizó, qué creció.

API pública:
    save_baseline(snap)        persiste el snapshot como baseline
    load_baseline()            lee el último baseline
    diff(current, baseline)    devuelve dict con cambios
    diff_against_baseline()    convenience: collect + load + diff

La baseline se guarda en `~/.vectrax/baseline_snapshot.json`. Si no
existe, todo es "primer arranque" y los diffs son ceros.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict
from typing import Any, Dict, Optional

from core.self_observation.state_collector import (
    SystemSnapshot,
    collect_state,
)

logger = logging.getLogger("vectrax.self_observation.diff")


_BASELINE_PATH = os.environ.get(
    "VECTRAX_BASELINE_SNAPSHOT",
    os.path.join(
        os.path.expanduser("~"),
        ".vectrax", "baseline_snapshot.json",
    ),
)


def save_baseline(snap: SystemSnapshot, path: Optional[str] = None) -> bool:
    """Persiste el snapshot como baseline. Devuelve True si OK."""
    p = path or _BASELINE_PATH
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(asdict(snap), f, ensure_ascii=False, indent=2)
        return True
    except Exception as exc:
        logger.debug("save_baseline failed: %s", exc)
        return False


def load_baseline(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Lee el último baseline persistido. None si no existe."""
    p = path or _BASELINE_PATH
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def diff(
    current: SystemSnapshot,
    baseline: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Calcula el diff entre `current` y la `baseline` cargada.

    Devuelve dict con campos:
      - elapsed_seconds:        cuánto tiempo pasó desde la baseline
      - queue_pending_delta:    +N pendientes vs baseline
      - error_rate_delta:       +N errores en 24h
      - deep_memory_delta:      +N records totales
      - identities_delta:       +N identidades de contexto
      - essentials_delta:       +N principios esenciales
      - audio_mode_changed:     ('voice' → 'audio')
      - sovereignty_mode_changed: idem
      - significant_changes:    list[str] resumen humano
    """
    out: Dict[str, Any] = {
        "elapsed_seconds": None,
        "queue_pending_delta": 0,
        "error_rate_delta": 0,
        "deep_memory_delta": 0,
        "identities_delta": 0,
        "essentials_delta": 0,
        "audio_mode_changed": None,
        "sovereignty_mode_changed": None,
        "continuity_processed_delta": 0,
        "significant_changes": [],
    }
    if not baseline:
        out["significant_changes"].append(
            "Sin baseline previo (primer snapshot del ciclo)."
        )
        return out

    elapsed = current.timestamp - float(baseline.get("timestamp") or 0)
    out["elapsed_seconds"] = max(0, int(elapsed))

    qp = current.queue_pending - int(baseline.get("queue_pending") or 0)
    out["queue_pending_delta"] = qp

    er = (
        current.recent_error_count_24h
        - int(baseline.get("recent_error_count_24h") or 0)
    )
    out["error_rate_delta"] = er

    dm = current.deep_memory_count - int(baseline.get("deep_memory_count") or 0)
    out["deep_memory_delta"] = dm

    idd = (
        current.context_identities_count
        - int(baseline.get("context_identities_count") or 0)
    )
    out["identities_delta"] = idd

    es = (
        current.essential_memories_count
        - int(baseline.get("essential_memories_count") or 0)
    )
    out["essentials_delta"] = es

    cd = (
        current.continuity_processed_updates
        - int(baseline.get("continuity_processed_updates") or 0)
    )
    out["continuity_processed_delta"] = cd

    base_audio = baseline.get("audio_mode") or ""
    if current.audio_mode and current.audio_mode != base_audio:
        out["audio_mode_changed"] = (base_audio, current.audio_mode)

    base_sov = baseline.get("sovereignty_mode") or ""
    if current.sovereignty_mode and current.sovereignty_mode != base_sov:
        out["sovereignty_mode_changed"] = (base_sov, current.sovereignty_mode)

    # Composición humana
    sig = []
    if qp > 5:
        sig.append(f"cola creció +{qp}")
    elif qp < -5:
        sig.append(f"cola bajó {qp}")
    if er > 5:
        sig.append(f"errores 24h subieron +{er}")
    elif er < -5:
        sig.append(f"errores 24h bajaron {er}")
    if idd > 0:
        sig.append(f"+{idd} identidades de contexto")
    if es > 0:
        sig.append(f"+{es} principios esenciales")
    if out["audio_mode_changed"]:
        sig.append(
            f"audio mode {out['audio_mode_changed'][0]} → "
            f"{out['audio_mode_changed'][1]}"
        )
    if out["sovereignty_mode_changed"]:
        sig.append(
            f"sovereignty {out['sovereignty_mode_changed'][0]} → "
            f"{out['sovereignty_mode_changed'][1]}"
        )
    if cd > 50:
        sig.append(f"+{cd} updates procesados (continuity)")
    out["significant_changes"] = sig
    return out


def diff_against_baseline() -> Dict[str, Any]:
    """Convenience: collect_state + load_baseline + diff."""
    current = collect_state()
    baseline = load_baseline()
    return diff(current, baseline)
