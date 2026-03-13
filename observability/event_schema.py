"""
Vectrax Observability — Event Schema
=======================================
Stable JSON event schema for telemetry and inter-component communication.
All events across the platform conform to this schema.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class Severity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class VectraxEvent(BaseModel):
    """
    Canonical event schema for the Vectrax platform.
    Used by Core, Agent, and Connectors.
    """

    # Identity
    event_id: str = ""
    event_type: str = "generic"
    source: str = "unknown"  # e.g. "core", "agent:local-001", "connector:google"

    # Timing
    timestamp: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat() + "Z"
    )

    # Classification
    severity: Severity = Severity.INFO
    category: str = ""  # e.g. "proposal", "health", "auth", "connector"

    # Payload
    data: Dict[str, Any] = Field(default_factory=dict)

    # Correlation
    correlation_id: Optional[str] = None
    agent_id: Optional[str] = None
    proposal_id: Optional[str] = None

    # Metadata
    version: str = "1.0"

    class Config:
        use_enum_values = True


def create_event(
    event_type: str,
    source: str = "core",
    severity: str = "info",
    category: str = "",
    data: Dict[str, Any] | None = None,
    **kwargs: Any,
) -> VectraxEvent:
    """Helper to create a VectraxEvent with defaults."""
    return VectraxEvent(
        event_type=event_type,
        source=source,
        severity=severity,
        category=category,
        data=data or {},
        **kwargs,
    )
