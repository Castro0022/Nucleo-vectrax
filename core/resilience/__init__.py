"""
Resilience Module - Error handling, retry logic, rate limiting, validation
"""

from .errors import (
    VectraxError,
    ErrorSeverity,
    ErrorCategory,
    ProviderError,
    ProviderUnavailableError,
    ProviderTimeoutError,
    ProviderRateLimitError,
    ModelNotFoundError,
    NetworkError,
    ValidationError,
    InvalidInputError,
    InvalidOutputError,
    ResourceError,
    ResourceExhaustedError,
    ConfigurationError,
    InvalidConfigError,
    MissingConfigError,
    InternalError,
    StateError,
    handle_exception
)

from .retry import (
    RetryConfig,
    RetryStrategy,
    RetryExhausted,
    retry_async,
    RetryableOperation,
    retry_on_connection_error,
    retry_on_timeout
)

from .rate_limiter import (
    RateLimitConfig,
    RateLimiter,
    SlidingWindowRateLimiter,
    MultiTierRateLimiter
)

from .validation import (
    ValidationRules,
    DEFAULT_RULES,
    validate_prompt,
    validate_temperature,
    validate_max_tokens,
    validate_model_name,
    sanitize_output,
    check_resource_limits
)

__all__ = [
    # Errors
    'VectraxError',
    'ErrorSeverity',
    'ErrorCategory',
    'ProviderError',
    'ProviderUnavailableError',
    'ProviderTimeoutError',
    'ProviderRateLimitError',
    'ModelNotFoundError',
    'NetworkError',
    'ValidationError',
    'InvalidInputError',
    'InvalidOutputError',
    'ResourceError',
    'ResourceExhaustedError',
    'ConfigurationError',
    'InvalidConfigError',
    'MissingConfigError',
    'InternalError',
    'StateError',
    'handle_exception',
    
    # Retry
    'RetryConfig',
    'RetryStrategy',
    'RetryExhausted',
    'retry_async',
    'RetryableOperation',
    'retry_on_connection_error',
    'retry_on_timeout',
    
    # Rate Limiting
    'RateLimitConfig',
    'RateLimiter',
    'SlidingWindowRateLimiter',
    'MultiTierRateLimiter',
    
    # Validation
    'ValidationRules',
    'DEFAULT_RULES',
    'validate_prompt',
    'validate_temperature',
    'validate_max_tokens',
    'validate_model_name',
    'sanitize_output',
    'check_resource_limits',
]
