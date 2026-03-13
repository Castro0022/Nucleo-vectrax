"""
Vectrax UCE — UniversalConnector interface.

Every connector in the UCE must implement this ABC.  It extends the
legacy ConnectorAdapter for backward-compatibility while adding the
full universal lifecycle: authenticate, read, write, observe,
disconnect, normalize, validate_permissions, generate_proposal.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Callable, Dict, List, Optional

from connectors.base import ConnectorAdapter, ConnectorSpec
from connectors.core.types import ConnectorCapability, ConnectorStatus


class UniversalConnector(ConnectorAdapter):
    """
    Abstract base for all UCE connectors.

    Subclasses MUST implement the abstract methods.  Optional methods
    (observe, write) may raise NotImplementedError if the connector
    does not support that capability — the contract validator will
    cross-check declared capabilities vs implemented methods.
    """

    def __init__(self, spec: ConnectorSpec) -> None:
        super().__init__(spec)
        self._status: ConnectorStatus = ConnectorStatus.IDLE
        self._last_error: Optional[str] = None

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @property
    def status(self) -> ConnectorStatus:
        return self._status

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def _set_status(self, status: ConnectorStatus, error: str | None = None) -> None:
        self._status = status
        self._last_error = error

    # ------------------------------------------------------------------
    # Lifecycle — authentication
    # ------------------------------------------------------------------

    @abstractmethod
    def authenticate(self, credentials: Dict[str, Any] | None = None) -> bool:
        """
        Perform authentication.  Returns True on success.
        Must update internal status to CONNECTED or ERROR.
        """
        ...

    # Legacy compat — delegate to authenticate()
    def auth_handshake(self, credentials: Dict[str, Any] | None = None) -> bool:
        return self.authenticate(credentials)

    # ------------------------------------------------------------------
    # Lifecycle — read / write / observe
    # ------------------------------------------------------------------

    @abstractmethod
    def read(self, query: Dict[str, Any]) -> Dict[str, Any]:
        """
        Read data from the external source.

        *query* is connector-specific (e.g. {"path": "/tmp"} for
        filesystem, {"endpoint": "/users"} for REST).

        Returns raw response dict.
        """
        ...

    def write(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Write data to the external source.
        Default: raise NotImplementedError (read-only connectors).
        """
        raise NotImplementedError(
            f"{self.spec.name} does not support write operations."
        )

    def observe(
        self,
        event_filter: Dict[str, Any] | None = None,
        callback: Callable[[Dict[str, Any]], None] | None = None,
    ) -> Dict[str, Any]:
        """
        Subscribe to real-time events from the external source.
        Default: raise NotImplementedError.
        """
        raise NotImplementedError(
            f"{self.spec.name} does not support observe operations."
        )

    # ------------------------------------------------------------------
    # Lifecycle — disconnect
    # ------------------------------------------------------------------

    def disconnect(self) -> None:
        """Gracefully close the connection and release resources."""
        self._set_status(ConnectorStatus.DISCONNECTED)

    # ------------------------------------------------------------------
    # Normalisation hook
    # ------------------------------------------------------------------

    def normalize_response(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """
        Connector-specific normalisation.  Override to transform the raw
        response before the global NormalizationEngine processes it.

        Default: pass-through.
        """
        return raw

    # ------------------------------------------------------------------
    # Permissions
    # ------------------------------------------------------------------

    def validate_permissions(self, required: List[str]) -> bool:
        """
        Check whether this connector's spec grants *required* permissions.
        Returns True if all required permissions are satisfied.
        """
        granted = set(self.spec.permissions)
        return all(p in granted for p in required)

    # ------------------------------------------------------------------
    # Proposal generation
    # ------------------------------------------------------------------

    def generate_proposal(self) -> Dict[str, Any]:
        """
        Build a connection-proposal dict that will be presented to the
        creator for approval before this connector is activated.
        """
        return {
            "connector_name": self.spec.name,
            "connector_version": self.spec.version,
            "capabilities": [
                s for s in self.spec.scopes
            ],
            "permissions_required": list(self.spec.permissions),
            "description": self.spec.description,
            "risks": [],
            "status": "pending",
        }

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def declared_capabilities(self) -> List[ConnectorCapability]:
        """
        Return the list of capabilities this connector actually supports.
        Inspects which methods have real implementations.
        """
        caps: List[ConnectorCapability] = [ConnectorCapability.AUTHENTICATE]

        # read is always abstract → must be implemented
        caps.append(ConnectorCapability.READ)

        for cap, method_name in [
            (ConnectorCapability.WRITE, "write"),
            (ConnectorCapability.OBSERVE, "observe"),
            (ConnectorCapability.EXECUTE, "execute"),
        ]:
            method = getattr(type(self), method_name, None)
            base_method = getattr(UniversalConnector, method_name, None)
            if method is not None and method is not base_method:
                caps.append(cap)

        return caps

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def info(self) -> Dict[str, Any]:
        """Return a JSON-serialisable summary of this connector."""
        return {
            "name": self.spec.name,
            "version": self.spec.version,
            "status": self._status.value,
            "capabilities": [c.value for c in self.declared_capabilities()],
            "permissions": list(self.spec.permissions),
            "healthy": self.healthcheck(),
            "last_error": self._last_error,
        }
