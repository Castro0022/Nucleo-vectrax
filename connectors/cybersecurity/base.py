"""
connectors/cybersecurity/base.py — Contrato del proveedor del dominio cybersecurity.

Espejo de connectors/real_estate/base.py: una observación canónica por CVE y una
interfaz de proveedor agnóstica a la fuente (NVD, simulador, …). El resto del
sistema (dims → subject ladder → outcome → verificación → gravity → criterio) es
invariante.

entity = CVE individual. Los 4 dims del subject (product_family,
vulnerability_class, attack_vector, severity_tier) se extraen en `dimensions.py`.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable

# Clave de dominio (única fuente de verdad para todo el conector).
DOMAIN = "cybersecurity"


@dataclass
class CVEEvent:
    """Observación canónica de una CVE (agnóstica a la fuente).

    `data` lleva el payload NORMALIZADO por el proveedor. Campos esperados
    (opcionales salvo cve_id/published):
        cve_id:        "CVE-2021-1234"
        published:     ISO-8601 str
        base_severity: "CRITICAL"|"HIGH"|"MEDIUM"|"LOW"|""     (CVSS)
        attack_vector: "NETWORK"|"ADJACENT"|"LOCAL"|"PHYSICAL"|""  (CVSS AV)
        cvss_vector:   str crudo (fallback para derivar AV)
        cwes:          List[str]  (p. ej. ["CWE-79"])
        cpe_products:  List[[vendor, product]]  (de las configurations CPE)
        in_kev:        bool
        kev_date:      ISO-8601 str  (CISA dateAdded / cisaExploitAdd)
        ransomware:    bool
    """
    event_type: str = "cve"
    data: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    @property
    def cve_id(self) -> str:
        return str(self.data.get("cve_id") or "").strip().upper()

    @property
    def published(self) -> str:
        return str(self.data.get("published") or "").strip()

    @property
    def in_kev(self) -> bool:
        return bool(self.data.get("in_kev"))


class CyberFeedProvider(ABC):
    """Interfaz de proveedor del dominio (misma forma que freight/real_estate)."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Nombre corto del proveedor (p. ej. 'nvd', 'simulator')."""

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """Estado del proveedor: {'healthy': bool, 'detail': str}. NUNCA lanza."""

    @abstractmethod
    def stream_events(self, n: int) -> Iterable[CVEEvent]:
        """Hasta `n` CVEEvent normalizados. Si no está sano, secuencia vacía."""
