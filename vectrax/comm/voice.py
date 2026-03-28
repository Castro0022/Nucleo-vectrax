"""
Vectrax Voice Interface
==========================
Full voice pipeline for the nucleus communication channel (Mario ↔ Vectrax).

Components:
  1. Microphone recording  — sounddevice + soundfile (press Enter to stop)
  2. Transcription          — faster-whisper (local) → OpenAI Whisper API (cloud)
  3. Text-to-Speech (TTS)  — macOS `say` (native, zero-dependency)

This module is designed for macOS and uses native system commands
(`say`, `afplay`) for TTS and audio playback.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("vectrax.comm.voice")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# macOS TTS defaults
_DEFAULT_VOICE = "Paulina"          # es_MX — clear female Spanish voice
_DEFAULT_RATE = 180                 # words per minute
_SAMPLE_RATE = 16000                # 16 kHz — optimal for Whisper
_CHANNELS = 1                       # mono

# Temp directory for audio files
_AUDIO_DIR = Path(tempfile.gettempdir()) / "vectrax_voice"


def _ensure_audio_dir() -> Path:
    _AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    return _AUDIO_DIR


# ---------------------------------------------------------------------------
# 1. Microphone Recording
# ---------------------------------------------------------------------------

class MicRecorder:
    """
    Records audio from the default microphone.

    Usage:
        recorder = MicRecorder()
        recorder.start()     # begins recording in background
        # ... user speaks ...
        path = recorder.stop()  # stops and returns .wav path
    """

    def __init__(self, sample_rate: int = _SAMPLE_RATE, channels: int = _CHANNELS):
        self._sample_rate = sample_rate
        self._channels = channels
        self._recording = False
        self._frames = []
        self._stream = None
        self._thread: Optional[threading.Thread] = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self) -> None:
        """Start recording from microphone in a background thread."""
        import sounddevice as sd

        self._frames = []
        self._recording = True

        def _callback(indata, frames, time_info, status):
            if status:
                logger.debug("Recording status: %s", status)
            if self._recording:
                self._frames.append(indata.copy())

        self._stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="float32",
            callback=_callback,
        )
        self._stream.start()
        logger.info("Microphone recording started (rate=%d)", self._sample_rate)

    def stop(self) -> Optional[str]:
        """Stop recording and save to a .wav file. Returns file path."""
        import numpy as np
        import soundfile as sf

        self._recording = False

        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if not self._frames:
            logger.warning("No audio frames captured")
            return None

        audio_data = np.concatenate(self._frames, axis=0)
        duration = len(audio_data) / self._sample_rate

        if duration < 0.5:
            logger.warning("Recording too short (%.1fs), discarding", duration)
            return None

        # Save to temp WAV file
        out_dir = _ensure_audio_dir()
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"voice_{timestamp}.wav"

        sf.write(str(out_path), audio_data, self._sample_rate)
        logger.info("Recorded %.1fs of audio → %s", duration, out_path)
        return str(out_path)

    def cancel(self) -> None:
        """Cancel recording without saving."""
        self._recording = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._frames = []


# ---------------------------------------------------------------------------
# 2. Transcription (delegates to CoreCommEngine pipeline)
# ---------------------------------------------------------------------------

def transcribe(audio_path: str) -> Optional[str]:
    """
    Transcribe an audio file to text.

    Tries in order:
      1. faster-whisper (local, no network)
      2. OpenAI Whisper API (cloud, requires OPENAI_API_KEY)
      3. Returns None if nothing works
    """
    path = Path(audio_path)
    if not path.exists():
        logger.error("Audio file not found: %s", audio_path)
        return None

    # ── 1. faster-whisper ────────────────────────────────────────────
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(path), language="es")
        text = " ".join(seg.text.strip() for seg in segments)
        if text:
            return text
    except ImportError:
        logger.debug("faster-whisper not available")
    except Exception as exc:
        logger.warning("faster-whisper failed: %s", exc)

    # ── 2. whisper CLI ───────────────────────────────────────────────
    try:
        result = subprocess.run(
            ["whisper", str(path), "--language", "es",
             "--model", "base", "--output_format", "txt"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            txt_path = path.with_suffix(".txt")
            if txt_path.exists():
                text = txt_path.read_text().strip()
                txt_path.unlink(missing_ok=True)
                if text:
                    return text
    except FileNotFoundError:
        logger.debug("whisper CLI not installed")
    except Exception as exc:
        logger.warning("whisper CLI failed: %s", exc)

    # ── 3. OpenAI Whisper API ────────────────────────────────────────
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        try:
            import openai
            client = openai.OpenAI()
            with open(path, "rb") as f:
                response = client.audio.transcriptions.create(
                    model="whisper-1", file=f, language="es",
                )
            if response.text:
                return response.text.strip()
        except Exception as exc:
            logger.warning("OpenAI Whisper API failed: %s", exc)

    return None


# ---------------------------------------------------------------------------
# 3. Text-to-Speech (macOS native)
# ---------------------------------------------------------------------------

def speak(
    text: str,
    voice: str = _DEFAULT_VOICE,
    rate: int = _DEFAULT_RATE,
    wait: bool = True,
    save_path: Optional[str] = None,
) -> Optional[str]:
    """
    Speak text aloud using macOS `say` command.

    Args:
        text:      Text to speak
        voice:     macOS voice name (default: Paulina, es_MX)
        rate:      Speech rate in words per minute
        wait:      If True, block until speech completes
        save_path: If set, save audio to this .aiff file instead of playing

    Returns:
        Path to saved audio file if save_path is set, else None.
    """
    if not text or not text.strip():
        return None

    # subprocess list mode passes args directly — NO shell escaping needed
    cmd = ["say", "-v", voice, "-r", str(rate)]

    if save_path:
        cmd.extend(["-o", save_path])

    cmd.append(text.strip())

    try:
        if wait:
            result = subprocess.run(
                cmd, timeout=60, capture_output=True, text=True,
            )
            if result.returncode != 0:
                logger.error("say failed (code %d): %s", result.returncode, result.stderr)
                return None
        else:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if save_path:
            return save_path
        return "ok"
    except FileNotFoundError:
        logger.error("macOS 'say' command not found — TTS unavailable")
    except subprocess.TimeoutExpired:
        logger.warning("TTS timed out for text: %s…", text[:50])
    except Exception as exc:
        logger.warning("TTS failed: %s", exc)

    return None


def speak_async(text: str, voice: str = _DEFAULT_VOICE, rate: int = _DEFAULT_RATE) -> None:
    """Speak text in a background thread (non-blocking)."""
    thread = threading.Thread(
        target=speak,
        args=(text,),
        kwargs={"voice": voice, "rate": rate, "wait": True},
        daemon=True,
    )
    thread.start()


def stop_speaking() -> None:
    """Stop any ongoing macOS speech."""
    try:
        subprocess.run(
            ["killall", "say"],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 3b. Identity-aware TTS — VECTRAX-CORE
# ---------------------------------------------------------------------------

def speak_as_core(
    text: str,
    wait: bool = True,
    save_path: Optional[str] = None,
) -> Optional[str]:
    """
    Speak text using the VECTRAX-CORE voice identity.

    Applies the identity's voice, rate, and formats the text
    for clean speech output (strips markup, truncates safely).
    """
    from vectrax.comm.voice_identity import VECTRAX_CORE, format_for_speech

    clean_text = format_for_speech(text)
    if not clean_text:
        return None

    return speak(
        text=clean_text,
        voice=VECTRAX_CORE.macos_voice,
        rate=VECTRAX_CORE.rate,
        wait=wait,
        save_path=save_path,
    )


def speak_core_async(text: str) -> None:
    """Speak text as VECTRAX-CORE in a background thread."""
    thread = threading.Thread(
        target=speak_as_core,
        args=(text,),
        kwargs={"wait": True},
        daemon=True,
    )
    thread.start()


def speak_core_phrase(phrase_attr: str, wait: bool = True) -> None:
    """
    Speak a predefined identity phrase (greeting, farewell, etc.).

    phrase_attr: one of 'greeting', 'farewell', 'confirm_listen',
                 'thinking', 'error_phrase'
    """
    from vectrax.comm.voice_identity import VECTRAX_CORE
    text = getattr(VECTRAX_CORE, phrase_attr, None)
    if text:
        speak(
            text=text,
            voice=VECTRAX_CORE.macos_voice,
            rate=VECTRAX_CORE.rate,
            wait=wait,
        )


# ---------------------------------------------------------------------------
# 4. Audio playback
# ---------------------------------------------------------------------------

def play_audio(path: str, wait: bool = True) -> None:
    """Play an audio file using macOS afplay."""
    if not Path(path).exists():
        logger.error("Audio file not found: %s", path)
        return

    try:
        if wait:
            subprocess.run(["afplay", path], timeout=120, check=False)
        else:
            subprocess.Popen(
                ["afplay", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except FileNotFoundError:
        logger.error("macOS 'afplay' not available")
    except Exception as exc:
        logger.warning("Audio playback failed: %s", exc)


# ---------------------------------------------------------------------------
# 5. Availability checks
# ---------------------------------------------------------------------------

def check_mic_available() -> bool:
    """Check if microphone recording is possible."""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        input_devices = [d for d in devices if d["max_input_channels"] > 0]
        return len(input_devices) > 0
    except Exception:
        return False


def check_tts_available() -> bool:
    """Check if macOS say command is available."""
    try:
        result = subprocess.run(
            ["say", "--version"],
            capture_output=True, timeout=5,
        )
        return True
    except Exception:
        return False


def get_voice_status() -> dict:
    """Return voice subsystem status."""
    mic = check_mic_available()
    tts = check_tts_available()

    # Check transcription backends
    transcription_backends = []
    try:
        from faster_whisper import WhisperModel
        transcription_backends.append("faster-whisper")
    except ImportError:
        pass
    try:
        result = subprocess.run(
            ["whisper", "--help"],
            capture_output=True, timeout=5,
        )
        if result.returncode == 0:
            transcription_backends.append("whisper-cli")
    except Exception:
        pass
    if os.environ.get("OPENAI_API_KEY"):
        transcription_backends.append("openai-api")

    return {
        "microphone": mic,
        "tts": tts,
        "tts_voice": _DEFAULT_VOICE,
        "tts_rate": _DEFAULT_RATE,
        "transcription_backends": transcription_backends,
        "ready": mic and tts and len(transcription_backends) > 0,
    }


# ---------------------------------------------------------------------------
# 6. Cleanup
# ---------------------------------------------------------------------------

def cleanup_temp_audio() -> int:
    """Remove temporary voice recording files. Returns count deleted."""
    count = 0
    if _AUDIO_DIR.exists():
        for f in _AUDIO_DIR.glob("voice_*.wav"):
            try:
                f.unlink()
                count += 1
            except Exception:
                pass
    return count
