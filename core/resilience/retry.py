"""
Retry Logic - Exponential backoff and resilience patterns
"""
import asyncio
import logging
import random
from typing import Optional, Callable, Any, Type
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class RetryStrategy(Enum):
    """Retry strategies"""
    EXPONENTIAL = "exponential"  # 2^n backoff
    LINEAR = "linear"            # n * interval
    FIXED = "fixed"              # constant interval


@dataclass
class RetryConfig:
    """Configuration for retry behavior"""
    max_attempts: int = 3
    initial_delay: float = 1.0      # seconds
    max_delay: float = 60.0         # seconds
    exponential_base: float = 2.0
    jitter: bool = True             # add randomness to prevent thundering herd
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL
    
    # Which exceptions to retry
    retryable_exceptions: tuple = (
        ConnectionError,
        TimeoutError,
        OSError,
    )
    
    # Which exceptions to never retry
    fatal_exceptions: tuple = (
        ValueError,
        TypeError,
        KeyError,
    )


class RetryExhausted(Exception):
    """Raised when all retry attempts are exhausted"""
    pass


async def retry_async(
    func: Callable,
    config: Optional[RetryConfig] = None,
    *args,
    **kwargs
) -> Any:
    """
    Retry an async function with exponential backoff.
    
    Args:
        func: Async function to retry
        config: RetryConfig instance
        *args: Arguments for func
        **kwargs: Keyword arguments for func
        
    Returns:
        Result from successful function call
        
    Raises:
        RetryExhausted: If all attempts fail
        Exception: If fatal exception occurs
    """
    if config is None:
        config = RetryConfig()
    
    last_exception = None
    
    for attempt in range(1, config.max_attempts + 1):
        try:
            result = await func(*args, **kwargs)
            
            if attempt > 1:
                logger.info(
                    f"Retry succeeded on attempt {attempt}/{config.max_attempts}"
                )
            
            return result
            
        except config.fatal_exceptions as e:
            # Don't retry fatal exceptions
            logger.error(f"Fatal exception encountered: {e}")
            raise
            
        except config.retryable_exceptions as e:
            last_exception = e
            
            if attempt >= config.max_attempts:
                logger.error(
                    f"All {config.max_attempts} retry attempts exhausted. "
                    f"Last error: {e}"
                )
                raise RetryExhausted(
                    f"Failed after {config.max_attempts} attempts: {e}"
                ) from e
            
            # Calculate delay
            delay = _calculate_delay(
                attempt=attempt,
                config=config
            )
            
            logger.warning(
                f"Attempt {attempt}/{config.max_attempts} failed: {e}. "
                f"Retrying in {delay:.2f}s..."
            )
            
            await asyncio.sleep(delay)
            
        except Exception as e:
            # Unknown exception - log and raise
            logger.error(f"Unexpected exception during retry: {e}")
            raise
    
    # Should not reach here
    raise RetryExhausted(
        f"Failed after {config.max_attempts} attempts: {last_exception}"
    )


def _calculate_delay(attempt: int, config: RetryConfig) -> float:
    """
    Calculate delay for next retry attempt.
    
    Args:
        attempt: Current attempt number (1-indexed)
        config: RetryConfig
        
    Returns:
        Delay in seconds
    """
    if config.strategy == RetryStrategy.EXPONENTIAL:
        # Exponential: initial_delay * (base ^ (attempt - 1))
        delay = config.initial_delay * (config.exponential_base ** (attempt - 1))
        
    elif config.strategy == RetryStrategy.LINEAR:
        # Linear: initial_delay * attempt
        delay = config.initial_delay * attempt
        
    else:  # FIXED
        # Fixed: constant delay
        delay = config.initial_delay
    
    # Cap at max_delay
    delay = min(delay, config.max_delay)
    
    # Add jitter to prevent thundering herd
    if config.jitter:
        # Random jitter: ±25% of delay
        jitter_range = delay * 0.25
        delay = delay + random.uniform(-jitter_range, jitter_range)
        delay = max(0, delay)  # ensure non-negative
    
    return delay


class RetryableOperation:
    """
    Context manager / decorator for retryable operations.
    
    Usage as decorator:
        @RetryableOperation(max_attempts=3)
        async def my_function():
            pass
    
    Usage as context manager:
        async with RetryableOperation(max_attempts=3) as retry:
            result = await retry.execute(my_function, arg1, arg2)
    """
    
    def __init__(self, config: Optional[RetryConfig] = None, **kwargs):
        """
        Initialize with config or kwargs.
        
        Args:
            config: RetryConfig instance
            **kwargs: Override config parameters
        """
        if config is None:
            config = RetryConfig(**kwargs)
        elif kwargs:
            # Override config with kwargs
            for key, value in kwargs.items():
                if hasattr(config, key):
                    setattr(config, key, value)
        
        self.config = config
    
    def __call__(self, func: Callable) -> Callable:
        """Decorator usage"""
        async def wrapper(*args, **kwargs):
            return await retry_async(func, self.config, *args, **kwargs)
        return wrapper
    
    async def __aenter__(self):
        """Context manager enter"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        return False  # Don't suppress exceptions
    
    async def execute(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with retry logic"""
        return await retry_async(func, self.config, *args, **kwargs)


# Convenience decorators for common retry patterns
def retry_on_connection_error(max_attempts: int = 3):
    """Retry decorator specifically for connection errors"""
    config = RetryConfig(
        max_attempts=max_attempts,
        retryable_exceptions=(ConnectionError, TimeoutError, OSError)
    )
    return RetryableOperation(config=config)


def retry_on_timeout(max_attempts: int = 3, timeout: float = 30.0):
    """Retry decorator for timeout errors"""
    config = RetryConfig(
        max_attempts=max_attempts,
        initial_delay=timeout / 10,  # shorter initial delay
        retryable_exceptions=(TimeoutError, asyncio.TimeoutError)
    )
    return RetryableOperation(config=config)
