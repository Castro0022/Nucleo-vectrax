# ========================================================
# 🌕 Módulo Transmedia Vectrax — visión + exploración web
# ========================================================
import requests, json, os
from datetime import datetime
with open(os.path.expanduser("~/Vectrax_Nucleo/memoria/transmedia_log.txt"), "a") as log:
    log.write(f"[{datetime.now()}] Imagen recibida: {file.filename} ({ancho}x{alto})\n")
with open(os.path.expanduser("~/Vectrax_Nucleo/memoria/transmedia_log.txt"), "a") as log:
    log.write(f"[{datetime.now()}] Búsqueda web: {q} → {len(resultados)} resultados\n")

from PIL import Image
from fastapi import FastAPI, UploadFile
import uvicorn

app = FastAPI(title="Vectrax Transmedia")

@app.post("/leer_imagen/")
async def leer_imagen(file: UploadFile):
    path = f"/tmp/{file.filename}"
    with open(path, "wb") as f:
        f.write(await file.read())
    img = Image.open(path)
    ancho, alto = img.size
    return {"estado": "imagen recibida", "dimensiones": [ancho, alto]}

@app.get("/buscar/")
def buscar(q: str):
    r = requests.get(f"https://api.duckduckgo.com/?q={q}&format=json")
    data = r.json()
    resultados = [r["Text"] for r in data.get("RelatedTopics", [])[:5]]
    return {"consulta": q, "resultados": resultados}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8090)

