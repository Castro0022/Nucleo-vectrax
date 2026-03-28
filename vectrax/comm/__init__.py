"""
Vectrax Communication Engines
================================
Two isolated communication motors:

  CoreCommEngine  — Voice + Text, exclusive to Mario ↔ Nucleus
  StarCommEngine  — Text only, per-user within their own star

Factory:
  get_engine(owner) → returns the appropriate engine for the identity.
"""
from __future__ import annotations

from vectrax.identity import CREATOR_OWNER


def get_engine(owner: str):
    """
    Return the correct communication engine for *owner*.

    - owner == 'mario'  → CoreCommEngine  (voice + text, nucleus)
    - any other owner   → StarCommEngine  (text, isolated star)
    """
    if owner == CREATOR_OWNER:
        from vectrax.comm.engine_core import CoreCommEngine
        return CoreCommEngine()
    else:
        from vectrax.comm.engine_star import StarCommEngine
        return StarCommEngine(owner=owner)
