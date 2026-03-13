"""Vectrax UCE — Integration Core."""
from connectors.core.types import (
    AuthMethod,
    ConnectorCapability,
    ConnectorStatus,
    Sensitivity,
    Severity,
)
from connectors.core.errors import (
    UCEError,
    ConnectorConnectionError,
    AuthenticationError,
    PermissionDenied,
    RateLimitExceeded,
    NormalizationError,
    ContractViolation,
)
from connectors.core.interface import UniversalConnector

__all__ = [
    "AuthMethod",
    "ConnectorCapability",
    "ConnectorStatus",
    "Sensitivity",
    "Severity",
    "UCEError",
    "ConnectorConnectionError",
    "AuthenticationError",
    "PermissionDenied",
    "RateLimitExceeded",
    "NormalizationError",
    "ContractViolation",
    "UniversalConnector",
]
