"""
connectors/cybersecurity/kev_client.py — Cliente del catálogo CISA KEV.

Descarga y conserva (snapshot) el catálogo oficial de Known Exploited
Vulnerabilities y construye el mapa de reconciliación {cve_id → dateAdded,
ransomware, vendor, product} para cruzar con NVD por `cve_id`.

Defensivo: timeouts y try/except; `build_kev_map`/`load_kev` no tocan la red y
son testeables con un JSON sintético.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("vectrax.cybersecurity.kev")

KEV_URL = os.environ.get(
    "CISA_KEV_URL",
    "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
)


def _vault_dir() -> str:
    return os.environ.get(
        "VECTRAX_VAULT_DIR",
        os.path.join(os.path.expanduser("~"), "Vectrax", "vault"),
    )


def _snapshot_dir() -> str:
    return os.path.join(_vault_dir(), "cyber_kev")


def _snapshot_path() -> str:
    return os.path.join(_snapshot_dir(), "known_exploited_vulnerabilities.json")


def _write_snapshot(data: Dict[str, Any]) -> None:
    try:
        os.makedirs(_snapshot_dir(), exist_ok=True)
        with open(_snapshot_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        # copia fechada (histórico conservado)
        dated = os.path.join(_snapshot_dir(), f"kev_{time.strftime('%Y%m%d')}.json")
        with open(dated, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as exc:
        logger.debug("kev snapshot write failed: %s", exc)


def fetch_kev(timeout: float = 30.0, snapshot: bool = True) -> Dict[str, Any]:
    """Descarga el catálogo KEV (JSON). Conserva snapshot si `snapshot`.
    Nunca lanza silenciosamente hacia el caller de negocio: si falla, propaga
    para que el orquestador decida (el backfill lo captura)."""
    import requests  # local: no encarece el import del módulo
    resp = requests.get(KEV_URL, headers={"Accept": "application/json"}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if snapshot:
        _write_snapshot(data)
    return data


def load_kev(path: Optional[str] = None) -> Dict[str, Any]:
    """Carga el catálogo desde el snapshot local (o `path`). {} si no existe."""
    p = path or _snapshot_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.debug("kev load failed: %s", exc)
        return {}


def build_kev_map(kev_json: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Mapa {cve_id → {date_added, ransomware, vendor, product, name}}. Puro."""
    out: Dict[str, Dict[str, Any]] = {}
    for v in (kev_json or {}).get("vulnerabilities", []) or []:
        cid = str(v.get("cveID") or "").strip().upper()
        if not cid:
            continue
        ransom = str(v.get("knownRansomwareCampaignUse") or "").strip().lower() == "known"
        out[cid] = {
            "date_added": str(v.get("dateAdded") or "").strip(),
            "ransomware": ransom,
            "vendor": str(v.get("vendorProject") or "").strip(),
            "product": str(v.get("product") or "").strip(),
            "name": str(v.get("vulnerabilityName") or "").strip(),
        }
    return out


def health(path: Optional[str] = None) -> Dict[str, Any]:
    data = load_kev(path)
    n = len((data or {}).get("vulnerabilities", []) or [])
    if n > 0:
        return {"healthy": True, "detail": f"KEV snapshot con {n} entradas"}
    return {"healthy": False, "detail": "sin snapshot KEV (ejecuta fetch_kev)"}
