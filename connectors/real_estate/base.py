"""
connectors/real_estate/base.py — Contrato del proveedor del dominio florida_real_estate.

Espejo de connectors/freight/base.py: un evento canónico y una interfaz de
proveedor agnóstica a la fuente. Cualquier fuente (simulador, ATTOM, RESO/MLS…)
implementa `RealEstateFeedProvider` y emite `RealEstateEvent`. El resto del
sistema (ingest → gravity → elevación → verificación → criterio) es invariante.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable

# Clave de dominio (única fuente de verdad para todo el conector).
DOMAIN = "florida_real_estate"


@dataclass
class RealEstateEvent:
    """Evento canónico del mercado inmobiliario (agnóstico a la fuente).

    `event_type` es el verbo del mercado (new_listing, price_change,
    status_change, sale_closed, expired, withdrawn, inventory_update, …).
    `data` lleva el payload normalizado, SIEMPRE incluye la zona como CONDICIÓN
    (nunca como prioridad): p. ej. {"zone": "33139", "type": "condo",
    "tier": "luxury", "list_price": ..., "sold_price": ..., "dom": ...}.
    """
    event_type: str
    data: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    @property
    def zone(self) -> str:
        return str(self.data.get("zone") or self.data.get("zip") or "").strip()


class RealEstateFeedProvider(ABC):
    """Interfaz de proveedor de datos del dominio (misma forma que freight)."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Nombre corto del proveedor (p. ej. 'simulator', 'attom')."""

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """Estado del proveedor. Debe devolver {'healthy': bool, 'detail': str}.
        NUNCA lanza: ante error, devuelve healthy=False con el detalle."""

    @abstractmethod
    def stream_events(self, n: int) -> Iterable[RealEstateEvent]:
        """Devuelve hasta `n` eventos normalizados. Si el proveedor no está
        saludable, devuelve una secuencia vacía (nunca lanza)."""
