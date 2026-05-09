"""
core/identity — Identity layer (Creator Cognitive Mode).

Cuando el chat_id corresponde al creador, Baytracks entra en un modo
distinto: tono directo, densidad alta, observación operacional,
continuidad de hilo, sin lenguaje de asistente genérico.
"""

from core.identity.creator_mode import (  # noqa: F401
    CreatorProfile,
    DEFAULT_PROFILE,
    is_creator,
    profile_for,
    compose_creator_context,
)

__all__ = [
    "CreatorProfile",
    "DEFAULT_PROFILE",
    "is_creator",
    "profile_for",
    "compose_creator_context",
]
