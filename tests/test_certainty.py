"""
Tests — core.operator.certainty (Fase 2: declaración honesta de certeza).

Verifican que:
  - rutas grounded NUNCA declaran incertidumbre (aunque la confianza sea baja)
  - rutas generativas con confianza < floor SÍ anteponen la salvedad
  - rutas generativas con confianza alta NO añaden salvedad
  - respuesta vacía => passthrough (nunca bloquea)
  - no duplica una salvedad ya presente
  - respeta el idioma (en) y no mezcla idiomas si no hay plantilla

Run:  .venv/bin/python -m pytest tests/test_certainty.py -v
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.operator import certainty as C


def test_grounded_route_never_hedges_even_low_confidence():
    for route in ("memory", "self_aware", "criterion:deterministic",
                  "freight_evidence", "market", "places", "identity"):
        out = C.declare_certainty("La respuesta.", confidence=0.1, route=route, lang="es")
        assert out == "La respuesta.", route


def test_generative_low_confidence_prepends_lead():
    out = C.declare_certainty("Nvidia podría subir.", confidence=0.3, route="llm", lang="es")
    assert out.startswith("Con lo que tengo ahora no me alcanza para asegurarlo")
    assert "Nvidia podría subir." in out


def test_generative_high_confidence_unchanged():
    out = C.declare_certainty("Dato firme.", confidence=0.8, route="online", lang="es")
    assert out == "Dato firme."


def test_empty_response_passthrough():
    assert C.declare_certainty("", confidence=0.1, route="llm", lang="es") == ""
    assert C.declare_certainty("   ", confidence=0.1, route="llm", lang="es") == "   "


def test_no_double_hedge():
    already = "No estoy seguro, pero podría ser X."
    out = C.declare_certainty(already, confidence=0.2, route="llm", lang="es")
    assert out == already


def test_english_lead():
    out = C.declare_certainty("It might rain.", confidence=0.2, route="llm", lang="en")
    assert out.startswith("With what I have right now I can't fully assure it")
    assert "It might rain." in out


def test_unsupported_language_does_not_mix():
    # Idioma sin plantilla → no añade salvedad (evita mezclar idiomas).
    out = C.declare_certainty("Talvez.", confidence=0.2, route="llm", lang="ja")
    assert out == "Talvez."


def test_needs_uncertainty_logic():
    assert C.needs_uncertainty(0.3, "llm") is True
    assert C.needs_uncertainty(0.9, "llm") is False
    assert C.needs_uncertainty(0.1, "memory") is False
    assert C.needs_uncertainty(0.1, "criterion:deterministic") is False
    assert C.needs_uncertainty("bad", "llm") is False
