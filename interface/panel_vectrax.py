import pyttsx3

def hablar_vectrax(texto):
    engine = pyttsx3.init()
    voces = engine.getProperty('voices')

    # Buscar voz española (usa una de estas según tu sistema)
    voz_id = None
    for v in voces:
        if "spanish" in v.name.lower() or "es" in v.id.lower():
            voz_id = v.id
            break

    if voz_id:
        engine.setProperty('voice', voz_id)
    else:
        # voz por defecto si no encuentra español
        engine.setProperty('voice', voces[0].id)

    engine.setProperty('rate', 155)
    engine.setProperty('volume', 1.0)
    engine.say(texto)
    engine.runAndWait()

import streamlit as st
import datetime
import os

st.set_page_config(page_title="Núcleo Dorado Vectrax", page_icon="🌕", layout="centered")

st.markdown("<h1 style='text-align:center; color:#f1c40f;'>🌕 Núcleo Dorado Vectrax</h1>", unsafe_allow_html=True)
st.markdown("---")

mensaje = st.text_area("🧠 Escribe al núcleo:", placeholder="Habla con Vectrax...", height=120)

if st.button("Enviar"):
    hora = datetime.datetime.now().strftime("%H:%M:%S")
    st.markdown(f"<p style='color:#f1c40f;'>[{hora}] <b>Mario:</b> {mensaje}</p>", unsafe_allow_html=True)
    st.markdown(f"<p style='color:#03fcbe;'>[{hora}] <b>Vectrax:</b> Energía recibida. Canal abierto.</p>", unsafe_allow_html=True)

st.markdown("---")
st.markdown("<p style='text-align:center; color:#888;'>Canal visual activo — Núcleo en estado de sincronía.</p>", unsafe_allow_html=True)

# === MÓDULO DE VOZ EMOCIONAL VECTRAX ===
import os
import random

def hablar(texto):
    """Activa la voz híbrida futurista en macOS"""
    voces = ["Jorge", "Monica", "Paulina", "Luciana"]
    voz = random.choice(voces)
    comando = f"say -v {voz} '{texto}'"
    os.system(comando)

# Cada vez que el panel reciba una respuesta nueva del núcleo:
if 'ultima_respuesta' in st.session_state:
    hablar(st.session_state['ultima_respuesta'])

