"""
core/opportunities/state_machine.py
=====================================
Transiciones permitidas entre estados de oportunidad.

Reglas:
    OPEN    → CLOSED, DORMANT
    DORMANT → OPEN, CLOSED
    CLOSED  → (final, no se reabre desde aquí; se crearía nueva)
"""
from __future__ import annotations

from typing import Dict, Set

from .domain import OpportunityStatus


_ALLOWED: Dict[OpportunityStatus, Set[OpportunityStatus]] = {
    OpportunityStatus.OPEN: {
        OpportunityStatus.CLOSED,
        OpportunityStatus.DORMANT,
    },
    OpportunityStatus.DORMANT: {
        OpportunityStatus.OPEN,
        OpportunityStatus.CLOSED,
    },
    OpportunityStatus.CLOSED: set(),  # terminal
}


class InvalidTransition(ValueError):
    """Se intentó pasar de un estado a otro no permitido."""


def can_transition(
    current: OpportunityStatus,
    target: OpportunityStatus,
) -> bool:
    if current == target:
        return True
    return target in _ALLOWED.get(current, set())


def assert_transition(
    current: OpportunityStatus,
    target: OpportunityStatus,
) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(
            f"transition {current.value} → {target.value} not allowed"
        )
