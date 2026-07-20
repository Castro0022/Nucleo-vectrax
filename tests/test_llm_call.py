"""
Tests — core/llm_call.complete (llamada LLM directa, context-agnostic).

Cubre el mapeo de cada `LLMStatus` (ok/empty/no_key/gate_closed/circuit_open/
http_error/exception), que usa el system prompt soberano, y que NUNCA lanza.

Run:  python -m pytest tests/test_llm_call.py -v
"""
from __future__ import annotations

import os
import sys

import httpx
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import llm_call as L


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def _ok_payload(text):
    return {"choices": [{"message": {"content": text}}]}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Entorno hermético: key presente, gate/circuit abiertos, guards no-op."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("VX_LLM_MODEL", raising=False)
    monkeypatch.delenv("VX_LLM_TIMEOUT", raising=False)
    monkeypatch.setattr("core.api_gate.check_gate", lambda p: True)
    monkeypatch.setattr("core.api_gate.record_429", lambda p: 0.0)
    monkeypatch.setattr("core.api_gate.record_success", lambda p: None)
    monkeypatch.setattr("core.external_call_guard._check_circuit", lambda p: True)
    monkeypatch.setattr("core.external_call_guard._record_success", lambda p, ms: None)
    monkeypatch.setattr(
        "core.external_call_guard._record_failure",
        lambda p, e, is_timeout=False: None,
    )
    yield


def test_ok(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(200, _ok_payload("  hola  ")))
    r = L.complete("prompt")
    assert r.ok and r.status == "ok"
    assert r.text == "hola"          # trimmed
    assert r.available is True


def test_empty_content(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(200, _ok_payload("   ")))
    r = L.complete("prompt")
    assert not r.ok and r.status == "empty" and r.text == ""
    assert r.available is True        # se llamó; solo quedó vacío


def test_no_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = L.complete("p")
    assert not r.ok and r.status == "no_key" and r.available is False


def test_gate_closed(monkeypatch):
    monkeypatch.setattr("core.api_gate.check_gate", lambda p: False)
    r = L.complete("p")
    assert not r.ok and r.status == "gate_closed" and r.available is False


def test_circuit_open(monkeypatch):
    monkeypatch.setattr("core.external_call_guard._check_circuit", lambda p: False)
    r = L.complete("p")
    assert not r.ok and r.status == "circuit_open" and r.available is False


def test_http_429(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(429))
    r = L.complete("p")
    assert not r.ok and r.status == "http_error" and r.error == "429"


def test_http_500(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(500))
    r = L.complete("p")
    assert not r.ok and r.status == "http_error"


def test_exception_never_raises(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("net down")
    monkeypatch.setattr(httpx, "post", _boom)
    r = L.complete("p")            # no debe propagar
    assert not r.ok and r.status == "exception"


def test_uses_sovereign_system_prompt(monkeypatch):
    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        captured["timeout"] = timeout
        return _Resp(200, _ok_payload("ok"))

    monkeypatch.setattr(httpx, "post", _fake_post)
    L.complete("PROMPT")
    msgs = captured["json"]["messages"]
    assert msgs[0]["role"] == "system" and "VECTRAX" in msgs[0]["content"]
    assert msgs[1] == {"role": "user", "content": "PROMPT"}


def test_explicit_system_prompt_overrides(monkeypatch):
    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _Resp(200, _ok_payload("ok"))

    monkeypatch.setattr(httpx, "post", _fake_post)
    L.complete("PROMPT", system_prompt="SÉ BREVE")
    assert captured["json"]["messages"][0]["content"] == "SÉ BREVE"


def test_env_overrides_model_and_timeout(monkeypatch):
    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        captured["timeout"] = timeout
        return _Resp(200, _ok_payload("ok"))

    monkeypatch.setenv("VX_LLM_MODEL", "gpt-4o")
    monkeypatch.setenv("VX_LLM_TIMEOUT", "7.5")
    monkeypatch.setattr(httpx, "post", _fake_post)
    r = L.complete("p")
    assert captured["json"]["model"] == "gpt-4o"
    assert captured["timeout"] == 7.5
    assert r.model == "gpt-4o"
