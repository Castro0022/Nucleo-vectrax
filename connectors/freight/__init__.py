"""
connectors/freight — Freight logistics domain connector package.

Public API:
    get_provider()  → FreightFeedProvider  (reads FREIGHT_FEED_PROVIDER env)
    FreightFeedProvider                    (base class for type hints)
    FreightEvent                           (canonical event type)
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from connectors.freight.base import FreightEvent, FreightFeedProvider

logger = logging.getLogger("vectrax.freight")

_DEFAULT_PROVIDER = "simulator"

_REGISTRY = {
    "simulator": "connectors.freight.simulator_adapter.SimulatorAdapter",
    "real":      "connectors.freight.simulator_adapter.RealFeedAdapter",
}

_singleton: Optional[FreightFeedProvider] = None


def get_provider(force_new: bool = False) -> FreightFeedProvider:
    """
    Return the singleton FreightFeedProvider for this process.

    Provider is selected by FREIGHT_FEED_PROVIDER env var (default: simulator).
    Pass force_new=True to bypass the singleton (useful in tests).
    """
    global _singleton
    if _singleton is not None and not force_new:
        return _singleton

    name = os.environ.get("FREIGHT_FEED_PROVIDER", _DEFAULT_PROVIDER).strip().lower()
    cls_path = _REGISTRY.get(name)
    if not cls_path:
        logger.warning(
            "FREIGHT_FEED_PROVIDER=%r not recognised; using %r. Valid: %s",
            name, _DEFAULT_PROVIDER, list(_REGISTRY),
        )
        cls_path = _REGISTRY[_DEFAULT_PROVIDER]

    module_path, cls_name = cls_path.rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    provider: FreightFeedProvider = getattr(module, cls_name)()
    logger.info("FreightFeedProvider: %s", provider.provider_name)

    if not force_new:
        _singleton = provider
    return provider


__all__ = ["FreightFeedProvider", "FreightEvent", "get_provider"]
