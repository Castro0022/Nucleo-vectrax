"""
Tests for connectors/broker.py — the BROKER_PROVIDER routing layer.

These tests are hermetic: they never reach a real broker. They verify the
selection contract and defensive degradation, and that calls are routed to the
correct underlying client via monkeypatched stubs.
"""
from __future__ import annotations

import sys
import types

import pytest

from connectors import broker


def test_default_provider_is_etoro(monkeypatch):
    monkeypatch.delenv("BROKER_PROVIDER", raising=False)
    assert broker.get_provider() == "etoro"


def test_provider_from_env(monkeypatch):
    monkeypatch.setenv("BROKER_PROVIDER", "alpaca")
    assert broker.get_provider() == "alpaca"
    assert broker.is_provider("alpaca") is True
    assert broker.is_provider("etoro") is False


def test_provider_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("BROKER_PROVIDER", "  ALPACA ")
    assert broker.get_provider() == "alpaca"


def test_unknown_provider_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("BROKER_PROVIDER", "robinhood")
    assert broker.get_provider() == "etoro"


def test_health_check_routes_to_etoro(monkeypatch):
    monkeypatch.setenv("BROKER_PROVIDER", "etoro")
    fake = types.SimpleNamespace(
        healthcheck=lambda: {"success": True, "connected": True, "environment": "real"}
    )
    monkeypatch.setitem(sys.modules, "connectors.etoro.etoro_client", fake)
    out = broker.health_check()
    assert out["provider"] == "etoro"
    assert out["success"] is True
    assert out["connected"] is True


def test_get_price_routes_to_etoro_two_step(monkeypatch):
    """eToro requires instrument_id resolution then a rate fetch."""
    monkeypatch.setenv("BROKER_PROVIDER", "etoro")
    fake = types.SimpleNamespace(
        get_instrument_id=lambda symbol: 100134,
        get_price=lambda iid: {
            "success": True, "bid": 100.0, "ask": 102.0, "mid": 101.0,
            "last": 101.5, "latency_ms": 12.0,
        },
    )
    monkeypatch.setitem(sys.modules, "connectors.etoro.etoro_client", fake)
    out = broker.get_price("BTC")
    assert out == {
        "provider": "etoro", "success": True, "symbol": "BTC",
        "bid": 100.0, "ask": 102.0, "mid": 101.0, "last": 101.5,
        "latency_ms": 12.0,
    }


def test_get_price_etoro_missing_instrument(monkeypatch):
    monkeypatch.setenv("BROKER_PROVIDER", "etoro")
    fake = types.SimpleNamespace(
        get_instrument_id=lambda symbol: None,
        get_price=lambda iid: {"success": True},
    )
    monkeypatch.setitem(sys.modules, "connectors.etoro.etoro_client", fake)
    out = broker.get_price("NOPE")
    assert out["success"] is False
    assert "instrument_id" in out["error"]


def test_get_price_routes_to_alpaca(monkeypatch):
    monkeypatch.setenv("BROKER_PROVIDER", "alpaca")
    fake = types.SimpleNamespace(
        get_price=lambda symbol: {"success": True, "bid": 1.0, "ask": 3.0, "mid": 2.0},
    )
    monkeypatch.setitem(sys.modules, "connectors.alpaca.alpaca_client", fake)
    out = broker.get_price("AAPL")
    assert out["provider"] == "alpaca"
    assert out["success"] is True
    assert out["mid"] == 2.0
    # Alpaca has no trade price; mid is used as the `last` proxy.
    assert out["last"] == 2.0


def test_alpaca_missing_sdk_degrades_cleanly(monkeypatch):
    """When alpaca-py is not installed, calls return an error dict, not a crash.

    The real alpaca_client.health_check() catches the missing-SDK import error
    itself and returns {"success": False, "error": ...}. The router must surface
    that as a clean failure for the active provider rather than raising.
    """
    monkeypatch.setenv("BROKER_PROVIDER", "alpaca")
    fake = types.SimpleNamespace(
        health_check=lambda: {"success": False, "connected": False,
                              "error": "No module named 'alpaca'"},
    )
    monkeypatch.setitem(sys.modules, "connectors.alpaca.alpaca_client", fake)
    out = broker.health_check()
    assert out["provider"] == "alpaca"
    assert out["success"] is False
    assert out["connected"] is False
    assert "alpaca" in out["error"].lower()


def test_health_check_router_catches_import_error(monkeypatch):
    """If even importing the client module fails, the router degrades cleanly."""
    monkeypatch.setenv("BROKER_PROVIDER", "alpaca")

    real_import = __import__

    def _fake_import(name, *args, **kwargs):
        if "alpaca_client" in name:
            raise ModuleNotFoundError("No module named 'connectors.alpaca.alpaca_client'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setitem(sys.modules, "connectors.alpaca.alpaca_client", None)
    monkeypatch.setattr("builtins.__import__", _fake_import)
    out = broker.health_check()
    assert out["provider"] == "alpaca"
    assert out["success"] is False
    assert "SDK no instalado" in out["error"]
