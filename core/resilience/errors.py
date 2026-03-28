"""
Error Handling Framework - Standardized error types and recovery
"""
from typing import Optional, Dict, Any
from enum import Enum


class ErrorSeverity(Enum):
    """Severity levels for errors"""
    LOW = "low"           # Minor issue, system continues normally
    MEDIUM = "medium"     # Degraded performance, some features unavailable
    HIGH = "high"         # Critical issue, requires immediate attention
    FATAL = "fatal"       # System cannot continue


class ErrorCategory(Enum):
    """Categories of errors"""
    PROVIDER = "provider"           # Provider-related errors
    NETWORK = "network"             # Network connectivity issues
    VALIDATION = "validation"       # Input/output validation
    RESOURCE = "resource"           # Resource limits (memory, disk, etc.)
    CONFIGURATION = "configuration" # Configuration errors
    INTERNAL = "internal"           # Internal system errors


class VectraxError(Exception):
    """
    Base exception for all Vectrax errors.
    Provides structured error information.
    """
    
    def __init__(
        self,
        message: str,
        category: ErrorCategory = ErrorCategory.INTERNAL,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        recoverable: bool = True,
        details: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None
    ):
        super().__init__(message)
        self.message = message
        self.category = category
        self.severity = severity
        self.recoverable = recoverable
        self.details = details or {}
        self.cause = cause
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert error to dictionary for serialization"""
        return {
            'error_type': self.__class__.__name__,
            'message': self.message,
            'category': self.category.value,
            'severity': self.severity.value,
            'recoverable': self.recoverable,
            'details': self.details,
            'cause': str(self.cause) if self.cause else None
        }
    
    def __str__(self) -> str:
        return f"[{self.severity.value.upper()}] {self.category.value}: {self.message}"


# Provider Errors
class ProviderError(VectraxError):
    """Base class for provider-related errors"""
    
    def __init__(self, message: str, provider: str, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.PROVIDER,
            **kwargs
        )
        self.details['provider'] = provider


class NotConfiguredError(ProviderError):
    """Raised when a provider is used without required configuration (missing API key, etc.)."""

    def __init__(self, provider: str, detail: str = "", **kwargs):
        msg = f"Provider '{provider}': {detail}" if detail else f"Provider '{provider}' is not configured"
        super().__init__(
            msg,
            provider=provider,
            severity=ErrorSeverity.HIGH,
            recoverable=False,
            **kwargs
        )


class ProviderUnavailableError(ProviderError):
    """Provider is not available or not responding"""
    
    def __init__(self, provider: str, **kwargs):
        super().__init__(
            f"Provider '{provider}' is unavailable",
            provider=provider,
            severity=ErrorSeverity.HIGH,
            recoverable=True,
            **kwargs
        )


class ProviderTimeoutError(ProviderError):
    """Provider request timed out"""
    
    def __init__(self, provider: str, timeout: float, **kwargs):
        super().__init__(
            f"Provider '{provider}' timed out after {timeout}s",
            provider=provider,
            severity=ErrorSeverity.MEDIUM,
            recoverable=True,
            **kwargs
        )
        self.details['timeout'] = timeout


class ProviderRateLimitError(ProviderError):
    """Provider rate limit exceeded"""
    
    def __init__(self, provider: str, retry_after: Optional[float] = None, **kwargs):
        super().__init__(
            f"Provider '{provider}' rate limit exceeded",
            provider=provider,
            severity=ErrorSeverity.LOW,
            recoverable=True,
            **kwargs
        )
        if retry_after:
            self.details['retry_after'] = retry_after


class ModelNotFoundError(ProviderError):
    """Requested model not found"""
    
    def __init__(self, provider: str, model: str, **kwargs):
        super().__init__(
            f"Model '{model}' not found in provider '{provider}'",
            provider=provider,
            severity=ErrorSeverity.MEDIUM,
            recoverable=False,
            **kwargs
        )
        self.details['model'] = model


# Network Errors
class NetworkError(VectraxError):
    """Base class for network-related errors"""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.NETWORK,
            **kwargs
        )


class ConnectionError(NetworkError):
    """Network connection failed"""
    
    def __init__(self, endpoint: str, **kwargs):
        super().__init__(
            f"Failed to connect to {endpoint}",
            severity=ErrorSeverity.HIGH,
            recoverable=True,
            **kwargs
        )
        self.details['endpoint'] = endpoint


# Validation Errors
class ValidationError(VectraxError):
    """Base class for validation errors"""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.VALIDATION,
            severity=ErrorSeverity.LOW,
            recoverable=False,
            **kwargs
        )


class InvalidInputError(ValidationError):
    """Invalid input provided"""
    
    def __init__(self, field: str, value: Any, reason: str, **kwargs):
        super().__init__(
            f"Invalid input for '{field}': {reason}",
            **kwargs
        )
        self.details['field'] = field
        self.details['value'] = str(value)
        self.details['reason'] = reason


class InvalidOutputError(ValidationError):
    """Invalid output received"""
    
    def __init__(self, reason: str, **kwargs):
        super().__init__(
            f"Invalid output: {reason}",
            **kwargs
        )
        self.details['reason'] = reason


# Resource Errors
class ResourceError(VectraxError):
    """Base class for resource-related errors"""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.RESOURCE,
            **kwargs
        )


class ResourceExhaustedError(ResourceError):
    """Resource limit exceeded"""
    
    def __init__(self, resource: str, limit: Any, **kwargs):
        super().__init__(
            f"Resource '{resource}' exhausted (limit: {limit})",
            severity=ErrorSeverity.HIGH,
            recoverable=True,
            **kwargs
        )
        self.details['resource'] = resource
        self.details['limit'] = str(limit)


class MemoryError(ResourceError):
    """Out of memory"""
    
    def __init__(self, required: int, available: int, **kwargs):
        super().__init__(
            f"Insufficient memory: required {required}MB, available {available}MB",
            severity=ErrorSeverity.HIGH,
            recoverable=False,
            **kwargs
        )
        self.details['required_mb'] = required
        self.details['available_mb'] = available


# Configuration Errors
class ConfigurationError(VectraxError):
    """Base class for configuration errors"""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.CONFIGURATION,
            severity=ErrorSeverity.FATAL,
            recoverable=False,
            **kwargs
        )


class InvalidConfigError(ConfigurationError):
    """Invalid configuration"""
    
    def __init__(self, key: str, reason: str, **kwargs):
        super().__init__(
            f"Invalid configuration for '{key}': {reason}",
            **kwargs
        )
        self.details['key'] = key
        self.details['reason'] = reason


class MissingConfigError(ConfigurationError):
    """Required configuration missing"""
    
    def __init__(self, key: str, **kwargs):
        super().__init__(
            f"Required configuration '{key}' is missing",
            **kwargs
        )
        self.details['key'] = key


# Internal Errors
class InternalError(VectraxError):
    """Base class for internal system errors"""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.INTERNAL,
            severity=ErrorSeverity.HIGH,
            **kwargs
        )


class StateError(InternalError):
    """Invalid system state"""
    
    def __init__(self, expected: str, actual: str, **kwargs):
        super().__init__(
            f"Invalid state: expected '{expected}', got '{actual}'",
            **kwargs
        )
        self.details['expected'] = expected
        self.details['actual'] = actual


class NotImplementedError(InternalError):
    """Feature not yet implemented"""
    
    def __init__(self, feature: str, **kwargs):
        super().__init__(
            f"Feature not implemented: {feature}",
            severity=ErrorSeverity.MEDIUM,
            **kwargs
        )
        self.details['feature'] = feature


def handle_exception(exc: Exception) -> VectraxError:
    """
    Convert standard exceptions to Vectrax errors.
    
    Args:
        exc: Exception to convert
        
    Returns:
        VectraxError instance
    """
    if isinstance(exc, VectraxError):
        return exc
    
    # Map common exceptions
    if isinstance(exc, ValueError):
        return ValidationError(str(exc), cause=exc)
    
    elif isinstance(exc, (ConnectionError, OSError)):
        return NetworkError(str(exc), cause=exc)
    
    elif isinstance(exc, TimeoutError):
        return ProviderTimeoutError("unknown", timeout=0.0, cause=exc)
    
    elif isinstance(exc, MemoryError):
        return ResourceError(str(exc), cause=exc)
    
    else:
        # Unknown exception - wrap as internal error
        return InternalError(
            f"Unexpected error: {type(exc).__name__}: {exc}",
            cause=exc
        )
