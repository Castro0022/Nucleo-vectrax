"""
Vectrax Star Communication Engine
=====================================
Text-only communication channel for each user within their own star.

Each user operates in complete isolation:
  - Memory scoped to their own stars (channel=user, owner=<username>)
  - Conversation history separated per user
  - No access to creator channel or nucleus content
  - A structural projection of the nucleus is available (metadata only)
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

from vectrax.identity import (
    CHANNEL_CREATOR,
    CHANNEL_USER,
    CREATOR_OWNER,
    ChannelViolation,
)

logger = logging.getLogger("vectrax.comm.star")


# ---------------------------------------------------------------------------
# Response dataclass
# ---------------------------------------------------------------------------

@dataclass
class StarResponse:
    """Response from the user's star."""
    message_id: str = ""
    text: str = ""                  # sovereign answer (clean, user-facing)
    star_id: str = ""
    layer: str = ""
    gravity_score: float = 0.0
    resolve_mode: str = "memory"    # memory | local | online
    context_stars: int = 0
    session_id: str = ""
    owner: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "text": self.text,
            "star_id": self.star_id,
            "layer": self.layer,
            "gravity_score": round(self.gravity_score, 4),
            "resolve_mode": self.resolve_mode,
            "context_stars": self.context_stars,
            "session_id": self.session_id,
            "owner": self.owner,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Star Communication Engine
# ---------------------------------------------------------------------------

class StarCommEngine:
    """
    Isolated text communication motor for a single user.

    Each user talks within their own star — memory, history, and
    context are completely separated.  The creator channel is never
    accessed.

    Supports:
      - send_text(text)       → ingest + resolve within user scope
      - get_history(limit)    → user's conversation history
      - get_context()         → user's star summary + nucleus projection
    """

    def __init__(
        self,
        owner: str,
        session_id: Optional[str] = None,
    ) -> None:
        if owner == CREATOR_OWNER:
            raise ChannelViolation(
                f"Identity '{owner}' must use CoreCommEngine, not StarCommEngine."
            )
        if not owner:
            raise ValueError("StarCommEngine requires a non-empty owner.")

        self.owner = owner
        self.channel = CHANNEL_USER
        self.session_id = session_id or str(uuid.uuid4())
        logger.info(
            "StarCommEngine initialised (owner=%s, session=%s)",
            self.owner, self.session_id[:8],
        )

    # ------------------------------------------------------------------
    # Text
    # ------------------------------------------------------------------

    def send_text(self, text: str, success: bool = False) -> StarResponse:
        """
        Send a text message from the user into their star.

        Pipeline:
          1. Classify intent (memory / local / online)
          2. Ingest into user channel (owner-scoped)
          3. Resolve with user's own context only
          4. Persist conversation turn
          5. Return sovereign response
        """
        from vectrax import db
        from vectrax.engine import ingest
        from vectrax.resolver import resolve, _detect_lang, _get_labels

        db.init_db()

        # ── Resolve (classify + answer) ──────────────────────────────
        resolution = resolve(text, self.channel, self.owner)
        mode = resolution.mode

        # ── Ingest into user's star ──────────────────────────────────
        star = ingest(
            text=text,
            success=success,
            channel=self.channel,
            owner=self.owner,
        )

        # ── Build sovereign answer ───────────────────────────────────
        if mode == "memory":
            lang = _detect_lang(text)
            labels = _get_labels(lang)
            sovereign = (
                labels["registered"]
                if star.repetition_count == 1
                else labels["updated"]
            )
        else:
            sovereign = resolution.sovereign_answer

        # ── Persist conversation ─────────────────────────────────────
        db.insert_message(self.session_id, self.owner, text, star_id=star.id)
        msg_id = db.insert_message(self.session_id, "vectrax", sovereign)

        # ── Learning log ─────────────────────────────────────────────
        self._log_learning(text, mode, resolution)

        return StarResponse(
            message_id=msg_id,
            text=sovereign,
            star_id=star.id,
            layer=star.layer,
            gravity_score=star.gravity_score,
            resolve_mode=mode,
            context_stars=resolution.context_stars,
            session_id=self.session_id,
            owner=self.owner,
        )

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_history(self, limit: int = 50) -> List[dict]:
        """Return conversation history for this user's session."""
        from vectrax import db
        db.init_db()

        if self.session_id:
            return db.get_session_messages(self.session_id)

        return db.get_recent_messages(limit=limit)

    # ------------------------------------------------------------------
    # Context — user's star summary + nucleus projection
    # ------------------------------------------------------------------

    def get_context(self) -> dict:
        """
        Return the user's star context plus a structural projection
        of the nucleus (metadata only, never creator content).
        """
        from vectrax import db
        from vectrax.comm.projection import CoreProjection

        db.init_db()

        # ── User's own data ──────────────────────────────────────────
        user_stars = db.get_all_stars(
            channel=CHANNEL_USER, owner=self.owner
        )
        user_constellations = db.get_all_constellations(
            channel=CHANNEL_USER, owner=self.owner
        )

        layers: dict = {}
        for s in user_stars:
            layers[s.layer] = layers.get(s.layer, 0) + 1

        gravities = [s.gravity_score for s in user_stars]
        avg_gravity = sum(gravities) / len(gravities) if gravities else 0.0

        # ── Nucleus projection (structure only) ──────────────────────
        projection = CoreProjection(owner=self.owner)

        return {
            "channel": CHANNEL_USER,
            "owner": self.owner,
            "session_id": self.session_id,
            "stars_total": len(user_stars),
            "constellations_total": len(user_constellations),
            "layers": layers,
            "avg_gravity": round(avg_gravity, 4),
            "engine_type": "star",
            "capabilities": ["text"],
            "nucleus_projection": projection.get_structure(),
            "navigation": projection.get_navigation_context(),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _log_learning(self, text: str, mode: str, resolution) -> None:
        """Store resolution pattern for silent learning."""
        try:
            from vectrax.db import log_resolution
            pattern = mode
            if resolution.fallback_from:
                pattern = f"{resolution.fallback_from}→{mode}"
            log_resolution(
                question=text,
                resolve_pattern=pattern,
                engines_used=resolution.engines_used,
                consensus=resolution.sovereign_answer[:100],
                channel=self.channel,
                owner=self.owner,
            )
        except Exception as exc:
            logger.warning("Learning log failed (non-fatal): %s", exc)
