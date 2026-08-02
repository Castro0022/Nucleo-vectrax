"""
connectors/real_estate/attom_provider.py — Proveedor ATTOM (cliente REST fino).

ATTOM no tiene CLI ni SDK oficial: es una API REST. Este proveedor la consume con
`requests` (ya instalado) detrás del contrato RealEstateFeedProvider, y normaliza
las respuestas a `RealEstateEvent`. NO añade dependencias nuevas.

Gating: si `ATTOM_API_KEY` no está, `health_check()` devuelve healthy=False y
`stream_events()` devuelve []. Así el proveedor es INERTE hasta que exista la key
(no rompe nada en producción). Defensivo: timeouts y try/except en cada llamada.

Endpoints usados (pilot; ajustables al cablear la key):
    GET /property/snapshot   — inventario/props por zona (postalcode)
    GET /sale/snapshot       — ventas recientes por zona (verdad objetiva de cierre)
Docs: https://api.developer.attomdata.com/docs

Env:
    ATTOM_API_KEY            — API key (secreto; va en .env, nunca al repo/logs)
    ATTOM_BASE_URL           — override del base (default gateway v1.0.0)
    ATTOM_FL_POSTALCODES     — lista CSV de ZIP FL a barrer (default: muestra)
    ATTOM_HTTP_TIMEOUT       — timeout por request (default 12s)

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, List, Mapping, Optional

from connectors.real_estate.base import RealEstateEvent, RealEstateFeedProvider

logger = logging.getLogger("vectrax.real_estate.attom")

_DEFAULT_BASE = "https://api.gateway.attomdata.com/propertyapi/v1.0.0"
# Muestra por defecto (NO es prioridad geográfica; solo un punto de arranque
# acotado del piloto). Statewide real = barrer condados/ZIP completos vía env.
_DEFAULT_POSTALCODES = ["33139", "32801", "33602", "32202", "34102"]


def _api_key() -> str:
    return os.environ.get("ATTOM_API_KEY", "").strip()


def _base_url() -> str:
    return os.environ.get("ATTOM_BASE_URL", _DEFAULT_BASE).rstrip("/")


def _timeout() -> float:
    try:
        return float(os.environ.get("ATTOM_HTTP_TIMEOUT", "12"))
    except ValueError:
        return 12.0


def _postalcodes() -> List[str]:
    raw = os.environ.get("ATTOM_FL_POSTALCODES", "").strip()
    if raw:
        return [z.strip() for z in raw.split(",") if z.strip()]
    return list(_DEFAULT_POSTALCODES)


class AttomProvider(RealEstateFeedProvider):
    """Cliente REST de ATTOM detrás del contrato del dominio (gated por API key)."""

    @property
    def provider_name(self) -> str:
        return "attom"

    def health_check(self) -> Dict[str, Any]:
        key = _api_key()
        if not key:
            return {"healthy": False, "detail": "ATTOM_API_KEY ausente (proveedor inerte)"}
        # Ping mínimo (1 request, timeout corto). Ante cualquier fallo → unhealthy.
        try:
            data = self._get("/sale/snapshot",
                             {"postalcode": _postalcodes()[0], "pagesize": 1})
            if data is None:
                return {"healthy": False, "detail": "sin respuesta de ATTOM"}
            return {"healthy": True, "detail": "ATTOM alcanzable"}
        except Exception as exc:  # nunca lanza
            return {"healthy": False, "detail": f"error ATTOM: {str(exc)[:120]}"}

    def stream_events(self, n: int) -> Iterable[RealEstateEvent]:
        if not _api_key():
            return []
        out: List[RealEstateEvent] = []
        per_zone = max(1, int(n) // max(1, len(_postalcodes())))
        for zipc in _postalcodes():
            if len(out) >= n:
                break
            try:
                data = self._get("/sale/snapshot",
                                 {"postalcode": zipc, "pagesize": per_zone})
            except Exception as exc:
                logger.debug("attom sale/snapshot %s error: %s", zipc, exc)
                continue
            for rec in _iter_properties(data):
                ev = _normalize_sale(rec, fallback_zip=zipc)
                if ev is not None:
                    out.append(ev)
                    if len(out) >= n:
                        break
        return out

    # ── HTTP ────────────────────────────────────────────────────────────────
    def _get(self, path: str, params: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        import requests  # local: no encarece el import del módulo
        url = f"{_base_url()}{path}"
        headers = {"apikey": _api_key(), "Accept": "application/json"}
        resp = requests.get(url, headers=headers, params=dict(params), timeout=_timeout())
        resp.raise_for_status()
        return resp.json()


# ── Normalización (pura y testeable) ─────────────────────────────────────────

def _iter_properties(data: Optional[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    if not isinstance(data, Mapping):
        return []
    props = data.get("property")
    return props if isinstance(props, list) else []


def _normalize_sale(rec: Mapping[str, Any], fallback_zip: str = "") -> Optional[RealEstateEvent]:
    """Mapea un registro de venta ATTOM → RealEstateEvent(sale_closed).

    Robusto a campos ausentes: si no hay monto de venta, devuelve None.
    """
    if not isinstance(rec, Mapping):
        return None
    address = rec.get("address") or {}
    sale = rec.get("sale") or {}
    amount = sale.get("amount") or {}
    summary = rec.get("summary") or {}
    building = rec.get("building") or {}
    size = building.get("size") or {}

    sold_price = amount.get("saleamt")
    if sold_price in (None, 0, "0"):
        return None  # sin verdad de cierre → no es un sale_closed útil

    zone = str(address.get("postal1") or fallback_zip or "").strip()
    data: Dict[str, Any] = {
        "zone": zone,
        "type": str(summary.get("proptype") or summary.get("propclass") or "").lower(),
        "sold_price": _to_int(sold_price),
        "sale_date": amount.get("salerecdate") or sale.get("salesearchdate"),
        "sqft": size.get("livingsize") or size.get("universalsize"),
        "status": "closed",
        "source": "attom",
    }
    return RealEstateEvent(event_type="sale_closed", data=data)


def _to_int(v: Any) -> Any:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return v
