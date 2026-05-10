"""
core/opportunities/domain.py
=============================
Modelos de dominio puros: enums + dataclasses, sin dependencias de
storage. Todo lo que cambie aquí debe ser compatible con cualquier
backend (SQLite hoy, Postgres mañana).

Reglas:
    * `owner_id` es obligatorio en todos los puntos del sistema.
    * `user_id` representa al CONTACTO al que se asocia la oportunidad
      (no al tenant). Para esta fase se deriva del `contact_name` con
      un slug estable por owner.
    * Los IDs son uuid4 hex (32 chars).
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums de estado e intenciones
# ---------------------------------------------------------------------------

class OpportunityStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    DORMANT = "DORMANT"


class IntentType(str, Enum):
    INTEREST = "INTEREST"
    OBJECTION = "OBJECTION"
    DELAY = "DELAY"
    FOLLOWUP = "FOLLOWUP"
    CLOSED = "CLOSED"


class SourceType(str, Enum):
    CHAT = "CHAT"
    MANUAL = "MANUAL"
    IMPORTED = "IMPORTED"
    AUTO_DETECTED = "AUTO_DETECTED"


class Urgency(str, Enum):
    WARM = "WARM"
    URGENT = "URGENT"


class JobStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSED = "PROCESSED"
    CANCELLED = "CANCELLED"


# ---------------------------------------------------------------------------
# Tipos de evento inmutables
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    OPPORTUNITY_CREATED = "OpportunityCreated"
    OPPORTUNITY_UPDATED = "OpportunityUpdated"
    OPPORTUNITY_CLOSED = "OpportunityClosed"
    OPPORTUNITY_DORMANT = "OpportunityDormant"
    REACTIVATION_SCHEDULED = "ReactivationScheduled"
    REACTIVATION_TRIGGERED = "ReactivationTriggered"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return uuid.uuid4().hex


@dataclass
class OpportunityState:
    """Estado actual de una oportunidad (proyección, no event log)."""
    id: str = field(default_factory=new_uuid)
    owner_id: str = ""
    user_id: str = ""
    contact_name: Optional[str] = None
    status: OpportunityStatus = OpportunityStatus.OPEN
    intent_signal: str = ""
    intent_type: IntentType = IntentType.INTEREST
    source_message: str = ""
    source_type: SourceType = SourceType.AUTO_DETECTED
    confidence_score: float = 0.0
    priority_score: float = 0.0
    created_at: datetime = field(default_factory=_now)
    last_activity: datetime = field(default_factory=_now)
    closed_at: Optional[datetime] = None
    updated_at: datetime = field(default_factory=_now)


@dataclass
class OpportunityEvent:
    """Entrada inmutable del log de eventos."""
    id: str = field(default_factory=new_uuid)
    opportunity_id: str = ""
    owner_id: str = ""
    event_type: EventType = EventType.OPPORTUNITY_CREATED
    payload: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=_now)


@dataclass
class ReactivationJob:
    """Tarea temporal de seguimiento."""
    id: str = field(default_factory=new_uuid)
    opportunity_id: str = ""
    owner_id: str = ""
    due_at: datetime = field(default_factory=_now)
    urgency: Urgency = Urgency.WARM
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = field(default_factory=_now)
    processed_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Identidad de contacto
# ---------------------------------------------------------------------------

_NON_WORD = re.compile(r"[^a-z0-9]+")


def slugify_contact(contact_name: str) -> str:
    """
    Convierte 'Carlos Pérez' → 'carlos-perez'. Estable y sin acentos.
    Útil como `user_id` derivado dentro de un mismo `owner_id` cuando
    aún no tenemos identificadores externos del contacto.
    """
    if not contact_name:
        return ""
    norm = unicodedata.normalize("NFKD", contact_name)
    norm = "".join(c for c in norm if not unicodedata.combining(c))
    norm = norm.lower().strip()
    norm = _NON_WORD.sub("-", norm)
    return norm.strip("-")
