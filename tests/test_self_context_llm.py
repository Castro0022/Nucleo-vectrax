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


# ---------------------------------------------------------------------------
# Fase 3, paso 6/7 — regresión: viñetas fijas de capacidades retiradas del
# bloque `base` de build_self_context(); _read_engines_state() se guarda
# (no se invoca) cuando el SSOT marca capability_query=True.
# ---------------------------------------------------------------------------

_REMOVED_BULLETS_ES = [
    "Lo que tengo activo hoy",
    "Sistema de equipos con billing",
    "Acceso por Telegram, sin fricción",
]
_REMOVED_BULLETS_EN = [
    "What I have active today",
    "Team system with billing",
    "Telegram access, zero friction",
]


def test_source_no_longer_contains_hardcoded_capability_bullets():
    """Las viñetas fijas (ES/EN) ya no existen en el código fuente de
    self_context.py (Fase 3, pasos 5-6) — ni siquiera citadas en
    comentarios/docstrings, para que este test permanezca honesto."""
    import inspect
    source = inspect.getsource(SC)
    for bullet in _REMOVED_BULLETS_ES + _REMOVED_BULLETS_EN:
        assert bullet not in source, "legacy bullet still present: %r" % (bullet,)


def test_build_self_context_output_has_no_removed_bullets():
    """build_self_context() real (hermético vía mocks de I/O) tampoco emite
    las viñetas retiradas, en ES ni EN."""
    with patch("vectrax.self_context._read_live_stats",
               return_value={"users": 1, "interactions": 1, "facts": 0, "teams": 0}), \
         patch("vectrax.self_context._read_universe_state", return_value=""), \
         patch("vectrax.self_context._read_recent_observations", return_value=""), \
         patch("vectrax.self_context._read_engines_state", return_value=""), \
         patch("vectrax.self_context._is_capability_query", return_value=False):
        out_es = SC.build_self_context(lang="es", user_id="test:x", query="hola")
        out_en = SC.build_self_context(lang="en", user_id="test:x", query="hi")
    for bullet in _REMOVED_BULLETS_ES + _REMOVED_BULLETS_EN:
        assert bullet not in out_es
        assert bullet not in out_en


def test_is_capability_query_delegates_to_ssot():
    """_is_capability_query() no reimplementa detección propia: delega
    exclusivamente en core.intent_ssot.resolve_intent().capability_query."""
    from core.intent_ssot import IntentDecision

    with patch("core.intent_ssot.resolve_intent",
               return_value=IntentDecision(primary_intent="cognitive", capability_query=True)):
        assert SC._is_capability_query("¿qué puedes hacer?") is True

    with patch("core.intent_ssot.resolve_intent",
               return_value=IntentDecision(primary_intent="cognitive", capability_query=False)):
        assert SC._is_capability_query("¿quién te creó?") is False


def test_is_capability_query_defensive_on_empty_and_error():
    assert SC._is_capability_query("") is False
    with patch("core.intent_ssot.resolve_intent", side_effect=RuntimeError("boom")):
        assert SC._is_capability_query("¿qué puedes hacer?") is False


def test_build_self_context_skips_engines_state_when_capability_query():
    """Guard del paso 7: con capability_query=True, _read_engines_state() no
    se invoca (evita duplicar, con menos detalle, lo que ya hace
    capability_context/capability_narrator)."""
    with patch("vectrax.self_context._read_live_stats",
               return_value={"users": 1, "interactions": 1, "facts": 0, "teams": 0}), \
         patch("vectrax.self_context._read_universe_state", return_value=""), \
         patch("vectrax.self_context._read_recent_observations", return_value=""), \
         patch("vectrax.self_context._is_capability_query", return_value=True), \
         patch("vectrax.self_context._read_engines_state") as mock_engines:
        SC.build_self_context(lang="es", user_id="test:x", query="¿qué puedes hacer?")
    mock_engines.assert_not_called()


def test_build_self_context_includes_engines_state_when_not_capability_query():
    """Sin capability_query, se preserva el comportamiento previo a la Fase 3:
    _read_engines_state() sí se invoca e incluye en el contexto."""
    with patch("vectrax.self_context._read_live_stats",
               return_value={"users": 1, "interactions": 1, "facts": 0, "teams": 0}), \
         patch("vectrax.self_context._read_universe_state", return_value=""), \
         patch("vectrax.self_context._read_recent_observations", return_value=""), \
         patch("vectrax.self_context._is_capability_query", return_value=False), \
         patch("vectrax.self_context._read_engines_state",
               return_value="[MIS MOTORES]\nDisponibles: 48/48") as mock_engines:
        out = SC.build_self_context(lang="es", user_id="test:x", query="hola")
    mock_engines.assert_called_once()
    assert "[MIS MOTORES]" in out
