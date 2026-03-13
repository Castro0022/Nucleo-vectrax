"""
Vectrax Cognition — Error hierarchy.

Every motor raises subclasses of CognitionError so callers can handle
them uniformly or granularly.
"""

from __future__ import annotations


class CognitionError(Exception):
    """Base for all cognitive-layer errors."""


# Motor 1 — Perception
class PerceptionError(CognitionError):
    """Signal intake or classification failure."""


class SignalIntakeError(PerceptionError):
    """Failed to ingest signals from a connector."""


# Motor 2 — Reasoning
class ReasoningError(CognitionError):
    """Strategic reasoning failure."""


class ConstitutionalViolation(ReasoningError):
    """Proposal violates one or more immutable principles."""


class ApprovalRequired(ReasoningError):
    """Action blocked — creator approval is required."""


# Motor 3 — Memory
class MemoryError(CognitionError):
    """Structural memory read/write failure."""


class KnowledgeGraphError(MemoryError):
    """Knowledge graph integrity or query error."""


# Motor 4 — Orchestrator
class OrchestrationError(CognitionError):
    """Cognitive loop or orchestrator failure."""


class ModeViolation(OrchestrationError):
    """Attempted action forbidden in the current cognitive mode."""
