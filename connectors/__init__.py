"""Vectrax Universal Connection Engine (UCE)."""
from connectors.base import ConnectorSpec, ConnectorAdapter
from connectors.registry import ConnectorRegistry, get_connector_registry
from connectors.core.interface import UniversalConnector
from connectors.core.types import (
    AuthMethod,
    ConnectorCapability,
    ConnectorStatus,
    Sensitivity,
    Severity,
)
from connectors.contracts.schema import ConnectorContract
from connectors.contracts.families import ConnectorFamily
from connectors.engine import ConnectionEngine, get_connection_engine

__all__ = [
    # Legacy
    "ConnectorSpec",
    "ConnectorAdapter",
    "ConnectorRegistry",
    "get_connector_registry",
    # UCE Core
    "UniversalConnector",
    "AuthMethod",
    "ConnectorCapability",
    "ConnectorStatus",
    "Sensitivity",
    "Severity",
    # Contracts
    "ConnectorContract",
    "ConnectorFamily",
    # Engine
    "ConnectionEngine",
    "get_connection_engine",
]
