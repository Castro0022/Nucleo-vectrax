"""
Vectrax Core — Capabilities Registry
======================================
Stub for future rate limits, API versioning, and feature flags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class Capability:
    name: str
    version: str = "1.0"
    enabled: bool = True
    rate_limit_rpm: int = 0  # 0 = unlimited
    description: str = ""


_BUILTIN: List[Capability] = [
    Capability(name="proposals", version="1.0", description="Generate code proposals"),
    Capability(name="events", version="1.0", description="Event ingestion from agents"),
    Capability(name="connectors", version="1.0", description="Connector management"),
]


class CapabilitiesRegistry:
    """Registry of available capabilities and their rate limits."""

    def __init__(self) -> None:
        self._caps: Dict[str, Capability] = {}
        for cap in _BUILTIN:
            self._caps[cap.name] = cap

    def register(self, cap: Capability) -> None:
        self._caps[cap.name] = cap

    def get(self, name: str) -> Capability | None:
        return self._caps.get(name)

    def list_all(self) -> List[Capability]:
        return list(self._caps.values())

    def is_enabled(self, name: str) -> bool:
        cap = self._caps.get(name)
        return cap.enabled if cap else False


_registry: CapabilitiesRegistry | None = None


def get_capabilities_registry() -> CapabilitiesRegistry:
    global _registry
    if _registry is None:
        _registry = CapabilitiesRegistry()
    return _registry
