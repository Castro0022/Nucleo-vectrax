"""
core/operator/certainty.py — Declaración honesta de certeza (Fase 2).

Cuando la convergencia/confianza interna es FUERTE, Vectrax responde sin
salvedades. Cuando es DÉBIL, lo declara — "con lo que tengo no me alcanza
para asegurarlo" — en vez de fingir seguridad.

Principios:
  - NUNCA bloquea: declarar duda ≠ callar. Si hay respuesta, se devuelve.
  - Solo actúa en rutas GENERATIVAS no respaldadas (llm/online/local/…).
    Las rutas grounded (memoria, criterio de dominio, evidencia freight,
    mercado, lugares, identidad, self-aware, saludos) NUNCA llevan salvedad.
  - No mezcla idiomas: si el idioma del usuario no tiene plantilla, no añade
    salvedad (evita violar la consistencia de idioma).
  - No duplica: si la respuesta ya trae una salvedad, no añade otra.

La confianza la provee la Fase 1 (procedencia por-respuesta): confianza real
del SmartRouter, o grounded por-ruta.

Creador: Mario Bravo Castro
Fecha: 2026-08-08
"""
from __future__ import annotations

import os

# Umbral por debajo del cual una ruta generativa declara su límite.
# Con el mapeo de la Fase 1 (llm=0.7, online real=confianza del router), esto
# dispara la salvedad SOLO cuando el router está genuinamente inseguro.
CERTAINTY_FLOOR = float(os.environ.get("VECTRAX_CERTAINTY_FLOOR", "0.5"))

# Rutas grounded: respuesta respaldada por evidencia/memoria o trivial.
# Nunca declaran incertidumbre (su confianza ya es alta por diseño).
_GROUNDED_ROUTES = {
    "memory", "self_aware", "greeting", "fast", "identity",
    "intake_store", "intake_filter", "intake_store_discarded",
    "places", "market", "freight", "freight_evidence",
}

# Salvedad breve, natural, en primera persona, por idioma soportado.
_LEAD = {
    "es": "Con lo que tengo ahora no me alcanza para asegurarlo, pero esto es lo que veo:",
    "en": "With what I have right now I can't fully assure it, but here's what I see:",
    "fr": "Avec ce que j'ai pour l'instant, je ne peux pas l'affirmer, mais voici ce que je vois :",
    "it": "Con quello che ho ora non posso assicurarlo, ma ecco cosa vedo:",
    "de": "Mit dem, was ich gerade habe, kann ich es nicht sicher sagen, aber das sehe ich:",
    "pt": "Com o que tenho agora não posso garantir, mas é isto que vejo:",
}

# Marcadores para no duplicar salvedades ya presentes (best-effort, es/en).
_HEDGE_MARKERS = (
    "no me alcanza para asegurar", "no estoy segur", "no lo doy por seguro",
    "can't fully assure", "cannot fully assure", "i'm not sure", "not certain",
)


def _is_grounded(route: str) -> bool:
    """True si la ruta está respaldada por evidencia/memoria (no generativa)."""
    if not route:
        return False
    r = route.lower()
    return r in _GROUNDED_ROUTES or r.startswith("criterion")


def needs_uncertainty(confidence: float, route: str) -> bool:
    """True si la respuesta debería declarar su límite de certeza."""
    if _is_grounded(route):
        return False
    try:
        return float(confidence) < CERTAINTY_FLOOR
    except (TypeError, ValueError):
        return False


def declare_certainty(
    response_text: str,
    confidence: float,
    route: str,
    lang: str = "es",
) -> str:
    """Antepone una declaración honesta de baja certeza cuando corresponde.

    NUNCA bloquea: si `response_text` está vacío se devuelve tal cual. No
    duplica salvedades. Solo actúa en rutas NO grounded con confianza por
    debajo de CERTAINTY_FLOOR y para idiomas con plantilla (sin mezclar).
    """
    if not response_text or not response_text.strip():
        return response_text
    if not needs_uncertainty(confidence, route):
        return response_text
    lead = _LEAD.get((lang or "es").lower())
    if not lead:
        # Idioma sin plantilla: no añadir salvedad para no mezclar idiomas.
        return response_text
    head = response_text[:140].lower()
    if any(m in head for m in _HEDGE_MARKERS):
        return response_text
    return f"{lead}\n\n{response_text}"
