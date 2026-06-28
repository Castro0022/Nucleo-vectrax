#!/usr/bin/env python3
"""
Vectrax — Chat de terminal
============================
Cliente simple que consume el API local (/v1/chat). Imprime la respuesta,
opcionalmente la dice en voz alta (macOS `say`, voz Reed), y como pasa por
/v1/chat dispara el Gravity Kernel en shadow (source=web_chat) del lado del
core_api.

No duplica lógica del núcleo: solo consume el API existente.

Uso:
    python3 scripts/vx_chat.py                 # login mario, voz ON
    python3 scripts/vx_chat.py --no-voice      # sin voz
    python3 scripts/vx_chat.py --user mario --base http://127.0.0.1:8900

Dentro del chat:
    /voz     activar voz        /novoz   desactivar voz
    /salir   salir (o Ctrl-C)
"""
from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys

import httpx

VOICE_NAME = "Reed"   # es_MX (VECTRAX-CORE)
VOICE_RATE = "175"


def _speak(text: str) -> None:
    """Habla en background con macOS `say`. No bloquea, nunca rompe."""
    if not text:
        return
    try:
        subprocess.Popen(
            ["say", "-v", VOICE_NAME, "-r", VOICE_RATE, text],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Vectrax terminal chat")
    ap.add_argument("--base", default="http://127.0.0.1:8900")
    ap.add_argument("--user", default="mario")
    ap.add_argument("--no-voice", action="store_true", help="desactivar voz")
    args = ap.parse_args()

    voice_on = not args.no_voice
    password = os.environ.get("VX_PASS") or getpass.getpass(f"Password de {args.user}: ")

    client = httpx.Client(timeout=60, base_url=args.base)

    # --- Login ---
    try:
        r = client.post("/v1/auth/login", json={"username": args.user, "password": password})
    except Exception as exc:
        print(f"[!] No pude conectar a {args.base}: {exc}")
        return 2
    if r.status_code != 200:
        print(f"[!] Login falló ({r.status_code}): {r.text[:200]}")
        return 1
    token = r.json().get("token")
    client.headers["Authorization"] = f"Bearer {token}"
    print(f"✓ Conectado a {args.base} como {args.user} | voz: {'ON' if voice_on else 'OFF'}")
    print("  Escribe tu mensaje. Comandos: /voz /novoz /salir\n")

    # --- Loop ---
    try:
        while True:
            try:
                text = input("tú › ").strip()
            except EOFError:
                break
            if not text:
                continue
            if text in ("/salir", "/exit", "/quit"):
                break
            if text == "/voz":
                voice_on = True; print("[voz ON]"); continue
            if text == "/novoz":
                voice_on = False; print("[voz OFF]"); continue

            try:
                resp = client.post("/v1/chat", json={"text": text})
            except Exception as exc:
                print(f"[!] Error de red: {exc}")
                continue
            if resp.status_code != 200:
                print(f"[!] /v1/chat {resp.status_code}: {resp.text[:200]}")
                continue

            d = resp.json()
            answer = d.get("sovereign_answer") or d.get("message") or "(sin respuesta)"
            mode = d.get("resolve_mode", "?")
            print(f"\nvectrax ({mode}) › {answer}\n")
            if voice_on:
                _speak(answer)
    except KeyboardInterrupt:
        pass
    finally:
        client.close()
    print("\n— sesión cerrada —")
    return 0


if __name__ == "__main__":
    sys.exit(main())
