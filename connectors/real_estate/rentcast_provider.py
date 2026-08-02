"""
connectors/real_estate/rentcast_provider.py — Proveedor RentCast (cliente REST fino).

RentCast (https://api.rentcast.io/v1) expone listings de venta/renta, registros de
propiedad (con última venta = verdad objetiva de cierre) y estadísticas de mercado
por ZIP. Se consume con `requests` (ya instalado) detrás del contrato
RealEstateFeedProvider; normaliza a `RealEstateEvent`. NO añade dependencias.

Gating: sin `RENTCAST_API_KEY`, `health_check()` → healthy=False y
`stream_events()` → []. Inerte hasta que exista la key (no rompe producción).
Defensivo: timeouts y try/except por llamada.

Endpoints (pilot):
    GET /listings/sale   — listings de venta por zona (actividad, precio, DOM)
    GET /properties      — registros con lastSalePrice/lastSaleDate (verdad de cierre)
Auth: header `X-Api-Key`. Docs: https://developers.rentcast.io

Env:
    RENTCAST_API_KEY          — API key (secreto; va en .env, nunca al repo/logs)
    RENTCAST_BASE_URL         — override del base (default v1)
    RENTCAST_FL_POSTALCODES   — CSV de ZIP FL a barrer (default: muestra)
    RENTCAST_INCLUDE_SOLD     — "1" para también traer ventas cerradas (default 0)
    RENTCAST_HTTP_TIMEOUT     — timeout por request (default 12s)

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, List, Mapping, Optional

from connectors.real_estate.base import RealEstateEvent, RealEstateFeedProvider

logger = logging.getLogger("vectrax.real_estate.rentcast")

_DEFAULT_BASE = "https://api.rentcast.io/v1"
# Muestra por defecto (NO es prioridad geográfica; punto de arranque acotado).
_DEFAULT_POSTALCODES = ["33139", "32801", "33602", "32202", "34102"]


def _api_key() -> str:
    return os.environ.get("RENTCAST_API_KEY", "").strip()


def _base_url() -> str:
    return os.environ.get("RENTCAST_BASE_URL", _DEFAULT_BASE).rstrip("/")


def _timeout() -> float:
    try:
        return float(os.environ.get("RENTCAST_HTTP_TIMEOUT", "12"))
    except ValueError:
        return 12.0


def _postalcodes() -> List[str]:
    raw = os.environ.get("RENTCAST_FL_POSTALCODES", "").strip()
    if raw:
        return [z.strip() for z in raw.split(",") if z.strip()]
    return list(_DEFAULT_POSTALCODES)


def _tier_from_price(price: Any) -> str:
    """Deriva un price_tier consistente con el simulador (entry/mid/luxury)."""
    try:
        p = float(price)
    except (TypeError, ValueError):
        return ""
    if p < 350_000:
        return "entry"
    if p < 750_000:
        return "mid"
    return "luxury"


class RentCastProvider(RealEstateFeedProvider):
    """Cliente REST de RentCast detrás del contrato del dominio (gated por API key)."""

    @property
    def provider_name(self) -> str:
        return "rentcast"

    def health_check(self) -> Dict[str, Any]:
        if not _api_key():
            return {"healthy": False, "detail": "RENTCAST_API_KEY ausente (proveedor inerte)"}
        try:
            data = self._get("/listings/sale",
                             {"zipCode": _postalcodes()[0], "limit": 1})
            if data is None:
                return {"healthy": False, "detail": "sin respuesta de RentCast"}
            return {"healthy": True, "detail": "RentCast alcanzable"}
        except Exception as exc:  # nunca lanza
            return {"healthy": False, "detail": f"error RentCast: {str(exc)[:120]}"}

    def stream_events(self, n: int) -> Iterable[RealEstateEvent]:
        if not _api_key():
            return []
        out: List[RealEstateEvent] = []
        zips = _postalcodes()
        per_zone = max(1, int(n) // max(1, len(zips)))
        include_sold = os.environ.get("RENTCAST_INCLUDE_SOLD", "0") == "1"

        for zipc in zips:
            if len(out) >= n:
                break
            # 1) Actividad: listings de venta.
            try:
                listings = self._get("/listings/sale",
                                     {"zipCode": zipc, "limit": per_zone})
            except Exception as exc:
                logger.debug("rentcast listings/sale %s error: %s", zipc, exc)
                listings = None
            for rec in _as_list(listings):
                ev = _normalize_listing(rec, fallback_zip=zipc)
                if ev is not None:
                    out.append(ev)
                    if len(out) >= n:
                        break

            # 2) Verdad objetiva de cierre (opcional, ahorra cuota por defecto).
            if include_sold and len(out) < n:
                try:
                    props = self._get("/properties",
                                     {"zipCode": zipc, "limit": per_zone})
                except Exception as exc:
                    logger.debug("rentcast properties %s error: %s", zipc, exc)
                    props = None
                for rec in _as_list(props):
                    ev = _normalize_property_sale(rec, fallback_zip=zipc)
                    if ev is not None:
                        out.append(ev)
                        if len(out) >= n:
                            break
        return out

    # ── HTTP ────────────────────────────────────────────────────────────────
    def _get(self, path: str, params: Mapping[str, Any]) -> Any:
        import requests  # local: no encarece el import del módulo
        url = f"{_base_url()}{path}"
        headers = {"X-Api-Key": _api_key(), "Accept": "application/json"}
        resp = requests.get(url, headers=headers, params=dict(params), timeout=_timeout())
        resp.raise_for_status()
        return resp.json()


# ── Normalización (pura y testeable) ─────────────────────────────────────────

def _as_list(data: Any) -> List[Mapping[str, Any]]:
    """RentCast devuelve una lista JSON en estos endpoints; robusto a variantes."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, Mapping)]
    if isinstance(data, Mapping):
        # por si viniera envuelto (p. ej. {"data": [...]})
        inner = data.get("data")
        if isinstance(inner, list):
            return [r for r in inner if isinstance(r, Mapping)]
    return []


def _normalize_listing(rec: Mapping[str, Any], fallback_zip: str = "") -> Optional[RealEstateEvent]:
    """Listing de venta RentCast → evento de actividad.

    Active → new_listing; Inactive/removido → status_change (off_market, no terminal).
    """
    if not isinstance(rec, Mapping):
        return None
    zone = str(rec.get("zipCode") or fallback_zip or "").strip()
    ptype = str(rec.get("propertyType") or "").lower()
    price = rec.get("price")
    status = str(rec.get("status") or "").lower()
    removed = rec.get("removedDate")

    data: Dict[str, Any] = {
        "zone": zone,
        "type": ptype,
        "tier": _tier_from_price(price),
        "list_price": price,
        "dom": rec.get("daysOnMarket"),
        "beds": rec.get("bedrooms"),
        "baths": rec.get("bathrooms"),
        "sqft": rec.get("squareFootage"),
        "source": "rentcast",
    }
    if status == "inactive" or removed:
        data["status"] = "off_market"
        data["removed_date"] = removed
        return RealEstateEvent(event_type="status_change", data=data)
    data["status"] = "active"
    return RealEstateEvent(event_type="new_listing", data=data)


def _normalize_property_sale(rec: Mapping[str, Any], fallback_zip: str = "") -> Optional[RealEstateEvent]:
    """Registro de propiedad RentCast → sale_closed si tiene última venta.

    Devuelve None si no hay lastSalePrice (sin verdad de cierre).
    """
    if not isinstance(rec, Mapping):
        return None
    sold_price = rec.get("lastSalePrice")
    if sold_price in (None, 0, "0"):
        return None
    zone = str(rec.get("zipCode") or fallback_zip or "").strip()
    data: Dict[str, Any] = {
        "zone": zone,
        "type": str(rec.get("propertyType") or "").lower(),
        "tier": _tier_from_price(sold_price),
        "sold_price": sold_price,
        "sale_date": rec.get("lastSaleDate"),
        "sqft": rec.get("squareFootage"),
        "status": "closed",
        "source": "rentcast",
    }
    return RealEstateEvent(event_type="sale_closed", data=data)
