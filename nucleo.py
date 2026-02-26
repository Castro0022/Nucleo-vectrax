from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()

@app.get("/preguntar")
def preguntar(q: str):
    return JSONResponse({
        "respuesta": f"Te escuché decir: {q}"
    })

if __name__ == "__main__":
    import uvicorn
    print("🧠 Núcleo activo en http://127.0.0.1:9005")
    uvicorn.run(app, host="127.0.0.1", port=9005)

