"""
connectors/real_estate/simulator_adapter.py — Proveedor simulador (statewide).

Genera eventos inmobiliarios sintéticos repartidos por TODA Florida SIN prioridad
geográfica: la zona se elige de forma uniforme de un pool amplio que cubre todas
las regiones. Sirve para validar el pipeline completo (ingest → gravity →
elevación → verificación → criterio) sin credenciales ni red.

La atención hacia una zona NO se codifica aquí: emerge después, sola, por la masa
gravitacional que la actividad genere en el motor.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import random
from typing import Iterable, List, Optional

from connectors.real_estate.base import RealEstateEvent, RealEstateFeedProvider

# Pool amplio de ZIP de Florida cubriendo múltiples regiones (sureste, centro,
# oeste/golfo, norte, panhandle, suroeste). NO es una lista de prioridad: es solo
# el universo de muestreo uniforme del simulador.
_FL_ZONES = [
    "33139", "33101", "33301", "33009",   # Miami / Broward (SE)
    "32801", "32803", "34741", "34787",   # Orlando / Kissimmee (centro)
    "33602", "33701", "33607", "34205",   # Tampa / St. Pete / Bradenton (golfo)
    "32202", "32204", "32250",            # Jacksonville (NE)
    "32301", "32303",                     # Tallahassee (norte)
    "32501", "32502",                     # Pensacola (panhandle)
    "34102", "34103", "33901",            # Naples / Fort Myers (SW)
    "33401", "34996",                     # West Palm / Stuart (SE costa)
    "32960", "34471",                     # Vero Beach / Ocala (centro-este)
]

_TYPES = ["sfr", "condo", "townhouse", "multifamily", "land"]
_TIERS = ["entry", "mid", "luxury"]

# Eventos no terminales (actividad) + terminales (con verdad objetiva).
_ACTIVITY = ["new_listing", "price_change", "status_change", "open_house"]
_TERMINAL = ["sale_closed", "expired", "withdrawn"]


class SimulatorAdapter(RealEstateFeedProvider):
    """Proveedor sintético statewide, sin dependencias externas."""

    def __init__(self, seed: Optional[int] = None) -> None:
        self._rng = random.Random(seed)

    @property
    def provider_name(self) -> str:
        return "simulator"

    def health_check(self) -> dict:
        return {"healthy": True, "detail": "simulador sintético (sin red)"}

    def stream_events(self, n: int) -> Iterable[RealEstateEvent]:
        out: List[RealEstateEvent] = []
        for _ in range(max(0, int(n))):
            out.append(self._one())
        return out

    # ── generación ────────────────────────────────────────────────────────
    def _one(self) -> RealEstateEvent:
        rng = self._rng
        zone = rng.choice(_FL_ZONES)          # uniforme → sin prioridad geográfica
        ptype = rng.choice(_TYPES)
        tier = rng.choice(_TIERS)
        base_price = {
            "entry": rng.randint(180_000, 350_000),
            "mid": rng.randint(350_000, 750_000),
            "luxury": rng.randint(750_000, 4_000_000),
        }[tier]

        # 30% eventos terminales (con verdad objetiva) / 70% actividad.
        terminal = rng.random() < 0.30
        etype = rng.choice(_TERMINAL if terminal else _ACTIVITY)

        data = {"zone": zone, "type": ptype, "tier": tier, "list_price": base_price}

        if etype == "sale_closed":
            # sale_to_list en un rango realista (0.90–1.08).
            ratio = round(rng.uniform(0.90, 1.08), 4)
            data["sold_price"] = int(base_price * ratio)
            data["sale_to_list"] = ratio
            data["dom"] = rng.randint(3, 180)
            data["status"] = "closed"
        elif etype in ("expired", "withdrawn"):
            data["dom"] = rng.randint(60, 365)
            data["status"] = etype
        elif etype == "price_change":
            delta = round(rng.uniform(-0.08, 0.03), 4)   # sesgo leve a recortes
            data["price_change_pct"] = delta
            data["direction"] = "price_cut" if delta < 0 else "price_increase"
        elif etype == "status_change":
            data["status"] = rng.choice(["pending", "under_contract", "active"])
        # new_listing / open_house: solo el precio de lista y la zona.

        return RealEstateEvent(event_type=etype, data=data)
