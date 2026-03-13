"""
Vectrax Connectors — Base Interfaces
=======================================
ConnectorSpec:    declarative metadata (name, scopes, permissions, version).
ConnectorAdapter: abstract base with lifecycle hooks.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ConnectorSpec:
    """Declarative specification for a connector."""
    name: str
    scopes: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    version: str = "0.1.0"
    description: str = ""
    author: str = ""


class ConnectorAdapter(ABC):
    """
    Abstract adapter that every connector must implement.

    Lifecycle:
        1. __init__(spec)    — construct with spec
        2. auth_handshake()  — authenticate / obtain credentials
        3. list_resources()  — enumerate available resources
        4. healthcheck()     — return True if connector is operational
        5. test()            — run self-test (returns dict with results)
    """

    def __init__(self, spec: ConnectorSpec):
        self._spec = spec

    @property
    def spec(self) -> ConnectorSpec:
        return self._spec

    @abstractmethod
    def auth_handshake(self, credentials: Dict[str, Any] | None = None) -> bool:
        """
        Perform auth handshake. Returns True on success.
        Stub implementations should return True immediately.
        """
        ...

    @abstractmethod
    def list_resources(self) -> List[Dict[str, Any]]:
        """
        List available resources from this connector.
        Returns list of dicts with at least {id, name, type}.
        """
        ...

    @abstractmethod
    def healthcheck(self) -> bool:
        """Return True if the connector is healthy."""
        ...

    @abstractmethod
    def test(self) -> Dict[str, Any]:
        """
        Run self-test.
        Returns dict with at least {passed: bool, details: str}.
        """
        ...
