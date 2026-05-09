"""
core/self_observation/operational_reflection.py — reflexión operacional.

Interpreta el estado, los commits recientes y los diffs en lenguaje
estructurado (NO logs, NO métricas crudas). Lo que produce este módulo
es PERCEPCIÓN, no telemetría.

Ejemplos de reflexión:
  - "Hoy se afinó el motor de audio. Voice messages quedaron forbidden
    en este chat; volvimos a sendAudio."
  - "La cola está vacía y el worker responde en ms."
  - "Errores 24h: 0. Estabilidad alta."
  - "Memoria gravitacional: 142 records, 8 identidades, 3 principios."
  - "Hilo activo: revisión del sendVoice → sendAudio fallback."

Forma deliberadamente humana: 3-7 líneas, sin jerga interna.

API pública:
    reflect(snap, diff, deploys) -> dict (categorias)
    reflect_now() -> dict (collect + diff + reflect)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from core.self_observation.behavior_diff import diff_against_baseline
from core.self_observation.deployment_memory import deploy_summary
from core.self_observation.state_collector import (
    SystemSnapshot,
    collect_state,
)

logger = logging.getLogger("vectrax.self_observation.reflect")


# ===========================================================================
# Categories de reflexión
# ===========================================================================

def _stability(snap: SystemSnapshot) -> str:
    """Línea breve sobre estabilidad."""
    parts = []
    if snap.gateway_heartbeat_age_s is not None:
        parts.append(
            f"gateway hb {int(snap.gateway_heartbeat_age_s)}s"
        )
    if snap.worker_heartbeat_age_s is not None:
        parts.append(
            f"worker hb {int(snap.worker_heartbeat_age_s)}s"
        )
    if snap.recent_error_count_24h:
        parts.append(f"errores 24h: {snap.recent_error_count_24h}")
    else:
        parts.append("errores 24h: 0")
    return " · ".join(parts)


def _memory(snap: SystemSnapshot) -> str:
    return (
        f"deep_memory: {snap.deep_memory_count} totales "
        f"({snap.deep_memory_active} activos, "
        f"{snap.deep_memory_fused} fusionados, "
        f"{snap.deep_memory_archived} archivados) · "
        f"{snap.context_identities_count} identidades · "
        f"{snap.essential_memories_count} principios esenciales"
    )


def _modes_line(snap: SystemSnapshot) -> str:
    bits = [
        f"audio={snap.audio_mode or '?'}",
        f"sovereignty={snap.sovereignty_mode or '?'}",
        f"proactive={'on' if snap.proactive_engine_enabled else 'off'}",
    ]
    return " · ".join(bits)


def _what_changed_today(commits: List[Dict[str, Any]]) -> List[str]:
    """Compone líneas humanas sobre los commits recientes."""
    if not commits:
        return ["Sin commits recientes en el repo."]
    out = []
    for c in commits[:5]:
        out.append(f"{c['sha']} · {c['subject']}")
    return out


def _patterns_detected(snap: SystemSnapshot, diff: Dict[str, Any]) -> List[str]:
    """Patrones que el sistema percibe ahora."""
    out: List[str] = []
    if snap.queue_pending > 5:
        out.append(f"cola con {snap.queue_pending} pending — presión moderada")
    if snap.recent_error_count_24h > 20:
        out.append(
            f"tasa de errores alta ({snap.recent_error_count_24h}/24h) — "
            "vigilar"
        )
    if snap.context_identities_count and snap.essential_memories_count:
        out.append(
            "memoria significativa activa: hay identidades de contexto y "
            "principios esenciales destilados"
        )
    if diff.get("audio_mode_changed"):
        a, b = diff["audio_mode_changed"]
        out.append(f"modo audio cambió {a} → {b}")
    if diff.get("error_rate_delta", 0) < -5:
        out.append("la tasa de errores bajó respecto al snapshot previo")
    return out


# ===========================================================================
# Public entrypoint
# ===========================================================================

def reflect(
    snap: SystemSnapshot,
    diff: Dict[str, Any],
    deploys: Dict[str, Any],
) -> Dict[str, Any]:
    """Compone una reflexión estructurada sobre el sistema.

    Devuelve dict con:
      what_changed_today: list[str]
      problems_solved:    list[str]
      still_unstable:     list[str]
      patterns_detected:  list[str]
      modules_evolved:    list[str]
      stability_line:     str
      memory_line:        str
      modes_line:         str
    """
    commits = deploys.get("recent_commits") or []
    modules = deploys.get("modified_modules") or []

    # Heurística simple: si subject empieza con 'fix:', cuenta como problema resuelto.
    fixes = [c for c in commits if c.get("subject", "").lower().startswith("fix")]
    feats = [c for c in commits if c.get("subject", "").lower().startswith("feat")]

    problems_solved = [
        f"{c['sha']} · {c['subject']}" for c in fixes[:5]
    ] or ["Sin fixes registrados en el último ciclo."]

    still_unstable: List[str] = []
    if snap.recent_error_count_24h > 0:
        still_unstable.append(
            f"hay {snap.recent_error_count_24h} eventos de error/warning en 24h"
        )
    if snap.queue_processing > 1:
        still_unstable.append(
            f"{snap.queue_processing} mensajes en processing"
        )
    if not still_unstable:
        still_unstable.append("nada significativo pendiente.")

    return {
        "what_changed_today": _what_changed_today(commits),
        "problems_solved": problems_solved,
        "still_unstable": still_unstable,
        "patterns_detected": _patterns_detected(snap, diff),
        "modules_evolved": modules[:8] if modules else [
            "ningún módulo modificado en la última semana.",
        ],
        "stability_line": _stability(snap),
        "memory_line": _memory(snap),
        "modes_line": _modes_line(snap),
        "current_head": deploys.get("current_head", ""),
        "current_branch": deploys.get("current_branch", ""),
    }


def reflect_now() -> Dict[str, Any]:
    """Convenience: collect_state + diff + deploy_summary + reflect."""
    snap = collect_state()
    diff_data = diff_against_baseline()
    deploys = deploy_summary()
    return reflect(snap, diff_data, deploys)
