"""
Vectrax UCE — Exception hierarchy.

All connector-related errors derive from UCEError so callers can
catch the entire family with a single except clause.
"""
from __future__ import annotations


class UCEError(Exception):
    """Base exception for the Universal Connection Engine."""


class ConnectorConnectionError(UCEError):
    """Failed to establish or maintain a connection."""


class AuthenticationError(UCEError):
    """Authentication handshake failed."""


class PermissionDenied(UCEError):
    """Operation not allowed by the active permission policy."""


class RateLimitExceeded(UCEError):
    """Connector rate limit reached."""


class NormalizationError(UCEError):
    """Failed to normalize external data to internal format."""


class ContractViolation(UCEError):
    """A connector contract is invalid or incomplete."""


class ApprovalRequired(UCEError):
    """Operation requires creator approval before proceeding."""
