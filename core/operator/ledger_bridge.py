"""
Vectrax Core — Ledger Bridge
===============================
Puente entre el operador y el audit_ledger existente (core.audit_ledger).
Provee funciones de alto nivel para registrar eventos del operador
con categorización automática y metadatos enriquecidos.

Principio P-003: "Toda acción relevante debe quedar registrada.
Sin registro, no existió."

Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core import audit_ledger

logger = logging.getLogger("vectrax.operator.ledger_bridge")


# ---------------------------------------------------------------------------
# Categorías de eventos del operador
# ---------------------------------------------------------------------------

class EventCategory:
    """Categorías de eventos registrables en el ledger."""
    IDENTITY = "operator.identity"
    NUCLEUS = "operator.nucleus"
    MEMORY = "operator.memory"
    PERCEPTION = "operator.perception"
    REASONING = "operator.reasoning"
    ACTION = "operator.action"
    GOVERNANCE = "operator.governance"
    INTEGRITY = "operator.integrity"
    MODULE = "operator.module"
    BUILD = "operator.build"
    MODE_CHANGE = "operator.mode_change"
    PHASE = "operator.phase"
    CONSTITUTIONAL = "operator.constitutional"


# ---------------------------------------------------------------------------
# Niveles de riesgo para registro
# ---------------------------------------------------------------------------

class RiskZone:
    """Zonas de riesgo para categorización en ledger."""
    GREEN = "green"     # Sin riesgo — operación normal
    YELLOW = "yellow"   # Riesgo moderado — requiere atención
    RED = "red"         # Riesgo alto — requiere autorización


# ---------------------------------------------------------------------------
# Funciones de registro
# ---------------------------------------------------------------------------

def record_event(
    action: str,
    *,
    category: str = EventCategory.NUCLEUS,
    risk_zone: str = RiskZone.GREEN,
    actor: str = "operator",
    reason: str = "",
    details: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Registra un evento del operador en el audit ledger.

    Returns:
        ID del registro en el ledger.
    """
    metadata = {
        "category": category,
        "risk_zone": risk_zone,
        "operator_timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if details:
        metadata["details"] = details

    row_id = audit_ledger.record(
        action=action,
        actor=actor,
        role="owner",
        decision="approved" if risk_zone == RiskZone.GREEN else "pending",
        reason=reason,
        metadata=metadata,
    )

    logger.info(
        "Ledger [%s] %s | zone=%s | id=%d",
        category, action, risk_zone, row_id,
    )
    return row_id


def record_phase_start(phase: int, description: str) -> int:
    """Registra el inicio de una fase de construcción."""
    return record_event(
        action=f"phase_{phase}_start",
        category=EventCategory.PHASE,
        risk_zone=RiskZone.GREEN,
        reason=description,
        details={"phase": phase, "description": description},
    )


def record_phase_complete(phase: int, files_created: List[str]) -> int:
    """Registra la completación de una fase de construcción."""
    return record_event(
        action=f"phase_{phase}_complete",
        category=EventCategory.PHASE,
        risk_zone=RiskZone.GREEN,
        reason=f"Phase {phase} completed with {len(files_created)} files",
        details={"phase": phase, "files_created": files_created},
    )


def record_module_created(module_path: str, layer_id: int, purpose: str) -> int:
    """Registra la creación de un módulo nuevo."""
    return record_event(
        action=f"module_created:{module_path}",
        category=EventCategory.MODULE,
        risk_zone=RiskZone.GREEN,
        reason=purpose,
        details={
            "module": module_path,
            "layer_id": layer_id,
            "purpose": purpose,
        },
    )


def record_integrity_check(result: Dict[str, Any]) -> int:
    """Registra el resultado de una verificación de integridad."""
    zone = RiskZone.GREEN if result.get("healthy") else RiskZone.YELLOW
    return record_event(
        action="integrity_check",
        category=EventCategory.INTEGRITY,
        risk_zone=zone,
        reason=f"healthy={result.get('healthy', 'unknown')}",
        details=result,
    )


def record_mode_change(old_mode: str, new_mode: str, reason: str) -> int:
    """Registra un cambio de modo operativo."""
    return record_event(
        action=f"mode_change:{old_mode}→{new_mode}",
        category=EventCategory.MODE_CHANGE,
        risk_zone=RiskZone.YELLOW,
        reason=reason,
        details={"old_mode": old_mode, "new_mode": new_mode},
    )


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------

def get_operator_events(
    limit: int = 50,
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Consulta eventos del operador en el ledger.
    Filtra por categoría si se especifica.
    """
    all_events = audit_ledger.query(limit=limit * 2)

    if category is None:
        # Filtrar solo eventos del operador
        return [
            e for e in all_events
            if _is_operator_event(e)
        ][:limit]

    return [
        e for e in all_events
        if _event_has_category(e, category)
    ][:limit]


def _is_operator_event(event: Dict[str, Any]) -> bool:
    """Verifica si un evento pertenece al operador."""
    meta = event.get("metadata", "{}")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            return False
    return isinstance(meta, dict) and str(meta.get("category", "")).startswith("operator.")


def _event_has_category(event: Dict[str, Any], category: str) -> bool:
    """Verifica si un evento tiene una categoría específica."""
    meta = event.get("metadata", "{}")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            return False
    return isinstance(meta, dict) and meta.get("category") == category


def get_ledger_stats() -> Dict[str, Any]:
    """Estadísticas del ledger."""
    total = audit_ledger.count()
    recent = audit_ledger.query(limit=100)
    operator_events = [e for e in recent if _is_operator_event(e)]

    return {
        "total_entries": total,
        "recent_operator_events": len(operator_events),
        "last_entry": recent[0] if recent else None,
    }
