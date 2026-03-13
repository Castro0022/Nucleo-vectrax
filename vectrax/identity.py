"""
Vectrax Identity — permanent channel separation principle.

Two channels exist and must never be mixed:

  CREATOR channel (Mario):
    - Lives in the nucleus (core layer by default)
    - Topics: creation, concept, direction, structural proposals, system evolution
    - Foundational memory — no user content may enter this space
    - Proposals generated here are exclusive to the creator

  USER channel:
    - Each user speaks from their own star or constellation
    - Topics: concrete tasks, support, local problems, local evolution
    - Users do not access the nucleus or the creator channel

Rules enforced at code level:
  - Cross-channel similarity links are FORBIDDEN
  - Cross-channel constellation formation is FORBIDDEN
  - Creator proposals are visible only in the creator channel
  - A user identity is never treated as the creator identity
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Channel constants
# ---------------------------------------------------------------------------

CHANNEL_CREATOR = "creator"
CHANNEL_USER = "user"

CREATOR_OWNER = "mario"   # The only valid owner of the creator channel


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class ChannelViolation(Exception):
    """Raised when an operation attempts to cross channel boundaries."""


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_channel(channel: str) -> str:
    """Return the channel if valid, raise ChannelViolation otherwise."""
    if channel not in (CHANNEL_CREATOR, CHANNEL_USER):
        raise ChannelViolation(
            f"Unknown channel '{channel}'. "
            f"Must be '{CHANNEL_CREATOR}' or '{CHANNEL_USER}'."
        )
    return channel


def validate_creator_ownership(owner: str) -> None:
    """Enforce that only Mario can write to the creator channel."""
    if owner != CREATOR_OWNER:
        raise ChannelViolation(
            f"Creator channel belongs exclusively to '{CREATOR_OWNER}'. "
            f"Identity '{owner}' is not permitted here."
        )


def assert_no_cross_channel_link(channel_a: str, channel_b: str) -> None:
    """Raise if two stars from different channels attempt to link."""
    if channel_a != channel_b:
        raise ChannelViolation(
            f"Cross-channel linking is forbidden: "
            f"'{channel_a}' ↔ '{channel_b}'. "
            "Stars and constellations are contained within their own channel."
        )


def channel_label(channel: str, owner: str = "") -> str:
    """Return a human-readable label for display."""
    if channel == CHANNEL_CREATOR:
        return "[bold yellow]◈ NÚCLEO / CREATOR[/bold yellow]"
    name = owner if owner else "anonymous"
    return f"[cyan]◉ user:{name}[/cyan]"
