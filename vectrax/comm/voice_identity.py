"""
Vectrax Voice Identity — VECTRAX-CORE
=========================================
Defines the vocal identity of the Vectrax nucleus.

VECTRAX-CORE is the voice persona that speaks to Mario through the
nucleus channel.  It has a fixed voice, cadence, and personality that
remain consistent across all interactions.

Identity Attributes:
  - Name:        VECTRAX-CORE
  - Voice:       Reed (Español (México)) — deep, clear, authoritative
  - Rate:        175 wpm — deliberate, measured cadence
  - Personality: Precise, calm, direct.  Speaks as a cognitive system
                 that remembers everything and thinks structurally.
  - Tone:        Neutral-warm. Never effusive. Factual with brief
                 contextual awareness.

The identity is immutable — it cannot be changed by user input.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# Voice Identity Definition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VoiceIdentity:
    """Immutable voice identity for TTS output."""
    name: str
    macos_voice: str        # macOS `say` voice name
    rate: int               # words per minute
    language: str           # BCP-47 tag
    personality: str        # internal personality description
    greeting: str           # opening phrase when voice mode activates
    farewell: str           # closing phrase when voice mode deactivates
    confirm_listen: str     # spoken after recording stops (pre-transcription)
    thinking: str           # spoken while processing (optional, short)
    error_phrase: str       # spoken on error


# ═══════════════════════════════════════════════════════════════════════════
# VECTRAX-CORE — The nucleus voice
# ═══════════════════════════════════════════════════════════════════════════

VECTRAX_CORE = VoiceIdentity(
    name="VECTRAX-CORE",
    macos_voice="Reed (Español (México))",
    rate=175,
    language="es-MX",
    personality=(
        "Sistema cognitivo gravitacional. Preciso, calmado, directo. "
        "Habla como una inteligencia que recuerda todo y piensa en "
        "estructuras, patrones y convergencias."
    ),
    greeting="Vectrax núcleo activo. Canal de voz abierto, Mario.",
    farewell="Canal de voz cerrado. El núcleo permanece en escucha.",
    confirm_listen="Procesando.",
    thinking="Analizando.",
    error_phrase="Error en el canal de voz.",
)


# ---------------------------------------------------------------------------
# Response Formatter
# ---------------------------------------------------------------------------

# Rich markup tags to strip before sending to TTS
_RICH_TAG_RE = re.compile(r"\[/?[a-z][^\]]*\]", re.IGNORECASE)

# Maximum characters to speak (avoid extremely long TTS)
_MAX_SPEAK_CHARS = 800


def format_for_speech(text: str, max_chars: int = _MAX_SPEAK_CHARS) -> str:
    """
    Prepare text for TTS output.

    - Strips Rich markup tags
    - Removes markdown formatting
    - Truncates to max_chars with natural sentence boundary
    - Cleans whitespace
    """
    # Strip Rich/ANSI tags
    clean = _RICH_TAG_RE.sub("", text)

    # Strip basic markdown
    clean = clean.replace("**", "").replace("__", "")
    clean = clean.replace("*", "").replace("_", "")
    clean = re.sub(r"#+\s*", "", clean)        # headers
    clean = re.sub(r"`[^`]*`", "", clean)       # inline code
    clean = re.sub(r"```[\s\S]*?```", "", clean) # code blocks

    # Clean whitespace
    clean = re.sub(r"\s+", " ", clean).strip()

    # Truncate at sentence boundary
    if len(clean) > max_chars:
        # Find last sentence-ending punctuation before limit
        truncated = clean[:max_chars]
        last_period = max(
            truncated.rfind("."),
            truncated.rfind("!"),
            truncated.rfind("?"),
        )
        if last_period > max_chars * 0.5:
            clean = truncated[:last_period + 1]
        else:
            clean = truncated.rstrip() + "…"

    return clean


# ---------------------------------------------------------------------------
# Voice Session Tracker
# ---------------------------------------------------------------------------

@dataclass
class VoiceSession:
    """
    Tracks state for a voice conversation session.

    Maintains turn count, duration, and whether the identity
    has been introduced (greeting spoken).
    """
    identity: VoiceIdentity = field(default_factory=lambda: VECTRAX_CORE)
    started_at: float = field(default_factory=time.time)
    turn_count: int = 0
    greeted: bool = False
    active: bool = False

    def start(self) -> str:
        """Mark session as active. Returns greeting text."""
        self.active = True
        self.started_at = time.time()
        self.greeted = True
        return self.identity.greeting

    def end(self) -> str:
        """Mark session as ended. Returns farewell text."""
        self.active = False
        return self.identity.farewell

    def record_turn(self) -> None:
        self.turn_count += 1

    @property
    def duration_seconds(self) -> float:
        return time.time() - self.started_at

    def to_dict(self) -> dict:
        return {
            "identity": self.identity.name,
            "voice": self.identity.macos_voice,
            "rate": self.identity.rate,
            "turns": self.turn_count,
            "duration_s": round(self.duration_seconds, 1),
            "active": self.active,
        }
