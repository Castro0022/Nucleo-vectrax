"""
Vectrax UCE — Universal type definitions.

All enums and type constants used across the Universal Connection Engine.
"""
from __future__ import annotations

from enum import Enum, unique


@unique
class ConnectorCapability(str, Enum):
    """What a connector can do."""
    READ = "read"
    WRITE = "write"
    OBSERVE = "observe"
    AUTHENTICATE = "authenticate"
    EXECUTE = "execute"


@unique
class AuthMethod(str, Enum):
    """Supported authentication mechanisms."""
    NONE = "none"
    TOKEN = "token"
    OAUTH2 = "oauth2"
    API_KEY = "api_key"
    CERTIFICATE = "certificate"
    CUSTOM = "custom"


@unique
class ConnectorStatus(str, Enum):
    """Runtime status of a connector instance."""
    IDLE = "idle"
    CONNECTED = "connected"
    ERROR = "error"
    DEGRADED = "degraded"
    DISCONNECTED = "disconnected"


@unique
class Sensitivity(str, Enum):
    """Data sensitivity classification."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@unique
class Severity(str, Enum):
    """Event severity level."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@unique
class OperationType(str, Enum):
    """Types of operations a connector can perform."""
    AUTH = "auth"
    READ = "read"
    WRITE = "write"
    OBSERVE = "observe"
    DISCONNECT = "disconnect"
    HEALTHCHECK = "healthcheck"
