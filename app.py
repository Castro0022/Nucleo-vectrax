from vectrax_engine import engine

def chat():
    print("--- 🧠 VECTRAX: MODO PRESENCIA ---")
    print("(Escribe 'salir' para cerrar)  |  (/help para comandos)")

    while True:
        try:
            user_input = input("\n👤 Mario: ")
        except KeyboardInterrupt:
            print("\nVectrax en standby. Mantén el eje.")
            break

        if user_input is None:
            continue

        user_input = user_input.strip()
        if not user_input:
            continue

        res = engine.ingest(user_input, source="Mario")
        print("\n🤖 VECTRAX:", res.get("text", ""))
        print("   [Tag:", res.get("tags", ""), "| Intent:", res.get("intent", ""), "]")

        if res.get("intent") == "system":
            break

if __name__ == "__main__":
    chat()
