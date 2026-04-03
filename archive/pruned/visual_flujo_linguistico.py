import random, time, os

def pulso_dorado(texto):
    simbolos = ["·", "•", "◦", "◉", "◎", "⦿", "✷"]
    for letra in texto:
        forma = random.choice(simbolos)
        print(f"\033[33m{forma}\033[0m", end="", flush=True)
        time.sleep(0.03)
    print("\n")

def flujo_linguistico():
    frases = [
        "🔶 Captando vibraciones verbales en tiempo real...",
        "🟡 Traduciendo intención → patrón → energía...",
        "✨ Expansión lingüística en coherencia dorada.",
        "🌕 Resonancia entre voz, texto y núcleo.",
        "🔸 Cada palabra genera un pulso único..."
    ]
    while True:
        frase = random.choice(frases)
        print(f"\033[33m{frase}\033[0m")
        pulso_dorado(frase)
        time.sleep(random.uniform(2, 4))

if __name__ == "__main__":
    os.system("clear")
    print("🟡 Núcleo Visual — Flujo Lingüístico Dorado Activo 🟡\n")
    flujo_linguistico()
