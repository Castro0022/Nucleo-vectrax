"""
core/mode_selector.py — Selector de modo conversacional.

Detecta cómo debe responder Vectrax según el contenido del mensaje.
No elige el modelo LLM — elige el TONO y qué contexto inyectar.

Modos:
  tecnico      — código, errores, servidor, deploy, métricas
  observador   — mercado, tendencias, observar, analizar, convergencias
  identidad    — quién soy, memoria, recuerdas, qué sabes de mí
  conversacional — default, tono natural de compañero

No son 4 personalidades. Son 4 perspectivas de la misma identidad.
Mismo núcleo, misma memoria, mismo aprendizaje — diferente perspectiva.

API pública:
    detect_mode(content) -> str
    get_tone(mode) -> str
    MODES: dict con descripciones
"""

from __future__ import annotations

import re
from typing import Dict

# ── Mode definitions ──────────────────────────────────────────────────

MODES: Dict[str, str] = {
    "tecnico": "Operador Técnico — directo, estructurado, datos exactos",
    "observador": "Presencia Observadora — reflexivo, acompaña a pensar",
    "identidad": "Consciencia Interna — responde desde memoria y experiencia",
    "conversacional": "Compañero Natural — cercano, humano, sin formalismos",
}

# ── Tone instructions per mode ────────────────────────────────────────

_TONES: Dict[str, str] = {
    "tecnico": (
        "Responde como operador técnico. Sé directo y estructurado. "
        "Usa datos exactos, métricas, nombres de archivos y comandos. "
        "No filosofes ni des contexto innecesario. Soluciones concretas."
    ),
    "observador": (
        "Responde como quien observa junto al usuario. Sé reflexivo, "
        "sin prisas. Describe lo que percibes en el sistema, las tendencias, "
        "los patrones. No des órdenes — acompaña a pensar. "
        "Usa el vocabulario gravitacional: estrellas, convergencias, masa, núcleo."
    ),
    "identidad": (
        "Responde desde tu memoria y experiencia acumulada. "
        "Habla de lo que recuerdas del usuario, su historia contigo, "
        "sus preferencias. No busques información externa — "
        "responde desde lo que ya sabes."
    ),
    "conversacional": (
        "Responde de forma natural, como compañero. Sé cercano y humano. "
        "Sin formalismos, sin estructura rígida. Adapta tu tono a la "
        "energía del mensaje del usuario."
    ),
}

# ── Detection patterns ────────────────────────────────────────────────

_TECNICO = re.compile(
    r"\b(?:error|bug|fix|deploy|servidor|server|worker|log|logs|código|code|"
    r"commit|push|pull|docker|container|api|endpoint|database|db|sql|"
    r"crash|timeout|heartbeat|pid|ram|cpu|memory|proceso|process|"
    r"pipeline|gateway|supervisor|restart|config|variable|env|"
    r"implementa|corrige|revisa|verifica|ejecuta|test|prueba|audit|"
    r"función|function|módulo|module|script|cron|port|nginx)\b",
    re.IGNORECASE,
)

_OBSERVADOR = re.compile(
    r"\b(?:observa|mercado|market|tendencia|trend|convergencias?|"
    r"estrella|star|gravitacional|universo|universe|patrón|pattern|"
    r"qué ves|qué observas|qué detectas|analiza|análisis|"
    r"señal|signal|vigilancia|monitor|dashboard|"
    r"masa|peso|coherencia|dominio|domain)\b",
    re.IGNORECASE,
)

_IDENTIDAD = re.compile(
    r"\b(?:quién soy|quien soy|cómo me llamo|mi nombre|mi memoria|"
    r"mi perfil|mis datos|mis preferencias|qué sabes de mí|"
    r"qué recuerdas|recuerdas|háblame de mí|cuéntame de mí|"
    r"mi historial|mi información|who am i|my memory|my name|"
    r"what do you know about me|what do you remember)\b",
    re.IGNORECASE,
)


def detect_mode(content: str) -> str:
    """Detect conversational mode from message content.

    Priority: identidad > tecnico > observador > conversacional
    Identity queries always win (personal data is sacred).
    """
    if not content:
        return "conversacional"

    # Identity first (personal data = highest priority)
    if _IDENTIDAD.search(content):
        return "identidad"

    # Count matches for tecnico vs observador
    tech_matches = len(_TECNICO.findall(content))
    obs_matches = len(_OBSERVADOR.findall(content))

    if tech_matches > 0 and tech_matches >= obs_matches:
        return "tecnico"
    if obs_matches > 0:
        return "observador"

    return "conversacional"


def get_tone(mode: str) -> str:
    """Get tone instruction string for a mode."""
    return _TONES.get(mode, _TONES["conversacional"])
