"""
connectors/real_estate — Conector del dominio florida_real_estate.

API pública:
    get_provider()            → RealEstateFeedProvider (lee REAL_ESTATE_FEED_PROVIDER)
    RealEstateFeedProvider    (base para type hints)
    RealEstateEvent           (evento canónico)
    DOMAIN                    ("florida_real_estate")

Diseño agnóstico a la fuente: arranca con 'simulator' (piloto sin credenciales),
y se cambia a 'attom' (o RESO/MLS en el futuro) por env var — SIN tocar el modelo
del dominio.
"""
from __future__ import annotations

import importlib
import logging
import os
from typing import Optional

from connectors.real_estate.base import DOMAIN, RealEstateEvent, RealEstateFeedProvider

logger = logging.getLogger("vectrax.real_estate")

_DEFAULT_PROVIDER = "simulator"

_REGISTRY = {
    "simulator": "connectors.real_estate.simulator_adapter.SimulatorAdapter",
    "attom":     "connectors.real_estate.attom_provider.AttomProvider",
    "rentcast":  "connectors.real_estate.rentcast_provider.RentCastProvider",
}

_singleton: Optional[RealEstateFeedProvider] = None


def get_provider(force_new: bool = False) -> RealEstateFeedProvider:
    """Devuelve el RealEstateFeedProvider del proceso (singleton).

    Selección por REAL_ESTATE_FEED_PROVIDER (default: simulator).
    force_new=True evita el singleton (útil en tests).
    """
    global _singleton
    if _singleton is not None and not force_new:
        return _singleton

    name = os.environ.get("REAL_ESTATE_FEED_PROVIDER", _DEFAULT_PROVIDER).strip().lower()
    cls_path = _REGISTRY.get(name)
    if not cls_path:
        logger.warning(
            "REAL_ESTATE_FEED_PROVIDER=%r no reconocido; uso %r. Válidos: %s",
            name, _DEFAULT_PROVIDER, list(_REGISTRY),
        )
        cls_path = _REGISTRY[_DEFAULT_PROVIDER]

    module_path, cls_name = cls_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    provider: RealEstateFeedProvider = getattr(module, cls_name)()
    logger.info("RealEstateFeedProvider: %s", provider.provider_name)

    if not force_new:
        _singleton = provider
    return provider


__all__ = ["DOMAIN", "RealEstateEvent", "RealEstateFeedProvider", "get_provider"]
