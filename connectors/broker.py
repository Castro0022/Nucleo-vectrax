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


# ── Normalized operations ─────────────────────────────────────────────

def health_check() -> Dict[str, Any]:
    """Provider-agnostic connectivity check for the active broker."""
    provider = get_provider()
    try:
        if provider == ETORO:
            from connectors.etoro import etoro_client
            r = etoro_client.healthcheck()
            return {"provider": ETORO, **r}
        if provider == ALPACA:
            from connectors.alpaca import alpaca_client
            r = alpaca_client.health_check()
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
        if provider == ETORO:
            from connectors.etoro import etoro_client
            iid = etoro_client.get_instrument_id(symbol)
            if not iid:
                return {"provider": ETORO, "success": False, "symbol": symbol,
                        "error": f"instrument_id no encontrado para {symbol}"}
            r = etoro_client.get_price(iid)
            if not r.get("success"):
                return {"provider": ETORO, "symbol": symbol, **r}
            return {
                "provider": ETORO, "success": True, "symbol": symbol,
                "bid": r.get("bid"), "ask": r.get("ask"),
                "mid": r.get("mid"), "last": r.get("last"),
                "latency_ms": r.get("latency_ms"),
            }
        if provider == ALPACA:
            from connectors.alpaca import alpaca_client
            r = alpaca_client.get_price(symbol)
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
        if provider == ETORO:
            from connectors.etoro import etoro_client
            r = etoro_client.get_portfolio()
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
            from connectors.alpaca import alpaca_client
            r = alpaca_client.get_account()
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
