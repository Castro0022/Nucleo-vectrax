"""
Vectrax UCE — Connector Contract schema.

A ConnectorContract is the strict, versioned declaration that every
connector must provide.  It extends ConnectorSpec with the full
metadata required by the UCE.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from connectors.core.types import AuthMethod, ConnectorCapability
from connectors.contracts.families import ConnectorFamily


@dataclass
class ConnectorContract:
    """
    Strict, versioned contract that every UCE connector must declare.

    Fields
    ------
    name            : unique identifier (e.g. "rest_api", "local_filesystem")
    family          : ConnectorFamily enum
    version         : semver string
    capabilities    : list of ConnectorCapability the connector supports
    required_permissions : permissions the connector needs from the host
    auth_method     : how the connector authenticates
    input_schema    : JSON-schema-like dict describing expected input
    output_schema   : JSON-schema-like dict describing output format
    risks           : human-readable list of known risks
    rate_limits     : e.g. {"requests_per_minute": 60}
    fallback_connector : name of another connector to try on failure
    logs_enabled    : whether operations are logged to connection_logs
    description     : human-readable description
    author          : who wrote this connector
    """

    # Identity
    name: str
    family: ConnectorFamily
    version: str = "0.1.0"

    # Capabilities & permissions
    capabilities: List[ConnectorCapability] = field(default_factory=lambda: [
        ConnectorCapability.READ,
        ConnectorCapability.AUTHENTICATE,
    ])
    required_permissions: List[str] = field(default_factory=lambda: ["read"])
    auth_method: AuthMethod = AuthMethod.NONE

    # Schemas
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)

    # Risk & limits
    risks: List[str] = field(default_factory=list)
    rate_limits: Dict[str, int] = field(default_factory=dict)
    fallback_connector: Optional[str] = None

    # Operational
    logs_enabled: bool = True
    description: str = ""
    author: str = "vectrax"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "family": self.family.value,
            "version": self.version,
            "capabilities": [c.value for c in self.capabilities],
            "required_permissions": self.required_permissions,
            "auth_method": self.auth_method.value,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "risks": self.risks,
            "rate_limits": self.rate_limits,
            "fallback_connector": self.fallback_connector,
            "logs_enabled": self.logs_enabled,
            "description": self.description,
            "author": self.author,
        }
