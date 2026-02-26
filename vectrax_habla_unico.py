import os, json, subprocess, sys, time
import requests

# --- Config ---
CORE = os.getenv("VECTRAX_CORE", "http://127.0.0.1:9005").rstrip("/")
VOICE = os.getenv("VECTRAX_VOICE", "http://127.0.0.1:9010").rstrip("/")
SECONDS = int(os.getenv("VECTRAX_SECONDS", "5"))

# --- STT (SpeechRecognition) ---
def stt_listen_once(seconds=5, lang="es-ES"):
    try:
        import speech_recognition as sr
    except Exception:
        return "__NO_SR__"

    r = sr.Recognizer()
    r.pause_threshold = 0.8

    try:
        with sr.Microphone() as source:
            # Ajuste rápido de ruido (no se queda calibrando)
            r.adjust_for_ambient_noise(source, duration=0.3)
            print(f"🎙️ Habla ahora ({seconds}s)…")
            audio = r.listen(source, phrase_time_limit=seconds)
    except Exception as e:
        return f"__MIC_ERROR__:{type(e).__name__}:{e}"

    try:
        return r.recognize_google(audio, language=lang)
    except sr.UnknownValueError:
        return ""
    except Exception as e:
        return f"__STT_ERROR__:{type(e).__name__}:{e}"

# --- Speak (prefer VOICE module, fallback to macOS say) ---
def speak(text: str):
    text = (text or "").strip()
    if not text:
        return
    # 1) intenta tu módulo de voz (9010)
    try:
        r = requests.post(f"{VOICE}/voz/hablar", json={"texto": text}, timeout=3)
        if r.ok:
            return
    except Exception:
        pass
    # 2) fallback: say (macOS)
    try:
        subprocess.run(["say", text])
    except Exception:
        pass

# --- Core call ---
def core_get(q: str):
    try:
        r = requests.get(f"{CORE}/preguntar", params={"q": q}, timeout=25)
        r.raise_for_status()
        # Tu Núcleo a veces devuelve json, a veces texto
        try:
            d = r.json()
            return d
        except Exception:
            return {"respuesta": r.text}
    except Exception as e:
        return {"respuesta": f"No pude conectar con el Núcleo en {CORE}. ({type(e).__name__})"}

def extract_answer(d: dict) -> str:
    # casos típicos de tu núcleo
    for k in ("respuesta", "mensaje"):
        if isinstance(d, dict) and d.get(k):
            return str(d.get(k))
    return str(d)

def compact(text: str, limit=900):
    t = (text or "").strip()
    if len(t) > limit:
        return t[:limit] + "…"
    return t

# --- Intents ---
def normalize(s: str) -> str:
    return " ".join((s or "").strip().split())

def is_save_intent(t: str) -> bool:
    x = t.lower().strip()
    return x.startswith("guarda ") or x.startswith("guardar ") or x.startswith("argos ") or x.startswith("nota ")

def build_query(t: str) -> str:
    t = normalize(t)
    if is_save_intent(t):
        # convierte "Guarda ..." en guardar: ...
        # para activar tu lógica existente en /preguntar
        if t.lower().startswith("guarda "):
            t = t[6:].strip()
        elif t.lower().startswith("guardar "):
            t = t[7:].strip()
        elif t.lower().startswith("argos "):
            t = t[6:].strip()
        elif t.lower().startswith("nota "):
            t = t[5:].strip()
        return "guardar: " + t
    return t

def main():
    print("✅ VECTRAX HABLA (ÚNICO) listo")
    print(f"   Núcleo: {CORE}")
    print(f"   Voz:   {VOICE} (si no está, uso 'say')")
    print("   Modo:  Push-To-Talk (ENTER para escuchar)")
    print("\nComandos:")
    print("  - ENTER → escuchar 5s y responder con voz")
    print("  - escribir texto + ENTER → preguntar sin mic")
    print("  - 'salir' + ENTER → cerrar")
    print("  - Para guardar: di o escribe: 'Guarda ...'\n")

    speak("Vectrax activo. Te escucho cuando tú quieras.")

    while True:
        try:
            typed = input("↩︎ ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nCerrando…")
            speak("Cerrando.")
            return

        if typed.lower() == "salir":
            speak("Listo. Cerrando.")
            return

        if typed:
            user_text = typed
        else:
            user_text = stt_listen_once(seconds=SECONDS)
            if user_text == "__NO_SR__":
                print("Falta SpeechRecognition. Instala:  pip3 install SpeechRecognition")
                speak("Me falta la librería de reconocimiento de voz.")
                continue
            if user_text.startswith("__MIC_ERROR__"):
                print(user_text)
                speak("No pude abrir el micrófono.")
                continue
            if user_text.startswith("__STT_ERROR__"):
                print(user_text)
                speak("Falló la transcripción.")
                continue
            if not user_text:
                print("…No se entendió.")
                continue

        print("TU:", user_text)

        q = build_query(user_text)
        d = core_get(q)
        ans = compact(extract_answer(d))

        print("VECTRAX:", ans)
        speak(ans)

if __name__ == "__main__":
    main()
