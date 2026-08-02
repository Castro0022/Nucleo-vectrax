"""
connectors/cybersecurity — Conector del dominio cybersecurity (NVD + CISA KEV).

API pública:
    get_provider()          → CyberFeedProvider (lee CYBER_FEED_PROVIDER)
    CyberFeedProvider       (base para type hints)
    CVEEvent                (observación canónica)
    DOMAIN                  ("cybersecurity")

Diseño agnóstico a la fuente: arranca con 'simulator' (piloto/tests sin red), y
se cambia a 'nvd' por env var — SIN tocar el modelo del dominio.
"""
from __future__ import annotations

import importlib
import logging
import os
from typing import Optional

from connectors.cybersecurity.base import DOMAIN, CVEEvent, CyberFeedProvider

logger = logging.getLogger("vectrax.cybersecurity")

_DEFAULT_PROVIDER = "simulator"

_REGISTRY = {
    "simulator": "connectors.cybersecurity.simulator_adapter.SimulatorAdapter",
    "nvd": "connectors.cybersecurity.nvd_provider.NvdProvider",
}

_singleton: Optional[CyberFeedProvider] = None


def get_provider(force_new: bool = False) -> CyberFeedProvider:
    """Devuelve el CyberFeedProvider del proceso (singleton).

    Selección por CYBER_FEED_PROVIDER (default: simulator).
    force_new=True evita el singleton (útil en tests).
    """
    global _singleton
    if _singleton is not None and not force_new:
        return _singleton

    name = os.environ.get("CYBER_FEED_PROVIDER", _DEFAULT_PROVIDER).strip().lower()
    cls_path = _REGISTRY.get(name)
    if not cls_path:
        logger.warning(
            "CYBER_FEED_PROVIDER=%r no reconocido; uso %r. Válidos: %s",
            name, _DEFAULT_PROVIDER, list(_REGISTRY),
        )
        cls_path = _REGISTRY[_DEFAULT_PROVIDER]

    module_path, cls_name = cls_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    provider: CyberFeedProvider = getattr(module, cls_name)()
    logger.info("CyberFeedProvider: %s", provider.provider_name)

    if not force_new:
        _singleton = provider
    return provider


__all__ = ["DOMAIN", "CVEEvent", "CyberFeedProvider", "get_provider"]
