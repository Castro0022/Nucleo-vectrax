"""
connectors/cybersecurity/nvd_provider.py — Proveedor NVD API 2.0.

Cliente REST fino sobre https://services.nvd.nist.gov/rest/json/cves/2.0 con:
  - paginación offset (`startIndex`/`resultsPerPage`=2000),
  - ventanas de fecha `pubStartDate/pubEndDate` (≤120 días) o `lastMod*`,
  - rate-limit (espaciado) + backoff en 403/429/5xx,
  - `NVD_API_KEY` opcional (sube el límite),
  - extracción de CVSS (v4.0/v3.1/v3.0/v2), CWE, CPE (vendor:product) y KEV
    (`cisaExploitAdd`), normalizando a `CVEEvent`.

`normalize_cve` es pura (sin red) y testeable con un item NVD sintético.
`NvdClient.iter_window` es el motor de paginación que usa el backfill.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

from connectors.cybersecurity.base import CVEEvent, CyberFeedProvider

try:
    import requests  # a nivel de módulo para permitir monkeypatch en tests
except Exception:  # pragma: no cover
    requests = None  # type: ignore

logger = logging.getLogger("vectrax.cybersecurity.nvd")

BASE_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
PAGE_SIZE = 2000
MAX_WINDOW_DAYS = 120
MIN_PUB = datetime(2020, 1, 1, tzinfo=timezone.utc)  # alcance histórico inicial


def _fmt(dt: datetime) -> str:
    """ISO-8601 sin zona, con milisegundos (formato aceptado por NVD)."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000")


def _api_key() -> str:
    return os.environ.get("NVD_API_KEY", "").strip()


def _delay() -> float:
    try:
        # NVD recomienda ~6s entre requests sin key; con key se puede menos.
        default = "0.8" if _api_key() else "6.0"
        return float(os.environ.get("NVD_REQUEST_DELAY", default))
    except ValueError:
        return 6.0


class NvdClient:
    """Cliente HTTP con paginación y backoff. Sin lógica de dominio."""

    def __init__(self, page_size: int = PAGE_SIZE, timeout: float = 30.0,
                 max_retries: int = 5, backoff: float = 2.0,
                 delay: Optional[float] = None) -> None:
        self.page_size = int(page_size)
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.backoff = float(backoff)
        self.delay = _delay() if delay is None else float(delay)

    def _headers(self) -> Dict[str, str]:
        h = {"Accept": "application/json"}
        key = _api_key()
        if key:
            h["apiKey"] = key
        return h

    def _request(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        if requests is None:
            raise RuntimeError("requests no disponible")
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.get(BASE_URL, params=dict(params),
                                    headers=self._headers(), timeout=self.timeout)
            except Exception as exc:  # error de red → backoff y reintenta
                last_exc = exc
                time.sleep(self.backoff * attempt)
                continue
            sc = getattr(resp, "status_code", 0)
            if sc == 200:
                return resp.json()
            if sc in (403, 429, 500, 502, 503, 504):
                logger.debug("NVD %s (intento %d) → backoff", sc, attempt)
                time.sleep(self.backoff * attempt)
                continue
            resp.raise_for_status()
            return {}
        if last_exc:
            raise last_exc
        raise RuntimeError("NVD request falló tras reintentos")

    def _sleep(self) -> None:
        if self.delay > 0:
            time.sleep(self.delay)

    def iter_window(self, start: datetime, end: datetime,
                    use_lastmod: bool = False) -> Iterator[Dict[str, Any]]:
        """Pagina una ventana de fecha y va emitiendo items crudos NVD
        (`vulnerabilities[i]`). Respeta el espaciado entre páginas."""
        start_index = 0
        while True:
            params: Dict[str, Any] = {
                "resultsPerPage": self.page_size,
                "startIndex": start_index,
            }
            if use_lastmod:
                params["lastModStartDate"] = _fmt(start)
                params["lastModEndDate"] = _fmt(end)
            else:
                params["pubStartDate"] = _fmt(start)
                params["pubEndDate"] = _fmt(end)
            data = self._request(params)
            vulns = data.get("vulnerabilities", []) or []
            for v in vulns:
                yield v
            total = int(data.get("totalResults", 0) or 0)
            start_index += len(vulns)
            if not vulns or start_index >= total:
                break
            self._sleep()


# ── Ventanas (≥2020, ≤120 días) ──────────────────────────────────────────────

def date_windows(since: datetime, until: datetime,
                 max_days: int = MAX_WINDOW_DAYS) -> List[Tuple[datetime, datetime]]:
    """Trocea [since, until] en ventanas de ≤max_days. Nunca antes de MIN_PUB."""
    if since < MIN_PUB:
        since = MIN_PUB
    out: List[Tuple[datetime, datetime]] = []
    cur = since
    step = timedelta(days=max_days)
    while cur < until:
        nxt = min(cur + step, until)
        out.append((cur, nxt))
        cur = nxt
    return out


# ── Normalización NVD 2.0 → CVEEvent (pura) ──────────────────────────────────

def _severity_and_vector(metrics: Mapping[str, Any]) -> Tuple[str, str]:
    for key in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30"):
        arr = metrics.get(key)
        if arr:
            d = (arr[0] or {}).get("cvssData", {}) or {}
            return str(d.get("baseSeverity") or ""), str(d.get("attackVector") or "")
    arr = metrics.get("cvssMetricV2")
    if arr:
        m0 = arr[0] or {}
        d = m0.get("cvssData", {}) or {}
        # En v2 la severidad cualitativa está a nivel de métrica.
        return str(m0.get("baseSeverity") or ""), str(d.get("accessVector") or "")
    return "", ""


def _cwes(weaknesses: Any) -> List[str]:
    out: List[str] = []
    for w in weaknesses or []:
        for d in (w or {}).get("description", []) or []:
            val = str((d or {}).get("value") or "").strip()
            if val and (val.upper().startswith("CWE-") or val.upper().startswith("NVD-CWE")):
                out.append(val)
    return out


def _cpe_products(configurations: Any) -> List[List[str]]:
    seen = set()
    out: List[List[str]] = []
    for cfg in configurations or []:
        for node in (cfg or {}).get("nodes", []) or []:
            for m in (node or {}).get("cpeMatch", []) or []:
                crit = str((m or {}).get("criteria") or "")
                parts = crit.split(":")
                # cpe:2.3:part:vendor:product:version:...
                if len(parts) >= 6 and parts[0] == "cpe":
                    vendor, product = parts[3], parts[4]
                    if vendor and vendor != "*":
                        key = (vendor, product)
                        if key not in seen:
                            seen.add(key)
                            out.append([vendor, product])
    return out


def normalize_cve(item: Mapping[str, Any],
                  kev_map: Optional[Mapping[str, Mapping[str, Any]]] = None) -> CVEEvent:
    """Convierte un item NVD 2.0 (`{"cve": {...}}`) en un CVEEvent normalizado."""
    cve = item.get("cve", item) if isinstance(item, Mapping) else {}
    cid = str(cve.get("id") or "").strip().upper()
    published = str(cve.get("published") or "").strip()
    severity, vector = _severity_and_vector(cve.get("metrics", {}) or {})
    cwes = _cwes(cve.get("weaknesses", []))
    cpe = _cpe_products(cve.get("configurations", []))

    kev_date = str(cve.get("cisaExploitAdd") or "").strip()
    in_kev = bool(kev_date)
    ransomware = False
    if kev_map and cid in kev_map:
        in_kev = True
        kev_date = kev_date or str(kev_map[cid].get("date_added") or "")
        ransomware = bool(kev_map[cid].get("ransomware"))

    data: Dict[str, Any] = {
        "cve_id": cid,
        "published": published,
        "last_modified": str(cve.get("lastModified") or ""),
        "vuln_status": str(cve.get("vulnStatus") or ""),
        "base_severity": severity,
        "attack_vector": vector,
        "cwes": cwes,
        "cpe_products": cpe,
        "in_kev": in_kev,
        "kev_date": kev_date,
        "ransomware": ransomware,
    }
    return CVEEvent(event_type="cve", data=data)


# ── Proveedor (contrato del dominio) ─────────────────────────────────────────

class NvdProvider(CyberFeedProvider):
    """CyberFeedProvider sobre NVD. `stream_events` = incremental por lastMod."""

    def __init__(self, client: Optional[NvdClient] = None) -> None:
        self._client = client or NvdClient()

    @property
    def provider_name(self) -> str:
        return "nvd"

    def health_check(self) -> Dict[str, Any]:
        if requests is None:
            return {"healthy": False, "detail": "requests no disponible"}
        try:
            data = self._client._request({"resultsPerPage": 1, "startIndex": 0})
            if "totalResults" in data:
                return {"healthy": True, "detail": f"NVD alcanzable ({data.get('totalResults')} CVE)"}
            return {"healthy": False, "detail": "respuesta NVD inesperada"}
        except Exception as exc:  # nunca lanza
            return {"healthy": False, "detail": f"error NVD: {str(exc)[:120]}"}

    def stream_events(self, n: int) -> Iterable[CVEEvent]:
        try:
            days = int(os.environ.get("CYBER_INCREMENTAL_DAYS", "2"))
        except ValueError:
            days = 2
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        out: List[CVEEvent] = []
        try:
            for item in self._client.iter_window(start, end, use_lastmod=True):
                out.append(normalize_cve(item))
                if len(out) >= int(n):
                    break
        except Exception as exc:
            logger.debug("nvd stream_events error: %s", exc)
            return []
        return out
