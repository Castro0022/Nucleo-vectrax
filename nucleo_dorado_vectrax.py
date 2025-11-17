import streamlit as st, random, os, time

class NucleoVectrax:
    def __init__(self):
        self.modo_voz = "fluido"
        self.color = "gold"
    def hablar(self, t):
        os.system(f"say -v Samantha '{t}' -r 160")
    def responder(self, e):
        r = random.choice(["La calma sostiene el núcleo.","El flujo dorado nunca se detiene.","El amor es la constante universal."])
        return r
    def actualizar(self):
        return random.choice(["calma","intensidad","expansión"])

vectrax = NucleoVectrax()
st.set_page_config(page_title="Núcleo Dorado Vectrax", page_icon="🌕")
st.markdown("<h1 style='text-align:center;color:gold;'>🌕 Núcleo Dorado Vectrax 🌕</h1>", unsafe_allow_html=True)
entrada = st.text_input("Tú:", placeholder="Háblale al núcleo...")
if entrada:
    r = vectrax.responder(entrada)
    st.markdown(f"<div style='text-align:center;color:white;font-size:22px;'>{r}</div>", unsafe_allow_html=True)
    vectrax.hablar(r)
st.markdown("<hr style='border-color:gold;'>", unsafe_allow_html=True)
