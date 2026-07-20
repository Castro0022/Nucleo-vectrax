"""
Tests — vectrax.self_context.resolve_self_aware unificado sobre core.llm_call.

Verifica que la ruta "OpenAI directo" del narrador self-aware ahora pasa por
`core.llm_call.complete` (context-agnostic), devolviendo su texto cuando está
disponible y cadena vacía cuando no.

Run:  python -m pytest tests/test_self_context_llm.py -v
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import llm_call
from vectrax import self_context as SC


def test_resolve_self_aware_via_llm_call():
    # build_self_aware_prompt parcheado → hermético (sin DB/census).
    # bridge no listo → cae al util compartido, que devuelve texto.
    with patch("vectrax.self_context.build_self_aware_prompt", return_value="PROMPT"), \
         patch("vectrax.intelligence_bridge.is_ready", return_value=False), \
         patch("core.llm_call.complete",
               return_value=llm_call.LLMResult(True, "Soy Vectrax, operando.", "ok")):
        out = SC.resolve_self_aware("¿qué eres?", lang="es", user_id="test:sc")
    assert out == "Soy Vectrax, operando."


def test_resolve_self_aware_empty_when_unavailable():
    with patch("vectrax.self_context.build_self_aware_prompt", return_value="PROMPT"), \
         patch("vectrax.intelligence_bridge.is_ready", return_value=False), \
         patch("core.llm_call.complete",
               return_value=llm_call.LLMResult(False, "", "no_key")):
        out = SC.resolve_self_aware("¿qué eres?", lang="es", user_id="test:sc")
    assert out == ""


def test_resolve_self_aware_passes_selfaware_params():
    # Conserva temperatura 0.4 y timeout 15s propios del self-aware.
    captured = {}

    def _fake_complete(prompt, **kw):
        captured.update(kw)
        return llm_call.LLMResult(True, "ok", "ok")

    with patch("vectrax.self_context.build_self_aware_prompt", return_value="PROMPT"), \
         patch("vectrax.intelligence_bridge.is_ready", return_value=False), \
         patch("core.llm_call.complete", side_effect=_fake_complete):
        SC.resolve_self_aware("¿qué eres?", lang="es", user_id="test:sc")
    assert captured.get("temperature") == 0.4
    assert captured.get("timeout") == 15.0
