#!/usr/bin/env python3
# Vectrax Presencia PTT (push-to-talk): NO escucha continuo.
#  - Tú presionas ENTER -> escucha N segundos -> consulta Núcleo -> habla con 'say'
#  - Evita que se escriba nada en nano porque no usa Dictado macOS.

import os, sys, json, time, shutil, subprocess
import requests
import speech_recognition as sr

CORE = os.environ.get("VECTRAX_CORE", "http://127.0.0.1:9005").rstrip("/")
LANG = os.environ.get("VECTRAX_LANG", "es-ES")
SECONDS = float(os.environ.get("VECTRAX_SECONDS", "4"))
VOICE = os.environ.get("VECTRAX_VOICE_NAME", "")  # opcional: nombre de voz macOS

def say(text: str):
    text = (text or "").strip()
    if not text:
        return
    # macOS TTS
    cmd = ["say"]
    if VOICE:
        cmd += ["-v", VOICE]
    cmd += [text]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def force_system_flac():
    """
    Evita el crash 'Bad CPU type' del flac-mac empaquetado por SpeechRecognition.
    Forzamos a usar el flac del sistema (brew): /opt/homebrew/bin/flac
    """
    import speech_recognition.audio as sra
    def _get_flac_converter():
        path = shutil.which("flac")
        if not path:
            raise RuntimeError("No encuentro 'flac' en PATH. Instala con: brew install flac")
        return path
    sra.get_flac_converter = _get_flac_converter

def core_preguntar(q: str) -> str:
    try:
        r = requests.get(f"{CORE}/preguntar", params={"q": q}, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return f"No pude conectar con el Núcleo en {CORE}. ({e.__class__.__name__})"

    # Prioridad: si guardó, confirmar. Si hay respuesta, usarla.
    if data.get("ok") and isinstance(data.get("guardado"), dict):
        gid = data["guardado"].get("id", "")
        return f"Guardado en ARGOS. {gid}".strip()

    return (data.get("respuesta")
            or data.get("mensaje")
            or json.dumps(data, ensure_ascii=False))

def normalize_intent(texto: str) -> str:
    t = (texto or "").strip()
    low = t.lower()

    # comandos de salida
    if low in ("salir", "exit", "quit", "stop"):
        return "__EXIT__"

    # “guarda ...” -> guardar: ...
    # (sirve para tu flujo de principios, reglas, huellas)
    if low.startswith("guarda "):
        return "guardar: " + t[6:].strip()
    if low.startswith("guardar "):
        return "guardar: " + t[7:].strip()

    # “buscar ...” -> se lo mandamos tal cual al núcleo
    return t

def escuchar_una_vez(rec: sr.Recognizer, mic: sr.Microphone) -> str:
    with mic as source:
        # Ajustes para ambiente (sin volverse loco)
        rec.dynamic_energy_threshold = False
        rec.energy_threshold = int(os.environ.get("VECTRAX_ENERGY", "450"))
        rec.pause_threshold = float(os.environ.get("VECTRAX_PAUSE", "0.9"))

        print(f"🎙️ Habla ahora ({SECONDS:.0f}s)...", flush=True)
        audio = rec.listen(source, phrase_time_limit=SECONDS)

    try:
        txt = rec.recognize_google(audio, language=LANG)
        return (txt or "").strip()
    except sr.UnknownValueError:
        return ""
    except Exception as e:
        return f"__ERR__:{e.__class__.__name__}"

def main():
    # fuerza flac del sistema para evitar “Bad CPU type”
    force_system_flac()

    # Chequeos mínimos
    if not shutil.which("flac"):
        print("❌ Falta 'flac'. Instala: brew install flac")
        sys.exit(1)

    print("✅ Vectrax PTT listo.")
    print(f"   Núcleo: {CORE}")
    print("   Modo: Presiona ENTER para hablar. Escribe 'salir' para cerrar.\n")

    say("Vectrax está presente. Presiona enter y habla.")

    rec = sr.Recognizer()
    mic = sr.Microphone()

    while True:
        try:
            input("⏎ ENTER = hablar | escribe 'salir' y ENTER = cerrar > ")
        except (KeyboardInterrupt, EOFError):
            print("\n👋 Cerrando.")
            break

        txt = escuchar_una_vez(rec, mic)

        if txt.startswith("__ERR__:"):
            err = txt.split(":", 1)[1]
            print(f"❌ Error de audio: {err}")
            say("Hubo un error de audio. Revisa permisos de micrófono.")
            continue

        if not txt:
            print("…No se entendió (ruido o muy bajo).")
            say("No te entendí. Repite, claro y fuerte.")
            continue

        print(f"TU: {txt}")

        q = normalize_intent(txt)
        if q == "__EXIT__":
            say("Cierro presencia.")
            break

        resp = core_preguntar(q)
        print(f"VECTRAX: {resp}\n")
        # recorta un poco por si núcleo devuelve bloques gigantes
        speak = resp.strip()
        if len(speak) > 350:
            speak = speak[:350].rsplit(" ", 1)[0] + "."
        say(speak)

if __name__ == "__main__":
    main()
