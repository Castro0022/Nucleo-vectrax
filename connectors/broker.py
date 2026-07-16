"""
Vectrax — Broker Provider Router
==================================
Single, declarative selection layer for the active trading broker.

Background (audit finding 2026-06-23)
-------------------------------------
`docs/ENGINES.md` and `README.md` document that the active broker "se
selecciona por BROKER_PROVIDER", but no such routing existed: the eToro and
Alpaca clients were two unrelated islands and nothing read BROKER_PROVIDER.
This module makes that documented contract real WITHOUT touching the
eToro-specific learning cycle (signal_recorder → outcome_tracker →
pattern_memory → learning_engine), which stays eToro-native by design.

It exposes a small, normalized, provider-agnostic surface that callers
(dashboards, health checks, future multi-broker execution) can use:

    get_provider()            → "etoro" | "alpaca"   (from BROKER_PROVIDER)
    health_check()            → {provider, success, connected, environment, latency_ms}
    get_price(symbol)         → {provider, success, symbol, bid, ask, mid, last}
    get_account()             → {provider, success, equity, cash, ...}

Design rules
------------
* Default provider is "etoro" (matches production today; never silently
  switches a live system to a different broker).
* Selection is read from the environment on every call so operators can flip
  BROKER_PROVIDER without a code change.
* Defensive: an unconfigured/uninstalled provider returns a clean error dict
  ({"success": False, "error": ...}); it never raises, so callers degrade
  gracefully instead of crashing the worker.
* Additive only: no existing module is modified by importing this one.
"""
from __future__ import annotations

import importlib
import logging
import os
from typing import Any, Dict

logger = logging.getLogger("vectrax.broker")

# Known providers and their canonical names.
ETORO = "etoro"
ALPACA = "alpaca"
_KNOWN_PROVIDERS = (ETORO, ALPACA)

_DEFAULT_PROVIDER = ETORO


def get_provider() -> str:
    """Return the active broker provider from BROKER_PROVIDER (default eToro).

    Unknown values fall back to the default and emit a warning, so a typo in
    the environment can never route orders to an unintended broker.
    """
    raw = os.environ.get("BROKER_PROVIDER", _DEFAULT_PROVIDER).strip().lower()
    if raw not in _KNOWN_PROVIDERS:
        logger.warning(
            "BROKER_PROVIDER=%r no reconocido; usando '%s'. Válidos: %s",
            raw, _DEFAULT_PROVIDER, ", ".join(_KNOWN_PROVIDERS),
        )
        return _DEFAULT_PROVIDER
    return raw


def is_provider(name: str) -> bool:
    """True if the given provider is the active one."""
    return get_provider() == name.strip().lower()


# ── Client resolution (dependency injection) ─────────────────────
#
# Contamination fix (2026-07-16): the router bound the client with
# ``from connectors.etoro import etoro_client``, which resolves via the
# ``connectors.etoro`` package ATTRIBUTE. A test stubbing only the
# ``sys.modules`` entry got bypassed once another test had imported the real
# submodule (leaving the real object bound as a package attribute) — producing
# order-dependent failures in the full suite.
#
# The client is now resolved through a single injectable seam:
#   * an explicitly injected client wins (``set_client`` — tests / wiring),
#   * otherwise it is imported via ``importlib.import_module`` (which honors
#     ``sys.modules`` and does NOT depend on package-attribute state).

_CLIENT_MODULES: Dict[str, str] = {
    ETORO: "connectors.etoro.etoro_client",
    ALPACA: "connectors.alpaca.alpaca_client",
}

# Explicitly injected clients keyed by provider. Empty in production.
_INJECTED_CLIENTS: Dict[str, Any] = {}


def set_client(provider: str, client: Any) -> None:
    """Inject the client object for a provider (dependency injection).

    Any object exposing the provider's methods works. Pass ``client=None`` to
    clear a single provider's injection. Intended for tests and advanced
    wiring; production leaves this empty and resolves clients by import.
    """
    key = provider.strip().lower()
    if client is None:
        _INJECTED_CLIENTS.pop(key, None)
    else:
        _INJECTED_CLIENTS[key] = client


def reset_clients() -> None:
    """Clear ALL injected clients (restore default import-based resolution)."""
    _INJECTED_CLIENTS.clear()


def _resolve_client(provider: str) -> Any:
    """Return the client for ``provider``: an injected one if present, else the
    module imported via importlib (honors ``sys.modules``; no static
    ``from package import submodule``, so no package-attribute contamination).
    """
    if provider in _INJECTED_CLIENTS:
        return _INJECTED_CLIENTS[provider]
    module_path = _CLIENT_MODULES.get(provider)
    if not module_path:
        raise ModuleNotFoundError(f"proveedor sin cliente registrado: {provider}")
    return importlib.import_module(module_path)


# ── Normalized operations ─────────────────────────────────────────────

def health_check() -> Dict[str, Any]:
    """Provider-agnostic connectivity check for the active broker."""
    provider = get_provider()
    try:
        client = _resolve_client(provider)
        if provider == ETORO:
            r = client.healthcheck()
            return {"provider": ETORO, **r}
        if provider == ALPACA:
            r = client.health_check()
            return {"provider": ALPACA, **r}
    except ModuleNotFoundError as exc:
        # alpaca-py not installed, etc.
        return {"provider": provider, "success": False, "connected": False,
                "error": f"SDK no instalado para '{provider}': {exc}"}
    except EnvironmentError as exc:
        # Missing API keys.
        return {"provider": provider, "success": False, "connected": False,
                "error": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive
        return {"provider": provider, "success": False, "connected": False,
                "error": str(exc)}
    return {"provider": provider, "success": False, "connected": False,
            "error": f"Proveedor '{provider}' sin implementación de health_check"}


def get_price(symbol: str) -> Dict[str, Any]:
    """Provider-agnostic latest quote. Returns normalized bid/ask/mid/last."""
    provider = get_provider()
    try:
        client = _resolve_client(provider)
        if provider == ETORO:
            iid = client.get_instrument_id(symbol)
            if not iid:
                return {"provider": ETORO, "success": False, "symbol": symbol,
                        "error": f"instrument_id no encontrado para {symbol}"}
            r = client.get_price(iid)
            if not r.get("success"):
                return {"provider": ETORO, "symbol": symbol, **r}
            return {
                "provider": ETORO, "success": True, "symbol": symbol,
                "bid": r.get("bid"), "ask": r.get("ask"),
                "mid": r.get("mid"), "last": r.get("last"),
                "latency_ms": r.get("latency_ms"),
            }
        if provider == ALPACA:
            r = client.get_price(symbol)
            if not r.get("success"):
                return {"provider": ALPACA, "symbol": symbol, **r}
            return {
                "provider": ALPACA, "success": True, "symbol": symbol,
                "bid": r.get("bid"), "ask": r.get("ask"),
                "mid": r.get("mid"),
                # Alpaca latest-quote has no trade price; mid is the best proxy.
                "last": r.get("last", r.get("mid")),
            }
    except ModuleNotFoundError as exc:
        return {"provider": provider, "success": False, "symbol": symbol,
                "error": f"SDK no instalado para '{provider}': {exc}"}
    except EnvironmentError as exc:
        return {"provider": provider, "success": False, "symbol": symbol,
                "error": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive
        return {"provider": provider, "success": False, "symbol": symbol,
                "error": str(exc)}
    return {"provider": provider, "success": False, "symbol": symbol,
            "error": f"Proveedor '{provider}' sin implementación de get_price"}


def get_account() -> Dict[str, Any]:
    """Provider-agnostic account summary (equity/cash where available)."""
    provider = get_provider()
    try:
        client = _resolve_client(provider)
        if provider == ETORO:
            r = client.get_portfolio()
            if not r.get("success"):
                return {"provider": ETORO, **r}
            return {
                "provider": ETORO, "success": True,
                "equity": r.get("equity"),
                "cash": r.get("credit"),
                "invested": r.get("invested"),
                "pnl": r.get("pnl"),
                "environment": r.get("environment"),
            }
        if provider == ALPACA:
            r = client.get_account()
            return {"provider": ALPACA, **r}
    except ModuleNotFoundError as exc:
        return {"provider": provider, "success": False,
                "error": f"SDK no instalado para '{provider}': {exc}"}
    except EnvironmentError as exc:
        return {"provider": provider, "success": False, "error": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive
        return {"provider": provider, "success": False, "error": str(exc)}
    return {"provider": provider, "success": False,
            "error": f"Proveedor '{provider}' sin implementación de get_account"}
