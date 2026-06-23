"""
Tests for core.continuity_reentry — light continuity re-engagement.

Focus: a user WITHOUT a stored name or conversation history must still get a
reentry message generated (previously _build_reentry_message returned None and
nothing was ever sent). The LLM is stubbed so no network is hit.
"""
from __future__ import annotations

import pytest


def test_reentry_message_generated_without_name_or_context(monkeypatch):
    """No name, no history → still generates a light continuity message."""
    import vectrax.intelligence_bridge as ib
    monkeypatch.setattr(ib, "is_ready", lambda: True, raising=False)
    monkeypatch.setattr(
        ib, "route_single",
        lambda prompt: {"success": True, "content": "Oye, ¿retomamos cuando quieras?"},
        raising=False,
    )

    from core.continuity_reentry import _build_reentry_message
    # An id with no stored profile/interactions → exercises the no-context path.
    msg = _build_reentry_message("tg:999999999")
    assert msg is not None
    assert "retomamos" in msg


def test_reentry_message_uses_context_when_available(monkeypatch):
    """When the LLM is available, generation succeeds regardless of context."""
    import vectrax.intelligence_bridge as ib
    monkeypatch.setattr(ib, "is_ready", lambda: True, raising=False)
    captured = {}

    def _fake_route(prompt):
        captured["prompt"] = prompt
        return {"success": True, "content": "Seguimos con lo de ayer."}

    monkeypatch.setattr(ib, "route_single", _fake_route, raising=False)

    from core.continuity_reentry import _build_reentry_message
    msg = _build_reentry_message("tg:123456789")
    assert msg == "Seguimos con lo de ayer."
    # The prompt always instructs Vectrax to produce a short continuity message.
    assert "continuidad" in captured["prompt"].lower()
