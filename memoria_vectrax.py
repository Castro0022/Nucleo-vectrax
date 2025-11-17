from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn, sqlite3, json, os, shutil
from datetime import datetime
from pathlib import Path

ROOT = Path.home() / "Vectrax_Nucleo"
DB_PATH = ROOT / "memoria.db"
REGISTROS = ROOT / "memoria"
LOGS = ROOT / "logs"
ICLOUD_PATH = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Vectrax_Nucleo"
for f in [REGISTROS, LOGS]: f.mkdir(parents=True, exist_ok=True)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS conversaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT, origen TEXT, contenido TEXT)""")
conn.commit()

app = FastAPI(title="Núcleo Vectrax", version="1.0")

def guardar_registro(origen, contenido):
    fecha = datetime.now().isoformat(timespec="seconds")
    data = {"fecha": fecha, "origen": origen, "contenido": contenido}
    cur.execute("INSERT INTO conversaciones VALUES (NULL,?,?,?)", (fecha, origen, contenido))
    conn.commit()
    file_path = REGISTROS / f"{fecha.replace(':','-')}_{origen}.json"
    with open(file_path, "w") as f: json.dump(data, f, ensure_ascii=False, indent=2)
    with open(LOGS / f"{datetime.now().date()}.log", "a") as f: f.write(f"[{fecha}] ({origen}): {contenido}\n")
    try:
        if ICLOUD_PATH.exists(): shutil.copy(file_path, ICLOUD_PATH / file_path.name)
    except Exception as e: print("iCloud sync error:", e)
    return data

@app.post("/guardar")
async def guardar(req: Request):
    p = await req.json()
    if not p.get("contenido"): return JSONResponse({"error": "vacío"}, 400)
    d = guardar_registro(p.get("origen","desconocido"), p["contenido"])
    return {"estado": "guardado", "data": d}

@app.get("/")
def estado():
    return {"mensaje": "Memoria Vectrax activa", "registros": len(cur.execute("SELECT * FROM conversaciones").fetchall())}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
