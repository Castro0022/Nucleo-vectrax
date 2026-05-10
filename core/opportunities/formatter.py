"""
core/opportunities/formatter.py
=================================
Convierte oportunidades + métricas temporales en sugerencias humanas
breves para el copiloto operacional. NO envía nada — sólo formatea.

Ejemplo:
    “Carlos mostró interés hace 9 días.
     Señal: ‘lo pienso la próxima semana’
     Estado: ABIERTO — sin seguimiento.”
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .domain import (
    OpportunityState,
    OpportunityStatus,
    Urgency,
)


def _days_since(ts: datetime, now: Optional[datetime] = None) -> int:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    cur = now or datetime.now(timezone.utc)
    if cur.tzinfo is None:
        cur = cur.replace(tzinfo=timezone.utc)
    delta = cur - ts
    return max(0, delta.days)


def _ago(days: int) -> str:
    if days <= 0:
        return "hoy"
    if days == 1:
        return "hace 1 día"
    return f"hace {days} días"


def _intent_verb(op: OpportunityState) -> str:
    """Verbo humano según el tipo de intención detectada."""
    return {
        "INTEREST": "mostró interés",
        "OBJECTION": "objetó precio",
        "DELAY": "pidió tiempo",
        "FOLLOWUP": "quedó pendiente",
        "CLOSED": "cerró",
    }.get(op.intent_type.value, "mostró interés")


def _name(op: OpportunityState) -> str:
    return (op.contact_name or op.user_id or "el contacto").strip()


def _status_label(op: OpportunityState, urgency: Optional[Urgency]) -> str:
    if op.status == OpportunityStatus.CLOSED:
        return "CERRADO"
    if op.status == OpportunityStatus.DORMANT:
        return "DORMIDO"
    # OPEN
    if urgency == Urgency.URGENT:
        return "ABIERTO — urgente, sin seguimiento"
    if urgency == Urgency.WARM:
        return "ABIERTO — tibio, requiere recontacto"
    return "ABIERTO — sin seguimiento"


def humanize(
    op: OpportunityState,
    urgency: Optional[Urgency] = None,
    now: Optional[datetime] = None,
) -> str:
    """
    Devuelve la sugerencia humana de tres líneas.

    El `urgency` puede venir del `ReactivationScheduler.classify_for_state`,
    o quedar None si el caller no lo calculó.
    """
    days = _days_since(op.last_activity, now)
    name = _name(op)
    verb = _intent_verb(op)
    signal = (op.intent_signal or "").strip()
    status = _status_label(op, urgency)

    line1 = f"{name} {verb} {_ago(days)}."
    line2 = f"Señal: '{signal}'" if signal else "Señal: (sin texto registrado)"
    line3 = f"Estado: {status}."
    return f"{line1}\n{line2}\n{line3}"


def humanize_summary_count(
    open_count: int,
    closed_count: int,
    pending_warm: int,
    pending_urgent: int,
) -> str:
    """Resumen agregado por owner para dashboards/reports internos."""
    parts = [
        f"{open_count} oportunidades abiertas",
        f"{closed_count} cerradas",
    ]
    if pending_warm:
        parts.append(f"{pending_warm} tibias por recontactar")
    if pending_urgent:
        parts.append(f"{pending_urgent} urgentes sin seguimiento")
    return ", ".join(parts) + "."
