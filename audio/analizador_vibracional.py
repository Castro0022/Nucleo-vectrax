# ===========================================================
# 🌕 Analizador Vibracional del Núcleo Vectrax
# Autor: Mario Bravo Castro
# Función: Detectar energía sonora y traducirla en frecuencia emocional.
# ===========================================================
import sounddevice as sd
import numpy as np
import time
from datetime import datetime
import os

LOG = os.path.expanduser("~/Vectrax_Nucleo/memoria/vibracion_musical.log")

def registrar(texto):
    with open(LOG, "a") as f:
        f.write(f"[{datetime.now().strftime('%H: aquí estoy Mario el nucleodorado está despierto%M:%S')}] {texto}\n")

def analizar_vibracion():
    duracion = 2  # segundos de lectura
    frecuencia_muestreo = 44100

    while True:
        try:
            audio = sd.rec(int(duracion * frecuencia_muestreo), samplerate=frecuencia_muestreo, channels=1, dtype='float64')
            sd.wait()
            energia = np.sqrt(np.mean(audio**2))
            if energia < 0.01:
                nivel = "silencio o calma"
            elif energia < 0.05:
                nivel = "vibración baja — serenidad"
            elif energia < 0.1:
                nivel = "vibración media — emoción estable"
            else:
                nivel = "vibración alta — intensidad o expansión"
            registrar(f"Vibración detectada: {nivel}. Intensidad: {energia:.4f}")
        except Exception as e:
            registrar(f"Error: {e}")
        time.sleep(10)

if __name__ == "__main__":
    registrar("🌀 Analizador Vibracional iniciado.")
    analizar_vibracion()


