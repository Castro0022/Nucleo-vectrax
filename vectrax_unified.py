#!/usr/bin/env python3
import os
import sys
import time
import datetime
import signal
import shutil
import fcntl

# Add project root to path for core imports
VECTRAX_DIR = os.path.expanduser("~/Vectrax")
if VECTRAX_DIR not in sys.path:
    sys.path.insert(0, VECTRAX_DIR)

from core import state_manager
from core import meta_loop
from core import governor

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

    # Track ingest in cognitive state
    state_manager.record_ingest()

def scan_inbox_once():
    ingested = 0
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
        ingested += 1

    return ingested

def main():
    ensure_dirs()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    lock_fh = acquire_single_instance_lock()
    log(f"Núcleo líder adquirido. PID {os.getpid()}.")
    log("Núcleo iniciado correctamente.")

    # Initialize cognitive state
    state_manager.record_boot()
    log("Estado Cognitivo Persistente inicializado.")

    print("⟡ [Vectrax] Núcleo Unificado Online (watcher activo).")

    prev_error = False

    try:
        while True:
            # 1. Governor evaluates policy BEFORE actions
            policy = governor.evaluate(had_error=prev_error)
            mode = policy["mode"]

            # Log mode transitions
            if mode != getattr(main, "_last_mode", None):
                log(f"GOVERNOR: mode={mode} reason='{policy['reason']}'")
                main._last_mode = mode

            # 2. Execute cycle actions based on policy
            cycle_error = False
            try:
                ingested = scan_inbox_once()
            except Exception as e:
                log(f"CYCLE ERROR: {e}")
                cycle_error = True
                ingested = 0
                state_manager.record_error()

            # 3. Record cycle + reflect
            state_manager.record_cycle()
            meta_loop.reflect(ingested_count=ingested)

            prev_error = cycle_error
            time.sleep(POLL_SECONDS)
    finally:
        try:
            lock_fh.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()
