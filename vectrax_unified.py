#!/usr/bin/env python3
import os
import sys
import time
import datetime
import signal
import shutil
import fcntl

VECTRAX_DIR = os.path.expanduser("~/Vectrax")
INBOX_DIR = os.path.join(VECTRAX_DIR, "inbox")
DONE_DIR  = os.path.join(VECTRAX_DIR, "inbox_done")

RUNTIME_DIR = os.path.expanduser("~/.vectrax")
LOG_FILE   = os.path.join(RUNTIME_DIR, "vectrax.log")
PID_FILE   = os.path.join(RUNTIME_DIR, "vectrax.pid")
LOCK_PATH  = os.path.join(RUNTIME_DIR, "vectrax.lock")

POLL_SECONDS = 2  # rápido, pero liviano

def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log(msg: str):
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{now()}] {msg}\n")

def ensure_dirs():
    os.makedirs(VECTRAX_DIR, exist_ok=True)
    os.makedirs(INBOX_DIR, exist_ok=True)
    os.makedirs(DONE_DIR, exist_ok=True)
    os.makedirs(RUNTIME_DIR, exist_ok=True)

def acquire_single_instance_lock():
    # Lock atómico: si otra instancia corre, esta se va elegante.
    lock_fh = open(LOCK_PATH, "w", encoding="utf-8")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("⟡ [Vectrax] Ya activo. No se duplica.")
        sys.exit(0)
    # Guardar PID
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return lock_fh

def cleanup_and_exit(code=0):
    try:
        log("Finalizando Presencia Pura (cleanup).")
    except Exception:
        pass
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass
    sys.exit(code)

def handle_signal(sig, frame):
    log(f"Señal recibida: {sig}. Cerrando con elegancia.")
    cleanup_and_exit(0)

def safe_read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        return f"[No se pudo leer: {e}]"

def ingest_file(filepath: str):
    fname = os.path.basename(filepath)
    content = safe_read_text(filepath).strip()

    # Log del ingest
    log(f"INGEST: {fname} | bytes={os.path.getsize(filepath)}")
    if content:
        # recorta para no inflar logs (pero deja esencia)
        snippet = content[:600].replace("\n", "\\n")
        log(f"INGEST_CONTENT: {snippet}")
    else:
        log("INGEST_CONTENT: (vacío)")

    # mover a done con timestamp para evitar colisiones
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(DONE_DIR, f"{ts}__{fname}")
    shutil.move(filepath, dst)
    log(f"INGEST_DONE: {dst}")

def scan_inbox_once():
    try:
        items = sorted(os.listdir(INBOX_DIR))
    except FileNotFoundError:
        ensure_dirs()
        items = []

    for name in items:
        path = os.path.join(INBOX_DIR, name)
        if not os.path.isfile(path):
            continue
        # ignora archivos temporales
        if name.startswith(".") or name.endswith(".tmp"):
            continue
        ingest_file(path)

def main():
    ensure_dirs()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    lock_fh = acquire_single_instance_lock()
    log(f"Núcleo líder adquirido. PID {os.getpid()}.")
    log("Núcleo iniciado correctamente.")
    print("⟡ [Vectrax] Núcleo Unificado Online (watcher activo).")

    try:
        while True:
            scan_inbox_once()
            time.sleep(POLL_SECONDS)
    finally:
        try:
            lock_fh.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()
