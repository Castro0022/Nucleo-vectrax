#!/usr/bin/env python3
"""
Phase 6 Tests - Hardening & Resilience
Tests error handling, retry logic, rate limiting, and validation
"""
import pytest
import asyncio
import time
from core.resilience import (
    # Errors
    VectraxError,
    ProviderUnavailableError,
    InvalidInputError,
    ValidationError,
    # Retry
    RetryConfig,
    RetryStrategy,
    RetryExhausted,
    retry_async,
    RetryableOperation,
    # Rate Limiting
    RateLimitConfig,
    RateLimiter,
    SlidingWindowRateLimiter,
    # Validation
    validate_prompt,
    validate_temperature,
    validate_max_tokens,
    validate_model_name,
    ValidationRules
)


# --- Error Handling Tests ---

def test_vectrax_error_creation():
    """Test creating structured errors"""
    error = ProviderUnavailableError("ollama")
    
    assert error.message == "Provider 'ollama' is unavailable"
    assert error.details['provider'] == "ollama"
    assert error.recoverable == True
    
    # Test serialization
    error_dict = error.to_dict()
    assert error_dict['error_type'] == "ProviderUnavailableError"
    assert error_dict['category'] == "provider"
    
    print("✅ Structured errors work")


def test_error_hierarchy():
    """Test error inheritance"""
    error = InvalidInputError("prompt", "test", "too short")
    
    assert isinstance(error, ValidationError)
    assert isinstance(error, VectraxError)
    assert isinstance(error, Exception)
    
    print("✅ Error hierarchy works")


# --- Retry Logic Tests ---

@pytest.mark.asyncio
async def test_retry_success_on_second_attempt():
    """Test successful retry after initial failure"""
    call_count = 0
    
    async def flaky_function():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise ConnectionError("Temporary failure")
        return "success"
    
    config = RetryConfig(
        max_attempts=3,
        initial_delay=0.1,
        strategy=RetryStrategy.EXPONENTIAL
    )
    
    result = await retry_async(flaky_function, config)
    
    assert result == "success"
    assert call_count == 2
    
    print("✅ Retry succeeds on second attempt")


@pytest.mark.asyncio
async def test_retry_exhausted():
    """Test retry exhaustion"""
    async def always_fails():
        raise ConnectionError("Always fails")
    
    config = RetryConfig(max_attempts=3, initial_delay=0.05)
    
    with pytest.raises(RetryExhausted):
        await retry_async(always_fails, config)
    
    print("✅ Retry exhaustion works")


@pytest.mark.asyncio
async def test_retry_fatal_exception():
    """Test that fatal exceptions are not retried"""
    call_count = 0
    
    async def raises_fatal():
        nonlocal call_count
        call_count += 1
        raise ValueError("Fatal error")
    
    config = RetryConfig(max_attempts=3)
    
    with pytest.raises(ValueError):
        await retry_async(raises_fatal, config)
    
    # Should only call once, not retry
    assert call_count == 1
    
    print("✅ Fatal exceptions not retried")


@pytest.mark.asyncio
async def test_retry_decorator():
    """Test retry as decorator"""
    call_count = 0
    
    @RetryableOperation(max_attempts=3, initial_delay=0.05)
    async def decorated_function():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise ConnectionError("Fail once")
        return "success"
    
    result = await decorated_function()
    
    assert result == "success"
    assert call_count == 2
    
    print("✅ Retry decorator works")


# --- Rate Limiting Tests ---

@pytest.mark.asyncio
async def test_rate_limiter_basic():
    """Test basic rate limiting"""
    config = RateLimitConfig(max_requests=5, window_seconds=1.0)
    limiter = RateLimiter(config)
    
    # Should acquire 5 tokens successfully
    for i in range(5):
        assert limiter.try_acquire() == True
    
    # 6th should fail
    assert limiter.try_acquire() == False
    
    print("✅ Rate limiter works")


@pytest.mark.asyncio
async def test_rate_limiter_refill():
    """Test token refill over time"""
    config = RateLimitConfig(max_requests=2, window_seconds=0.5)
    limiter = RateLimiter(config)
    
    # Acquire all tokens
    assert limiter.try_acquire() == True
    assert limiter.try_acquire() == True
    assert limiter.try_acquire() == False
    
    # Wait for refill
    await asyncio.sleep(0.6)
    
    # Should be able to acquire again
    assert limiter.try_acquire() == True
    
    print("✅ Rate limiter refill works")


@pytest.mark.asyncio
async def test_sliding_window_rate_limiter():
    """Test sliding window rate limiter"""
    config = RateLimitConfig(max_requests=3, window_seconds=1.0)
    limiter = SlidingWindowRateLimiter(config)
    
    # Acquire 3 slots
    assert limiter.try_acquire() == True
    assert limiter.try_acquire() == True
    assert limiter.try_acquire() == True
    
    # 4th should fail
    assert limiter.try_acquire() == False
    
    # Check count
    assert limiter.current_count() == 3
    
    print("✅ Sliding window rate limiter works")


# --- Validation Tests ---

def test_validate_prompt_success():
    """Test valid prompt passes validation"""
    prompt = "Write a function to calculate fibonacci"
    result = validate_prompt(prompt)
    
    assert result == prompt
    
    print("✅ Valid prompt passes")


def test_validate_prompt_empty():
    """Test empty prompt fails validation"""
    with pytest.raises(InvalidInputError) as exc_info:
        validate_prompt("")
    
    assert "cannot be empty" in str(exc_info.value)
    
    print("✅ Empty prompt rejected")


def test_validate_prompt_too_long():
    """Test overly long prompt fails validation"""
    rules = ValidationRules(max_length=100)
    long_prompt = "a" * 1000
    
    with pytest.raises(InvalidInputError) as exc_info:
        validate_prompt(long_prompt, rules)
    
    assert "too long" in str(exc_info.value).lower()
    
    print("✅ Long prompt rejected")


def test_validate_prompt_sanitization():
    """Test prompt sanitization"""
    prompt = "Hello\x00World\x01"
    result = validate_prompt(prompt)
    
    assert "\x00" not in result
    assert "\x01" not in result
    
    print("✅ Prompt sanitization works")


def test_validate_temperature():
    """Test temperature validation"""
    # Valid
    assert validate_temperature(0.7) == 0.7
    assert validate_temperature(1.0) == 1.0
    
    # Invalid
    with pytest.raises(InvalidInputError):
        validate_temperature(-0.5)
    
    with pytest.raises(InvalidInputError):
        validate_temperature(3.0)
    
    print("✅ Temperature validation works")


def test_validate_max_tokens():
    """Test max_tokens validation"""
    # Valid
    assert validate_max_tokens(100) == 100
    
    # Invalid
    with pytest.raises(InvalidInputError):
        validate_max_tokens(0)
    
    with pytest.raises(InvalidInputError):
        validate_max_tokens(-10)
    
    print("✅ Max tokens validation works")


def test_validate_model_name():
    """Test model name validation"""
    # Valid
    assert validate_model_name("llama3.2:3b") == "llama3.2:3b"
    assert validate_model_name("gpt-4") == "gpt-4"
    
    # Invalid
    with pytest.raises(InvalidInputError):
        validate_model_name("")
    
    with pytest.raises(InvalidInputError):
        validate_model_name("model with spaces")
    
    with pytest.raises(InvalidInputError):
        validate_model_name("model/with/slashes")
    
    print("✅ Model name validation works")


# --- Integration Tests ---

@pytest.mark.asyncio
async def test_retry_with_rate_limiter():
    """Test retry logic with rate limiting"""
    config = RateLimitConfig(max_requests=2, window_seconds=1.0)
    limiter = RateLimiter(config)
    
    call_count = 0
    
    async def rate_limited_function():
        nonlocal call_count
        call_count += 1
        
        if not limiter.try_acquire():
            raise Exception("Rate limited")
        
        if call_count < 2:
            raise ConnectionError("Temporary failure")
        
        return "success"
    
    retry_config = RetryConfig(max_attempts=5, initial_delay=0.1)
    
    # This will fail first time, then succeed after rate limit refill
    with pytest.raises(RetryExhausted):
        # Rate limit will prevent retries
        await retry_async(rate_limited_function, retry_config)
    
    print("✅ Retry + rate limiting integration works")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
