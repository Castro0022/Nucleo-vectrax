"""
connectors/cybersecurity/dimensions.py — Extractores de dimensiones + subject ladder.

Puro y testeable (sin red; único estado es el mapa de familias cacheado).
Convierte el `data` normalizado de un CVEEvent en las 4 dimensiones del subject:
    product_family | vulnerability_class | attack_vector | severity_tier
y construye la escalera jerárquica (específico→general) para el fallback por masa.

Además expone `gravity_events()`: un (event_type, data) por nivel presente, de
forma que el fingerprint de gravity sea `cybersecurity:cve_<level>:<field>=<valor>`
tanto en el backfill (bulk) como en el incremental (`ingest_event`) — paridad de
identidad de estrellas.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Tuple

# Orden de la escalera: específico → general.
LADDER: Tuple[str, ...] = ("family", "class", "vector", "tier")

# level → (event_type de gravity, nombre de campo del dim en `data`)
LEVEL_EVENT: Dict[str, Tuple[str, str]] = {
    "family": ("cve_family", "product_family"),
    "class": ("cve_class", "vulnerability_class"),
    "vector": ("cve_vector", "attack_vector"),
    "tier": ("cve_tier", "severity_tier"),
}

_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}

# CWE → clase de vulnerabilidad (taxonomía curada; acota cardinalidad a ~decenas).
_CWE_CLASS: Dict[str, str] = {
    "CWE-79": "xss", "CWE-80": "xss",
    "CWE-89": "sql_injection",
    "CWE-78": "os_command_injection", "CWE-77": "command_injection",
    "CWE-94": "code_injection", "CWE-95": "code_injection",
    "CWE-22": "path_traversal", "CWE-23": "path_traversal", "CWE-36": "path_traversal",
    "CWE-352": "csrf",
    "CWE-434": "unrestricted_upload",
    "CWE-502": "deserialization",
    "CWE-787": "out_of_bounds_write", "CWE-125": "out_of_bounds_read",
    "CWE-416": "use_after_free", "CWE-415": "double_free",
    "CWE-476": "null_pointer_deref",
    "CWE-190": "integer_overflow", "CWE-191": "integer_underflow",
    "CWE-119": "memory_buffer", "CWE-120": "buffer_overflow",
    "CWE-121": "buffer_overflow", "CWE-122": "buffer_overflow",
    "CWE-287": "improper_authentication", "CWE-306": "missing_authentication",
    "CWE-862": "missing_authorization", "CWE-863": "incorrect_authorization",
    "CWE-269": "privilege_management", "CWE-266": "privilege_management",
    "CWE-250": "privilege_management",
    "CWE-918": "ssrf",
    "CWE-611": "xxe",
    "CWE-200": "info_exposure", "CWE-209": "info_exposure",
    "CWE-798": "hardcoded_credentials", "CWE-522": "weak_credential_protection",
    "CWE-20": "improper_input_validation",
    "CWE-284": "improper_access_control", "CWE-639": "improper_access_control",
    "CWE-400": "resource_exhaustion", "CWE-770": "resource_exhaustion",
    "CWE-601": "open_redirect",
    "CWE-732": "incorrect_permissions", "CWE-276": "incorrect_permissions",
    "CWE-843": "type_confusion",
    "CWE-367": "race_condition", "CWE-362": "race_condition",
    "CWE-295": "improper_cert_validation",
    "CWE-668": "resource_exposure",
    "CWE-74": "injection", "CWE-91": "xml_injection", "CWE-917": "expression_injection",
}
_CWE_IGNORE = {"NVD-CWE-NOINFO", "NVD-CWE-OTHER", "CWE-NOINFO", "CWE-OTHER", "UNSURE"}

_MAP_CACHE: Optional[Dict[str, Any]] = None


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _family_map() -> Dict[str, Any]:
    global _MAP_CACHE
    if _MAP_CACHE is not None:
        return _MAP_CACHE
    path = os.path.join(_repo_root(), "config", "cyber", "product_family_map.json")
    try:
        with open(path, encoding="utf-8") as f:
            _MAP_CACHE = json.load(f)
    except Exception:
        _MAP_CACHE = {"vendor_family": {}, "product_family": {}}
    return _MAP_CACHE


# ── Extractores por dimensión (puros) ─────────────────────────────────────────

def severity_tier(data: Mapping[str, Any]) -> Optional[str]:
    raw = str(data.get("base_severity") or "").strip().upper()
    return raw if raw in _SEVERITIES else None


def _av_token(tok: str) -> Optional[str]:
    t = tok.strip().upper()
    return {
        "N": "NETWORK", "NETWORK": "NETWORK",
        "A": "ADJACENT", "ADJACENT": "ADJACENT", "ADJACENT_NETWORK": "ADJACENT",
        "L": "LOCAL", "LOCAL": "LOCAL",
        "P": "PHYSICAL", "PHYSICAL": "PHYSICAL",
    }.get(t)


def attack_vector(data: Mapping[str, Any]) -> Optional[str]:
    raw = str(data.get("attack_vector") or "").strip().upper()
    if raw:
        v = _av_token(raw)
        if v:
            return v
    vec = str(data.get("cvss_vector") or "").upper()
    m = re.search(r"AV:([NALP])", vec)
    if m:
        return _av_token(m.group(1))
    raw2 = str(data.get("access_vector") or "").strip().upper()  # CVSS v2
    return _av_token(raw2) if raw2 else None


def vulnerability_class(data: Mapping[str, Any]) -> Optional[str]:
    cwes = data.get("cwes") or []
    if isinstance(cwes, str):
        cwes = [cwes]
    has_real = False
    for c in cwes:
        cid = str(c or "").strip().upper()
        if not cid or cid in _CWE_IGNORE:
            continue
        has_real = True
        cls = _CWE_CLASS.get(cid)
        if cls:
            return cls
    # Hay CWE real pero fuera de la taxonomía curada → "other_weakness" (acota cardinalidad).
    return "other_weakness" if has_real else None


def _cpe_products(data: Mapping[str, Any]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for item in (data.get("cpe_products") or []):
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append((str(item[0]).lower(), str(item[1]).lower()))
        elif isinstance(item, Mapping):
            out.append((str(item.get("vendor", "")).lower(), str(item.get("product", "")).lower()))
        elif isinstance(item, str) and ":" in item:
            v, p = item.split(":", 1)
            out.append((v.strip().lower(), p.strip().lower()))
    return [(v, p) for (v, p) in out if v]


def product_family(data: Mapping[str, Any]) -> Optional[str]:
    prods = _cpe_products(data)
    if not prods:
        return None
    vendor, product = Counter(prods).most_common(1)[0][0]
    fam = _family_map()
    key_vp = f"{vendor}:{product}"
    if key_vp in fam.get("product_family", {}):
        return fam["product_family"][key_vp]
    if vendor in fam.get("vendor_family", {}):
        return fam["vendor_family"][vendor]
    return key_vp  # fallback: vendor:product crudo (subject usable, menos agrupado)


# ── Escalera de subject + eventos de gravedad ────────────────────────────────

def extract_dims(data: Mapping[str, Any]) -> Dict[str, Optional[str]]:
    return {
        "product_family": product_family(data),
        "vulnerability_class": vulnerability_class(data),
        "attack_vector": attack_vector(data),
        "severity_tier": severity_tier(data),
    }


def subject_ladder(dims: Mapping[str, Optional[str]]) -> List[Tuple[str, str]]:
    """Escalera específico→general: [(level, subject_key)] omitiendo dims None.
    subject_key con prefijo de nivel para no colisionar entre dimensiones."""
    ladder: List[Tuple[str, str]] = []
    for level in LADDER:
        field_name = LEVEL_EVENT[level][1]
        val = dims.get(field_name)
        if val:
            ladder.append((level, f"{level}:{val}"))
    return ladder


def gravity_events(dims: Mapping[str, Optional[str]], cve_id: str = "") -> List[Tuple[str, Dict[str, Any]]]:
    """Un (event_type, data) por nivel presente, alineado con signature_fields del
    template → fingerprint `cybersecurity:cve_<level>:<field>=<valor>`."""
    out: List[Tuple[str, Dict[str, Any]]] = []
    for level in LADDER:
        event_type, field_name = LEVEL_EVENT[level]
        val = dims.get(field_name)
        if val:
            data: Dict[str, Any] = {field_name: val}
            if cve_id:
                data["cve_id"] = cve_id
            out.append((event_type, data))
    return out


def has_any_dim(dims: Mapping[str, Optional[str]]) -> bool:
    return any(dims.get(k) for k in ("product_family", "vulnerability_class",
                                     "attack_vector", "severity_tier"))


def coverage(rows: List[Mapping[str, Optional[str]]]) -> Dict[str, Any]:
    """Estadística de cobertura de dimensiones sobre un lote (para --dry-run)."""
    keys = ("product_family", "vulnerability_class", "attack_vector", "severity_tier")
    n = len(rows)
    denom = n or 1
    present = {k: sum(1 for r in rows if r.get(k)) for k in keys}
    neutral = sum(1 for r in rows if not has_any_dim(r))
    return {
        "n": n,
        "present_pct": {k: round(present[k] / denom * 100, 1) for k in keys},
        "neutral": neutral,
        "neutral_pct": round(neutral / denom * 100, 1),
    }
