#!/usr/bin/env python3
"""
Vectrax Voice — Diagnóstico end-to-end ejecutable.

Etapas:
  1. Micrófono: grabar 3 segundos
  2. Transcripción: Whisper (faster-whisper → CLI → OpenAI API)
  3. Respuesta: CoreCommEngine genera respuesta
  4. TTS: say genera audio con identidad VECTRAX-CORE
  5. Reproducción: afplay reproduce el archivo

Si falla, imprime la etapa exacta, archivo, función y error.
"""
import os
import sys
import subprocess
import tempfile
import time

# Ensure project is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

STAGES = []


def stage(name):
    """Decorator to mark a diagnostic stage."""
    def decorator(fn):
        STAGES.append((name, fn))
        return fn
    return decorator


# ────────────────────────────────────────────────────────────────
# STAGE 1: Micrófono
# ────────────────────────────────────────────────────────────────
@stage("1. MICRÓFONO — grabar 3s")
def test_microphone():
    try:
        import sounddevice as sd
    except ImportError as e:
        return False, f"sounddevice no instalado: {e}", "voice.py", "MicRecorder.start()"

    try:
        import numpy as np
        import soundfile as sf
    except ImportError as e:
        return False, f"numpy/soundfile no instalado: {e}", "voice.py", "MicRecorder.stop()"

    devices = sd.query_devices()
    input_devs = [d for d in devices if d["max_input_channels"] > 0]
    if not input_devs:
        return False, "Ningún dispositivo de entrada detectado", "voice.py", "check_mic_available()"

    print(f"  Dispositivo: {input_devs[0]['name']}")
    print(f"  Grabando 3 segundos...")

    try:
        audio = sd.rec(int(3 * 16000), samplerate=16000, channels=1, dtype="float32")
        sd.wait()
    except Exception as e:
        return False, f"Error en grabación: {type(e).__name__}: {e}", "voice.py", "MicRecorder.start()"

    if audio is None or len(audio) == 0:
        return False, "No se capturaron frames de audio", "voice.py", "MicRecorder.stop()"

    duration = len(audio) / 16000
    peak = float(np.max(np.abs(audio)))
    print(f"  Duración: {duration:.1f}s | Peak: {peak:.4f}")

    if peak < 0.001:
        print(f"  ⚠ Audio muy bajo (peak={peak:.4f}) — posible micrófono en silencio o bloqueado")

    # Save to temp file
    out_path = os.path.join(tempfile.gettempdir(), "vectrax_diag_mic.wav")
    sf.write(out_path, audio, 16000)
    print(f"  Archivo: {out_path}")

    return True, out_path, None, None


# ────────────────────────────────────────────────────────────────
# STAGE 2: Transcripción
# ────────────────────────────────────────────────────────────────
@stage("2. TRANSCRIPCIÓN — Whisper")
def test_transcription():
    wav_path = os.path.join(tempfile.gettempdir(), "vectrax_diag_mic.wav")
    if not os.path.exists(wav_path):
        return False, f"Archivo de audio no existe: {wav_path}", "voice.py", "transcribe()"

    # Try faster-whisper
    try:
        from faster_whisper import WhisperModel
        print("  Backend: faster-whisper")
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(wav_path, language="es")
        text = " ".join(seg.text.strip() for seg in segments)
        if text:
            print(f"  Transcripción: «{text}»")
            return True, text, None, None
        else:
            return False, "faster-whisper: transcripción vacía", "voice.py", "transcribe() → faster-whisper"
    except ImportError:
        print("  faster-whisper: no instalado")
    except Exception as e:
        print(f"  faster-whisper falló: {e}")

    # Try whisper CLI
    try:
        r = subprocess.run(
            ["whisper", wav_path, "--language", "es", "--model", "base", "--output_format", "txt"],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0:
            from pathlib import Path
            txt_path = Path(wav_path).with_suffix(".txt")
            if txt_path.exists():
                text = txt_path.read_text().strip()
                txt_path.unlink(missing_ok=True)
                if text:
                    print(f"  Backend: whisper CLI")
                    print(f"  Transcripción: «{text}»")
                    return True, text, None, None
    except FileNotFoundError:
        print("  whisper CLI: no instalado")
    except Exception as e:
        print(f"  whisper CLI falló: {e}")

    # Try OpenAI API
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        try:
            import openai
            print("  Backend: OpenAI Whisper API")
            client = openai.OpenAI()
            with open(wav_path, "rb") as f:
                response = client.audio.transcriptions.create(
                    model="whisper-1", file=f, language="es",
                )
            if response.text:
                print(f"  Transcripción: «{response.text.strip()}»")
                return True, response.text.strip(), None, None
            else:
                return False, "OpenAI API: transcripción vacía", "voice.py", "transcribe() → openai"
        except ImportError:
            print("  openai SDK: no instalado")
        except Exception as e:
            return False, f"OpenAI API falló: {e}", "voice.py", "transcribe() → openai"
    else:
        print("  OPENAI_API_KEY: no configurada")

    return (
        False,
        "NINGÚN backend de transcripción disponible. "
        "Instala: pip install faster-whisper  O  pip install openai + export OPENAI_API_KEY=...",
        "vectrax/comm/voice.py",
        "transcribe()",
    )


# ────────────────────────────────────────────────────────────────
# STAGE 3: CoreCommEngine respuesta
# ────────────────────────────────────────────────────────────────
@stage("3. RESPUESTA — CoreCommEngine")
def test_response():
    try:
        from vectrax.comm.engine_core import CoreCommEngine
    except ImportError as e:
        return False, f"No se puede importar CoreCommEngine: {e}", "vectrax/comm/engine_core.py", "import"

    try:
        engine = CoreCommEngine(session_id="diag-voice-test")
        result = engine.send_text("prueba de diagnóstico de voz")
        response = result.get("response", "")
        print(f"  Respuesta: «{response[:80]}{'…' if len(response) > 80 else ''}»")
        if response:
            return True, response, None, None
        else:
            return False, "CoreCommEngine devolvió respuesta vacía", "engine_core.py", "send_text()"
    except Exception as e:
        return False, f"CoreCommEngine falló: {type(e).__name__}: {e}", "engine_core.py", "send_text()"


# ────────────────────────────────────────────────────────────────
# STAGE 4: TTS — say con identidad VECTRAX-CORE
# ────────────────────────────────────────────────────────────────
@stage("4. TTS — say con VECTRAX-CORE")
def test_tts():
    try:
        from vectrax.comm.voice_identity import VECTRAX_CORE
    except ImportError as e:
        return False, f"No se puede importar VECTRAX_CORE: {e}", "voice_identity.py", "import"

    voice = VECTRAX_CORE.macos_voice
    rate = VECTRAX_CORE.rate
    test_text = "Diagnóstico de voz completado exitosamente."
    out_path = os.path.join(tempfile.gettempdir(), "vectrax_diag_tts.aiff")

    print(f"  Voz: {voice} | Rate: {rate} wpm")

    try:
        r = subprocess.run(
            ["say", "-v", voice, "-r", str(rate), "-o", out_path, test_text],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return False, f"say retornó código {r.returncode}: {r.stderr}", "voice.py", "speak()"

        if not os.path.exists(out_path):
            return False, "say no generó archivo de audio", "voice.py", "speak()"

        size = os.path.getsize(out_path)
        print(f"  Archivo: {out_path} ({size} bytes)")
        if size < 100:
            return False, f"Archivo de audio demasiado pequeño ({size} bytes)", "voice.py", "speak()"

        return True, out_path, None, None

    except FileNotFoundError:
        return False, "Comando 'say' no encontrado", "voice.py", "speak()"
    except Exception as e:
        return False, f"TTS falló: {type(e).__name__}: {e}", "voice.py", "speak()"


# ────────────────────────────────────────────────────────────────
# STAGE 5: Reproducción — afplay
# ────────────────────────────────────────────────────────────────
@stage("5. REPRODUCCIÓN — afplay")
def test_playback():
    audio_path = os.path.join(tempfile.gettempdir(), "vectrax_diag_tts.aiff")
    if not os.path.exists(audio_path):
        return False, f"Archivo TTS no existe: {audio_path}", "voice.py", "play_audio()"

    print(f"  Reproduciendo: {audio_path}")
    try:
        r = subprocess.run(["afplay", audio_path], timeout=15, capture_output=True, text=True)
        if r.returncode != 0:
            return False, f"afplay retornó código {r.returncode}: {r.stderr}", "voice.py", "play_audio()"
        print("  Reproducción completada")
        return True, "OK", None, None
    except FileNotFoundError:
        return False, "Comando 'afplay' no encontrado", "voice.py", "play_audio()"
    except subprocess.TimeoutExpired:
        return False, "afplay timeout (>15s)", "voice.py", "play_audio()"
    except Exception as e:
        return False, f"Reproducción falló: {type(e).__name__}: {e}", "voice.py", "play_audio()"


# ────────────────────────────────────────────────────────────────
# Runner
# ────────────────────────────────────────────────────────────────
def main():
    print("╔════════════════════════════════════════════════════════╗")
    print("║  VECTRAX — Diagnóstico de Voz End-to-End             ║")
    print("╚════════════════════════════════════════════════════════╝")
    print()

    for name, fn in STAGES:
        print(f"── {name} ──")
        ok, detail, file_loc, func_loc = fn()

        if ok:
            print(f"  ✓ PASÓ\n")
        else:
            print(f"\n  ✗ FALLO en esta etapa")
            print(f"  Error:    {detail}")
            if file_loc:
                print(f"  Archivo:  {file_loc}")
            if func_loc:
                print(f"  Función:  {func_loc}")
            print()
            print("═" * 56)
            print(f"PRIMER FALLO: {name}")
            print(f"  → {detail}")
            if file_loc:
                print(f"  → {file_loc} :: {func_loc}")
            print("═" * 56)
            sys.exit(1)

    print("═" * 56)
    print("✓ TODAS LAS ETAPAS PASARON — Voz end-to-end funcional")
    print("═" * 56)


if __name__ == "__main__":
    main()
