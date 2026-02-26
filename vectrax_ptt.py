#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import wave
import json
import shutil
import subprocess
import requests

import speech_recognition as sr
import pyaudio


CORE = os.getenv("VECTRAX_CORE", "http://127.0.0.1:9005").rstrip("/")
SECONDS = int(os.getenv("VECTRAX_SECONDS", "4"))
LANG = os.getenv("VECTRAX_LANG", "es-ES")
VOICE = os.getenv("VECTRAX_VOICE", "Jorge")   # prueba: Jorge / Monica / Paulina / etc
RATE = int(os.getenv("VECTRAX_RATE", "165"))  # velocidad del "say"
TMP_WAV = "/tmp/vectrax_ptt.wav"


def say(text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    # macOS "say" (una sola voz, cero duplicado)
    try:
        subprocess.run(
            ["say", "-v", VOICE, "-r", str(RATE), text],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def record_wav(seconds: int, out_path: str) -> None:
    # Grabación directa con PyAudio (sin sox, sin rec)
    pa = pyaudio.PyAudio()
    try:
        default_idx = pa.get_default_input_device_info()["index"]
    except Exception:
        default_idx = None

    fmt = pyaudio.paInt16
    channels = 1
    rate = 16000
    chunk = 1024

    stream = pa.open(
        format=fmt,
        channels=channels,
        rate=rate,
        input=True,
        input_device_index=default_idx,
        frames_per_buffer=chunk,
    )

    frames = []
    t_end = time.time() + seconds
    try:
        while time.time() < t_end:
            frames.append(stream.read(chunk, exception_on_overflow=False))
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()

    wf = wave.open(out_path, "wb")
    wf.setnchannels(channels)
    wf.setsampwidth(pyaudio.PyAudio().get_sample_size(fmt))
    wf.setframerate(rate)
    wf.writeframes(b"".join(frames))
    wf.close()


def stt_google(wav_path: str) -> str:
    r = sr.Recognizer()
    # Ajustes para que no “se trabe” con ambiente
    r.dynamic_energy_threshold = True
    r.pause_threshold = 0.8

    with sr.AudioFile(wav_path) as source:
        audio = r.record(source)

    try:
        return r.recognize_google(audio, language=LANG).strip()
    except sr.UnknownValueError:
        return ""
    except Exception:
        return ""


def ask_core(text: str) -> str:
    # Tu Núcleo REAL responde por /preguntar?q=...
    try:
        resp = requests.get(f"{CORE}/preguntar", params={"q": text}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        return (data.get("respuesta") or "").strip() or "No tengo respuesta ahora."
    except Exception as e:
        return f"No pude conectar con el Núcleo en {CORE}. ({type(e).__name__})"


def main() -> int:
    print("✅ Vectrax PTT listo (ENTER = hablar; escribir texto + ENTER; 'salir' para cerrar)")
    print(f"   Núcleo: {CORE}")
    print(f"   Voz:   {VOICE}  |  Segundos: {SECONDS}  |  Lang: {LANG}")
    print("")

    while True:
        try:
            entrada = input("⌁ ENTER = hablar | texto = escribir > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSaliendo.")
            return 0

        if entrada.lower() in ("salir", "exit", "quit"):
            say("Cerrando presencia.")
            return 0

        if entrada:
            texto = entrada
        else:
            print(f"🎙️  Grabando {SECONDS}s... (habla claro)")
            try:
                record_wav(SECONDS, TMP_WAV)
            except Exception as e:
                msg = f"No pude abrir el micrófono. ({type(e).__name__})"
                print("VECTRAX:", msg)
                say(msg)
                continue

            texto = stt_google(TMP_WAV)
            if not texto:
                msg = "No se entendió (ruido o muy bajo)."
                print("VECTRAX:", msg)
                say(msg)
                continue

        print("TU:", texto)

        respuesta = ask_core(texto)
        print("VECTRAX:", respuesta)
        say(respuesta)


if __name__ == "__main__":
    raise SystemExit(main())

