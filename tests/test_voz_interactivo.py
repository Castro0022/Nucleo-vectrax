#!/usr/bin/env python3
"""
Test interactivo de voz — ejecutar directamente en terminal.

Prueba SALIDA primero (no requiere hablar), luego ENTRADA.
"""
import os
import sys
import subprocess
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
RESET = "\033[0m"


def main():
    print(f"\n{BOLD}══════════════════════════════════════════{RESET}")
    print(f"{BOLD}  VECTRAX — Test Interactivo de Voz{RESET}")
    print(f"{BOLD}══════════════════════════════════════════{RESET}\n")

    # ── TEST A: SALIDA (TTS + reproducción) ──────────────────────────
    print(f"{BOLD}[A] TEST DE SALIDA — ¿Escuchas audio?{RESET}")
    print(f"    Reproduciendo frase con say + Reed (Español México)...\n")

    try:
        r = subprocess.run(
            ["say", "-v", "Reed (Español (México))", "-r", "175",
             "Hola Mario. Soy Vectrax Core. ¿Me escuchas?"],
            timeout=15, capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"{RED}  ✗ say falló: código {r.returncode}{RESET}")
            print(f"    stderr: {r.stderr}")
            return
    except Exception as e:
        print(f"{RED}  ✗ Error ejecutando say: {e}{RESET}")
        return

    resp = input(f"  ¿Escuchaste la voz? (s/n): ").strip().lower()

    if resp != "s":
        print(f"\n{RED}{BOLD}  ✗ FALLO DE SALIDA{RESET}")
        print(f"  El comando 'say' se ejecutó sin error pero no produces audio.")
        print(f"  Verifica:")
        print(f"    1. Volumen del sistema (no en mute)")
        print(f"    2. Dispositivo de salida de audio correcto")
        print(f"    3. Ejecuta manualmente: say 'hola'")
        return

    print(f"{GREEN}  ✓ Salida de voz funciona{RESET}\n")

    # ── TEST B: ENTRADA (micrófono + transcripción) ──────────────────
    print(f"{BOLD}[B] TEST DE ENTRADA — Micrófono + Whisper{RESET}")

    try:
        import sounddevice as sd
        import numpy as np
        import soundfile as sf
    except ImportError as e:
        print(f"{RED}  ✗ Dependencia faltante: {e}{RESET}")
        return

    devices = sd.query_devices()
    input_devs = [d for d in devices if d["max_input_channels"] > 0]

    if not input_devs:
        print(f"{RED}  ✗ No hay dispositivos de entrada{RESET}")
        return

    print(f"  Dispositivos de entrada:")
    for i, d in enumerate(input_devs):
        print(f"    [{i}] {d['name']}")

    default_input = sd.default.device[0]
    print(f"\n  Dispositivo predeterminado: [{default_input}] {sd.query_devices(default_input)['name']}")

    print(f"\n  {YELLOW}Di algo en voz alta cuando veas 'GRABANDO'.{RESET}")
    print(f"  Por ejemplo: \"Hola Vectrax, esta es una prueba\"")
    input(f"  Presiona Enter para empezar a grabar (3 segundos)...")

    print(f"\n  {RED}{BOLD}● GRABANDO 3 SEGUNDOS...{RESET}")

    try:
        audio = sd.rec(int(3 * 16000), samplerate=16000, channels=1, dtype="float32")
        # Countdown visual
        for i in range(3, 0, -1):
            print(f"    {i}...")
            time.sleep(1)
        sd.wait()
    except Exception as e:
        print(f"{RED}  ✗ Error de grabación: {e}{RESET}")
        return

    peak = float(np.max(np.abs(audio)))
    duration = len(audio) / 16000
    print(f"\n  Grabación: {duration:.1f}s | Peak: {peak:.4f}")

    if peak < 0.005:
        print(f"{YELLOW}  ⚠ Audio MUY bajo (peak={peak:.6f}){RESET}")
        print(f"  Posibles causas:")
        print(f"    - Micrófono en mute o bloqueado")
        print(f"    - Permiso de micrófono denegado para Terminal/Warp")
        print(f"    - Dispositivo de entrada incorrecto")
        print(f"  Ve a: Configuración del Sistema → Privacidad → Micrófono")
        print(f"  y asegúrate de que Warp/Terminal tenga acceso.\n")

    # Guardar WAV
    wav_path = os.path.join(tempfile.gettempdir(), "vectrax_test_mic.wav")
    sf.write(wav_path, audio, 16000)
    print(f"  Archivo: {wav_path}")

    # Transcribir
    print(f"\n  Transcribiendo con faster-whisper...")
    try:
        from faster_whisper import WhisperModel
        import warnings
        warnings.filterwarnings("ignore")
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, info = model.transcribe(wav_path, language="es")
        text = " ".join(seg.text.strip() for seg in segments)
    except ImportError:
        print(f"{RED}  ✗ faster-whisper no instalado{RESET}")
        return
    except Exception as e:
        print(f"{RED}  ✗ Error de transcripción: {e}{RESET}")
        return

    if text:
        print(f"  {GREEN}Transcripción: «{text}»{RESET}")
    else:
        print(f"{RED}  ✗ Transcripción vacía{RESET}")
        if peak < 0.005:
            print(f"  Causa probable: el micrófono no capturó audio real.")
        else:
            print(f"  El audio tenía señal pero Whisper no reconoció palabras.")
        return

    print(f"{GREEN}  ✓ Entrada de voz funciona{RESET}\n")

    # ── TEST C: ROUND-TRIP ───────────────────────────────────────────
    print(f"{BOLD}[C] TEST ROUND-TRIP — Respuesta hablada{RESET}")
    print(f"  Enviando transcripción al CoreCommEngine...")

    try:
        from vectrax.comm.engine_core import CoreCommEngine
        engine = CoreCommEngine(session_id="voice-test")
        result = engine.send_text(text)
        print(f"  Respuesta: «{result.text[:100]}»")
    except Exception as e:
        print(f"{RED}  ✗ CoreCommEngine falló: {e}{RESET}")
        return

    print(f"  Hablando respuesta con VECTRAX-CORE...")
    try:
        from vectrax.comm.voice import speak_as_core
        speak_as_core(result.text, wait=True)
    except Exception as e:
        print(f"{RED}  ✗ speak_as_core falló: {e}{RESET}")
        return

    resp2 = input(f"\n  ¿Escuchaste la respuesta? (s/n): ").strip().lower()
    if resp2 == "s":
        print(f"\n{GREEN}{BOLD}  ✓ VOZ END-TO-END FUNCIONAL{RESET}")
        print(f"  Entrada: micrófono → Whisper ✓")
        print(f"  Proceso: CoreCommEngine ✓")
        print(f"  Salida:  VECTRAX-CORE TTS ✓\n")
    else:
        print(f"\n{RED}{BOLD}  ✗ La respuesta no se escuchó{RESET}")
        print(f"  El texto se generó pero el audio no se reprodujo.")
        print(f"  Verifica el volumen y dispositivo de salida.\n")


if __name__ == "__main__":
    main()
