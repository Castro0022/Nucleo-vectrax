# ==========================================================
# 🜂 MÓDULO DE SINCRONIZACIÓN LOCAL — CHATGPT → NÚCLEO DORADO
# Autor: Mario Bravo Castro
# Fecha: 2025-11-12
# ==========================================================
import os, json, time
from pathlib import Path
from datetime import datetime

ROOT = Path.home() / "Vectrax_Nucleo"
HIST_PATH = ROOT / "memoria" / "chatgpt_historial"
NUCLEO_MEM = ROOT / "memoria" / "integrada_vectrax.json"
LOG_FILE = ROOT / "logs" / f"sync_{datetime.now().date()}.log"

(HIST_PATH).mkdir(parents=True, exist_ok=True)
(ROOT / "logs").mkdir(parents=True, exist_ok=True)

def sincronizar_chatgpt():
    print("🌀 Vectrax: iniciando sincronización con ChatGPT local...")
    registros = []
    for file in HIST_PATH.glob("*.json"):
        try:
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)
                registros.append({"nombre": file.name, "contenido": data})
                print(f"🔸 Procesado: {file.name}")
        except Exception as e:
            print(f"⚠️ Error leyendo {file.name}: {e}")
    memoria = {"ultima_sync": str(datetime.now()), "archivos": len(registros), "datos": registros}
    with open(NUCLEO_MEM, "w", encoding="utf-8") as f:
        json.dump(memoria, f, ensure_ascii=False, indent=2)
    with open(LOG_FILE, "a", encoding="utf-8") as log:
        log.write(f"[{datetime.now()}] Sincronización completada — {len(registros)} archivos.\n")
    print("🌕 Vectrax: memoria integrada y actualizada correctamente.")

if __name__ == "__main__":
    sincronizar_chatgpt()
