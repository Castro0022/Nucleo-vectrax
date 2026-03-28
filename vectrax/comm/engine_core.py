"""
Vectrax Core Communication Engine
=====================================
Exclusive voice + text channel: Mario ↔ Vectrax Nucleus.

Only the creator (mario) may instantiate this engine.
All messages are ingested into the CREATOR channel and resolved
through the full cognitive pipeline (classify → resolve → respond).

Voice support: transcribes audio via Whisper (local or API fallback)
before delegating to the text pipeline.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from vectrax.identity import (
    CHANNEL_CREATOR,
    CREATOR_OWNER,
    ChannelViolation,
)

logger = logging.getLogger("vectrax.comm.core")


# ---------------------------------------------------------------------------
# Response dataclass
# ---------------------------------------------------------------------------

@dataclass
class CoreResponse:
    """Response from the nucleus to Mario."""
    message_id: str = ""
    text: str = ""                  # sovereign answer (clean, user-facing)
    star_id: str = ""
    layer: str = ""
    gravity_score: float = 0.0
    resolve_mode: str = "memory"    # memory | local | online
    context_stars: int = 0
    session_id: str = ""
    timestamp: float = field(default_factory=time.time)
    source_type: str = "text"       # "text" | "voice"

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
            "timestamp": self.timestamp,
            "source_type": self.source_type,
        }


# ---------------------------------------------------------------------------
# Core Communication Engine
# ---------------------------------------------------------------------------

class CoreCommEngine:
    """
    Exclusive communication motor: Mario ↔ Vectrax Nucleus.

    Supports:
      - send_text(text)       → ingest + resolve, return sovereign answer
      - send_voice(audio)     → transcribe → send_text
      - get_history(limit)    → creator conversation history
      - get_context()         → nucleus status summary
    """

    def __init__(self, session_id: Optional[str] = None) -> None:
        self.owner = CREATOR_OWNER
        self.channel = CHANNEL_CREATOR
        self.session_id = session_id or str(uuid.uuid4())
        logger.info(
            "CoreCommEngine initialised (session=%s)", self.session_id[:8]
        )

    # ------------------------------------------------------------------
    # Text
    # ------------------------------------------------------------------

    def send_text(
        self,
        text: str,
        success: bool = True,
        source_type: str = "text",
    ) -> CoreResponse:
        """
        Send a text message from Mario to the nucleus.

        Pipeline:
          1. Classify intent (memory / local / online)
          2. For voice+memory: probe local resolution BEFORE ingesting
             (avoids self-match on the just-created star)
          3. Ingest into creator channel as star
          4. Resolve through cognitive pipeline
          5. Persist conversation turn
          6. Return sovereign response

        Args:
            source_type: "text" (default) or "voice". Voice inputs always
                         receive a conversational response — never just
                         "Registrado." / "Actualizado."
        """
        from vectrax import db
        from vectrax.engine import ingest
        from vectrax.resolver import resolve, resolve_local, _detect_lang, _get_labels

        db.init_db()

        # ── Resolve (classify + answer) ──────────────────────────────
        resolution = resolve(text, self.channel, self.owner)
        mode = resolution.mode

        # ── Voice + memory: probe local context BEFORE ingest ────────
        # Calling resolve_local here (before the star is created) ensures
        # the new star does not appear as a spurious self-match.
        voice_local_res = None
        if source_type == "voice" and mode == "memory":
            voice_local_res = resolve_local(text, self.channel, self.owner)

        # ── Ingest into nucleus ──────────────────────────────────────
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

            if source_type == "voice":
                # Voice conversation: return context-aware response.
                # If there are related stars in memory, surface them;
                # otherwise give a natural acknowledgment (not "Registrado.").
                if voice_local_res and voice_local_res.context_stars > 0:
                    sovereign = voice_local_res.sovereign_answer
                    mode = "local"
                    resolution = voice_local_res
                else:
                    sovereign = (
                        "Entendido. Lo he registrado en tu memoria."
                        if lang == "es"
                        else "Understood. Saved to your memory."
                    )
            else:
                sovereign = (
                    labels["registered"]
                    if star.repetition_count == 1
                    else labels["updated"]
                )
        else:
            sovereign = resolution.sovereign_answer

        # ── Persist conversation ─────────────────────────────────────
        db.insert_message(self.session_id, "mario", text, star_id=star.id)
        msg_id = db.insert_message(self.session_id, "vectrax", sovereign)

        # ── Learning log ───────────────────────────────────
        self._log_learning(text, mode, resolution)

        # ── Emit to Universal Bus (live circuit) ────────────────────
        try:
            from core.operator.universal_bus import get_universal_bus
            from core.operator.bus_reactor import LiveChannels, EventTypes
            _live_ch = LiveChannels.VOICE if source_type == "voice" else LiveChannels.CHAT
            _live_et = EventTypes.VOICE_RESPONSE if source_type == "voice" else EventTypes.CHAT_RESPONSE
            get_universal_bus().emit(
                channel=_live_ch,
                event_type=_live_et,
                source_layer=1,
                payload={
                    "text": text[:100],
                    "sovereign": sovereign[:200],
                    "star_id": star.id,
                    "resolve_mode": mode,
                    "source_type": source_type,
                    "channel": self.channel,
                },
            )
        except Exception:
            pass  # bus hook is non-fatal

        return CoreResponse(
            message_id=msg_id,
            text=sovereign,
            star_id=star.id,
            layer=star.layer,
            gravity_score=star.gravity_score,
            resolve_mode=mode,
            context_stars=resolution.context_stars,
            session_id=self.session_id,
            source_type=source_type,
        )

    # ------------------------------------------------------------------
    # Voice
    # ------------------------------------------------------------------

    def send_voice(self, audio_path: str) -> CoreResponse:
        """
        Send a voice message from Mario to the nucleus.

        Transcription chain (first available wins):
          1. Local Whisper (whisper CLI or faster-whisper)
          2. OpenAI Whisper API
          3. Fallback error

        After transcription, delegates to send_text().
        """
        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        transcript = self._transcribe(path)
        if not transcript:
            raise RuntimeError("Voice transcription returned empty result")

        logger.info("Voice transcribed (%d chars): %s…", len(transcript), transcript[:60])

        response = self.send_text(transcript)
        response.source_type = "voice"
        return response

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_history(self, limit: int = 50) -> List[dict]:
        """Return conversation history for the current session."""
        from vectrax import db
        db.init_db()

        if self.session_id:
            return db.get_session_messages(self.session_id)

        return db.get_recent_messages(limit=limit)

    # ------------------------------------------------------------------
    # Context — nucleus status
    # ------------------------------------------------------------------

    def get_context(self) -> dict:
        """
        Return a summary of the nucleus state visible only to Mario.

        Includes: star count, constellation count, pending proposals,
        layer distribution, recent activity.
        """
        from vectrax import db
        db.init_db()

        creator_stars = db.get_all_stars(
            channel=CHANNEL_CREATOR, owner=CREATOR_OWNER
        )
        creator_constellations = db.get_all_constellations(
            channel=CHANNEL_CREATOR, owner=CREATOR_OWNER
        )
        pending_proposals = db.get_proposals(status="pending")

        # Layer distribution
        layers: dict = {}
        for s in creator_stars:
            layers[s.layer] = layers.get(s.layer, 0) + 1

        # Gravity stats
        gravities = [s.gravity_score for s in creator_stars]
        avg_gravity = sum(gravities) / len(gravities) if gravities else 0.0

        return {
            "channel": CHANNEL_CREATOR,
            "owner": CREATOR_OWNER,
            "session_id": self.session_id,
            "stars_total": len(creator_stars),
            "constellations_total": len(creator_constellations),
            "pending_proposals": len(pending_proposals),
            "layers": layers,
            "avg_gravity": round(avg_gravity, 4),
            "engine_type": "core",
            "capabilities": ["text", "voice"],
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _transcribe(self, audio_path: Path) -> str:
        """
        Transcribe audio to text.

        Strategy:
          1. Try local whisper CLI (faster-whisper or openai-whisper)
          2. Try OpenAI Whisper API if local unavailable
          3. Raise RuntimeError if nothing works
        """
        # ── 1. Local whisper via CLI ─────────────────────────────────
        transcript = self._transcribe_local(audio_path)
        if transcript:
            return transcript

        # ── 2. OpenAI Whisper API ────────────────────────────────────
        transcript = self._transcribe_api(audio_path)
        if transcript:
            return transcript

        raise RuntimeError(
            "No transcription backend available. "
            "Install 'faster-whisper' or set OPENAI_API_KEY."
        )

    @staticmethod
    def _transcribe_local(audio_path: Path) -> Optional[str]:
        """Try faster-whisper or whisper locally."""
        try:
            from faster_whisper import WhisperModel
            model = WhisperModel("base", device="cpu", compute_type="int8")
            segments, _ = model.transcribe(str(audio_path), language="es")
            return " ".join(seg.text.strip() for seg in segments)
        except ImportError:
            pass
        except Exception as exc:
            logger.warning("Local whisper failed: %s", exc)

        # Fallback: openai-whisper CLI
        try:
            import subprocess
            result = subprocess.run(
                ["whisper", str(audio_path), "--language", "es",
                 "--model", "base", "--output_format", "txt"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                txt_path = audio_path.with_suffix(".txt")
                if txt_path.exists():
                    text = txt_path.read_text().strip()
                    txt_path.unlink(missing_ok=True)
                    return text
        except FileNotFoundError:
            pass  # whisper CLI not installed
        except Exception as exc:
            logger.warning("Whisper CLI failed: %s", exc)

        return None

    @staticmethod
    def _transcribe_api(audio_path: Path) -> Optional[str]:
        """Try OpenAI Whisper API."""
        import os
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None

        try:
            import openai
            client = openai.OpenAI()
            with open(audio_path, "rb") as f:
                response = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    language="es",
                )
            return response.text.strip() if response.text else None
        except Exception as exc:
            logger.warning("OpenAI Whisper API failed: %s", exc)
            return None

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
