"""
connectors/cybersecurity/simulator_adapter.py — Proveedor simulador (hermético).

Genera CVEEvent sintéticos deterministas (seed) con dims variadas y KEV/timing
controlados, para tests y piloto SIN red. No llama a NVD/CISA.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List

from connectors.cybersecurity.base import CVEEvent, CyberFeedProvider

_SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
_VECTORS = ["NETWORK", "ADJACENT", "LOCAL", "PHYSICAL"]
_CWES = ["CWE-79", "CWE-89", "CWE-787", "CWE-22", "CWE-352",
         "CWE-416", "CWE-434", "CWE-502", "CWE-918", "CWE-287"]
_VENDORS = [
    ("microsoft", "windows"), ("cisco", "ios_xe"), ("google", "chrome"),
    ("adobe", "acrobat_reader"), ("fortinet", "fortios"), ("apache", "http_server"),
    ("vmware", "vcenter_server"), ("ivanti", "connect_secure"),
    ("citrix", "netscaler"), ("oracle", "weblogic_server"),
    ("apple", "macos"), ("progress", "moveit_transfer"),
]

_NOW = datetime(2026, 8, 2, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000")


class SimulatorAdapter(CyberFeedProvider):
    """CVEs sintéticas deterministas. `kev_ratio`/`neutral_ratio` controlan la
    mezcla; `include_pre2020` inyecta algunas <2020 para tests de exclusión."""

    def __init__(self, seed: int = 1337, kev_ratio: float = 0.15,
                 neutral_ratio: float = 0.08, include_pre2020: bool = False) -> None:
        self._rng = random.Random(seed)
        self._kev_ratio = kev_ratio
        self._neutral_ratio = neutral_ratio
        self._include_pre2020 = include_pre2020

    @property
    def provider_name(self) -> str:
        return "simulator"

    def health_check(self) -> Dict[str, Any]:
        return {"healthy": True, "detail": "simulador cyber (sin red)"}

    def stream_events(self, n: int) -> Iterable[CVEEvent]:
        r = self._rng
        out: List[CVEEvent] = []
        for i in range(int(n)):
            if self._include_pre2020 and r.random() < 0.1:
                year = r.choice([2017, 2018, 2019])
            else:
                year = r.choice([2020, 2021, 2022, 2023, 2024, 2025, 2026])
            pub = datetime(year, r.randint(1, 12), r.randint(1, 28), tzinfo=timezone.utc)
            cve_id = f"CVE-{year}-{10000 + i}"
            neutral = r.random() < self._neutral_ratio

            if neutral:
                data: Dict[str, Any] = {
                    "cve_id": cve_id, "published": _iso(pub),
                    "base_severity": "", "attack_vector": "", "cwes": [], "cpe_products": [],
                }
            else:
                vend = _VENDORS[r.randrange(len(_VENDORS))]
                data = {
                    "cve_id": cve_id, "published": _iso(pub),
                    "base_severity": r.choice(_SEVERITIES),
                    "attack_vector": r.choice(_VECTORS),
                    "cwes": [r.choice(_CWES)],
                    "cpe_products": [list(vend)],
                }

            in_kev = (not neutral) and (r.random() < self._kev_ratio)
            if in_kev:
                b = r.random()
                if b < 0.5:
                    delta = r.randint(0, 30)      # EARLY
                elif b < 0.85:
                    delta = r.randint(31, 180)    # LATE
                else:
                    delta = r.randint(181, 900)   # NONE
                data["in_kev"] = True
                data["kev_date"] = _iso(pub + timedelta(days=delta))
                data["ransomware"] = r.random() < 0.2
            else:
                data["in_kev"] = False

            out.append(CVEEvent(event_type="cve", data=data))
        return out
