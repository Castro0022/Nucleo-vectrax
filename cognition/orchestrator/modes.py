"""
Cognitive Modes — operational mode management with persistence.

Two modes:
  - SILENT_OBSERVATION  — perceive and learn, never propose
  - SUPERVISED_PROPOSAL — perceive, reason, propose, wait for approval

No autonomous mode exists by design.
"""

from __future__ import annotations

import logging
from typing import Optional

from cognition.errors import ModeViolation
from cognition.types import CognitiveMode

logger = logging.getLogger("vectrax.cognition.orchestrator.modes")


class ModeManager:
    """Manages the current cognitive mode with state persistence."""

    STATE_KEY = "cognitive_mode"

    def __init__(self) -> None:
        self._mode: Optional[CognitiveMode] = None

    @property
    def current(self) -> CognitiveMode:
        """Return current mode (loads from state if needed)."""
        if self._mode is None:
            self._mode = self._load()
        return self._mode

    def set_mode(self, mode: CognitiveMode) -> CognitiveMode:
        """Change the cognitive mode and persist."""
        self._mode = mode
        self._save(mode)
        logger.info("Cognitive mode changed to: %s", mode.value)
        return mode

    def can_propose(self) -> bool:
        """Whether proposals are allowed in the current mode."""
        return self.current == CognitiveMode.SUPERVISED_PROPOSAL

    def require_proposal_mode(self) -> None:
        """Raise ModeViolation if not in proposal mode."""
        if not self.can_propose():
            raise ModeViolation(
                f"Cannot generate proposals in {self.current.value} mode. "
                "Switch to supervised_proposal first."
            )

    def auto_adjust(self, governor_mode: str) -> None:
        """
        Automatically adjust cognitive mode based on governor state.
        If governor is in 'recover', force SILENT_OBSERVATION.
        """
        if governor_mode == "recover" and self.current != CognitiveMode.SILENT_OBSERVATION:
            logger.warning(
                "Governor in recover mode — forcing SILENT_OBSERVATION"
            )
            self.set_mode(CognitiveMode.SILENT_OBSERVATION)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _load() -> CognitiveMode:
        try:
            from core import state_manager
            value = state_manager.get("cognitive_mode")
            if value:
                return CognitiveMode(value)
        except Exception:
            pass
        return CognitiveMode.SILENT_OBSERVATION  # safe default

    @staticmethod
    def _save(mode: CognitiveMode) -> None:
        try:
            from core import state_manager
            state_manager.put("cognitive_mode", mode.value)
        except Exception:
            pass
