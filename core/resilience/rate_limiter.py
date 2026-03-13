"""
Rate Limiter - Protect resources from overload
Implements token bucket and sliding window algorithms
"""
import time
import asyncio
from typing import Optional, Dict
from dataclasses import dataclass
from threading import Lock
import logging

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting"""
    max_requests: int = 100      # Maximum requests
    window_seconds: float = 60.0 # Time window
    burst_size: Optional[int] = None  # Allow bursts (token bucket)


class RateLimiter:
    """
    Token bucket rate limiter.
    Allows controlled request rate with burst support.
    """
    
    def __init__(self, config: RateLimitConfig):
        """
        Initialize rate limiter.
        
        Args:
            config: RateLimitConfig instance
        """
        self.config = config
        self.tokens = float(config.max_requests)
        self.max_tokens = float(config.max_requests)
        self.refill_rate = config.max_requests / config.window_seconds
        self.last_refill = time.time()
        self._lock = Lock()
        
        # Burst size defaults to max_requests if not specified
        self.burst_size = config.burst_size or config.max_requests
    
    def _refill(self):
        """Refill tokens based on elapsed time"""
        now = time.time()
        elapsed = now - self.last_refill
        
        # Add tokens based on elapsed time
        new_tokens = elapsed * self.refill_rate
        self.tokens = min(self.max_tokens, self.tokens + new_tokens)
        self.last_refill = now
    
    def try_acquire(self, tokens: int = 1) -> bool:
        """
        Try to acquire tokens without blocking.
        
        Args:
            tokens: Number of tokens to acquire
            
        Returns:
            True if tokens acquired, False otherwise
        """
        with self._lock:
            self._refill()
            
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            
            return False
    
    async def acquire(self, tokens: int = 1, timeout: Optional[float] = None):
        """
        Acquire tokens, waiting if necessary.
        
        Args:
            tokens: Number of tokens to acquire
            timeout: Maximum time to wait (None = wait forever)
            
        Raises:
            TimeoutError: If timeout exceeded
        """
        start_time = time.time()
        
        while True:
            if self.try_acquire(tokens):
                return
            
            # Check timeout
            if timeout is not None:
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    raise TimeoutError(
                        f"Rate limit acquisition timed out after {timeout}s"
                    )
            
            # Wait before retry
            await asyncio.sleep(0.1)
    
    def available_tokens(self) -> float:
        """Get number of available tokens"""
        with self._lock:
            self._refill()
            return self.tokens
    
    def reset(self):
        """Reset rate limiter to full capacity"""
        with self._lock:
            self.tokens = self.max_tokens
            self.last_refill = time.time()


class SlidingWindowRateLimiter:
    """
    Sliding window rate limiter.
    Tracks requests in a sliding time window.
    """
    
    def __init__(self, config: RateLimitConfig):
        """
        Initialize sliding window rate limiter.
        
        Args:
            config: RateLimitConfig instance
        """
        self.config = config
        self.requests: list[float] = []
        self._lock = Lock()
    
    def _clean_old_requests(self):
        """Remove requests outside the time window"""
        now = time.time()
        cutoff = now - self.config.window_seconds
        
        # Remove old requests
        self.requests = [req_time for req_time in self.requests if req_time > cutoff]
    
    def try_acquire(self) -> bool:
        """
        Try to acquire slot without blocking.
        
        Returns:
            True if slot acquired, False otherwise
        """
        with self._lock:
            self._clean_old_requests()
            
            if len(self.requests) < self.config.max_requests:
                self.requests.append(time.time())
                return True
            
            return False
    
    async def acquire(self, timeout: Optional[float] = None):
        """
        Acquire slot, waiting if necessary.
        
        Args:
            timeout: Maximum time to wait
            
        Raises:
            TimeoutError: If timeout exceeded
        """
        start_time = time.time()
        
        while True:
            if self.try_acquire():
                return
            
            # Check timeout
            if timeout is not None:
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    raise TimeoutError(
                        f"Rate limit acquisition timed out after {timeout}s"
                    )
            
            # Wait before retry
            await asyncio.sleep(0.1)
    
    def current_count(self) -> int:
        """Get current request count in window"""
        with self._lock:
            self._clean_old_requests()
            return len(self.requests)
    
    def reset(self):
        """Reset rate limiter"""
        with self._lock:
            self.requests.clear()


class MultiTierRateLimiter:
    """
    Multi-tier rate limiter.
    Supports per-user, per-provider, and global limits.
    """
    
    def __init__(
        self,
        global_config: Optional[RateLimitConfig] = None,
        per_user_config: Optional[RateLimitConfig] = None,
        per_provider_config: Optional[RateLimitConfig] = None
    ):
        """
        Initialize multi-tier rate limiter.
        
        Args:
            global_config: Global rate limit
            per_user_config: Per-user rate limit
            per_provider_config: Per-provider rate limit
        """
        self.global_limiter = RateLimiter(global_config) if global_config else None
        self.per_user_config = per_user_config
        self.per_provider_config = per_provider_config
        
        self.user_limiters: Dict[str, RateLimiter] = {}
        self.provider_limiters: Dict[str, RateLimiter] = {}
        self._lock = Lock()
    
    def _get_user_limiter(self, user_id: str) -> Optional[RateLimiter]:
        """Get or create limiter for user"""
        if not self.per_user_config:
            return None
        
        with self._lock:
            if user_id not in self.user_limiters:
                self.user_limiters[user_id] = RateLimiter(self.per_user_config)
            return self.user_limiters[user_id]
    
    def _get_provider_limiter(self, provider: str) -> Optional[RateLimiter]:
        """Get or create limiter for provider"""
        if not self.per_provider_config:
            return None
        
        with self._lock:
            if provider not in self.provider_limiters:
                self.provider_limiters[provider] = RateLimiter(self.per_provider_config)
            return self.provider_limiters[provider]
    
    async def acquire(
        self,
        user_id: Optional[str] = None,
        provider: Optional[str] = None,
        timeout: Optional[float] = None
    ):
        """
        Acquire across all applicable limiters.
        
        Args:
            user_id: User identifier
            provider: Provider name
            timeout: Maximum time to wait
            
        Raises:
            TimeoutError: If any limiter times out
        """
        # Try to acquire from all applicable limiters
        limiters = []
        
        if self.global_limiter:
            limiters.append(("global", self.global_limiter))
        
        if user_id:
            user_limiter = self._get_user_limiter(user_id)
            if user_limiter:
                limiters.append((f"user:{user_id}", user_limiter))
        
        if provider:
            provider_limiter = self._get_provider_limiter(provider)
            if provider_limiter:
                limiters.append((f"provider:{provider}", provider_limiter))
        
        # Acquire from each limiter
        for name, limiter in limiters:
            try:
                await limiter.acquire(timeout=timeout)
            except TimeoutError as e:
                logger.warning(f"Rate limit exceeded for {name}")
                raise
    
    def reset_all(self):
        """Reset all limiters"""
        if self.global_limiter:
            self.global_limiter.reset()
        
        for limiter in self.user_limiters.values():
            limiter.reset()
        
        for limiter in self.provider_limiters.values():
            limiter.reset()
