"""
Vectrax UCE — Normalized data models.

Every piece of external data is translated into one of these four
canonical types before it enters the Vectrax engine.  This ensures
every integration speaks the same internal language.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from connectors.core.types import Sensitivity, Severity


@dataclass
class _NormalizedBase:
    """Common fields shared by all normalized types."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_connector: str = ""
    timestamp: float = field(default_factory=time.time)
    sensitivity: Sensitivity = Sensitivity.LOW
    severity: Severity = Severity.INFO
    constellation_hint: Optional[str] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_connector": self.source_connector,
            "timestamp": self.timestamp,
            "sensitivity": self.sensitivity.value,
            "severity": self.severity.value,
            "constellation_hint": self.constellation_hint,
            "metadata": self.metadata,
        }


@dataclass
class NormalizedEvent(_NormalizedBase):
    """
    An event from an external source (e.g. file changed, API called,
    message received, cron fired).
    """
    event_type: str = "generic"
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["kind"] = "event"
        d["event_type"] = self.event_type
        d["description"] = self.description
        return d


@dataclass
class NormalizedEntity(_NormalizedBase):
    """
    An entity retrieved from an external source (e.g. a file, a
    database row, a calendar entry, a contact).
    """
    entity_type: str = "generic"
    name: str = ""
    content: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["kind"] = "entity"
        d["entity_type"] = self.entity_type
        d["name"] = self.name
        d["content"] = self.content
        return d


@dataclass
class NormalizedAction(_NormalizedBase):
    """
    An action performed on an external source (e.g. wrote a file,
    sent a message, inserted a row).
    """
    action_type: str = "generic"
    target: str = ""
    success: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["kind"] = "action"
        d["action_type"] = self.action_type
        d["target"] = self.target
        d["success"] = self.success
        return d


@dataclass
class NormalizedResult(_NormalizedBase):
    """
    The outcome of a connector operation — wraps whatever the
    connector returned into a standard envelope.
    """
    operation: str = ""
    success: bool = False
    error: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["kind"] = "result"
        d["operation"] = self.operation
        d["success"] = self.success
        d["error"] = self.error
        return d
