"""
Circuit Breaker - Resilient fallback between providers
Implements circuit breaker pattern for provider failure handling
"""
import time
import logging
from typing import Dict, Any, Optional
from enum import Enum
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Circuit tripped, requests fail fast
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker"""
    failure_threshold: int = 5          # Failures before opening
    success_threshold: int = 2          # Successes to close from half-open
    timeout_seconds: float = 60.0       # Time before trying again
    half_open_max_calls: int = 3        # Max calls in half-open state


@dataclass
class CircuitStats:
    """Statistics for a circuit"""
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: Optional[float] = None
    opened_at: Optional[float] = None
    total_requests: int = 0
    total_failures: int = 0
    total_successes: int = 0


class CircuitBreaker:
    """
    Circuit breaker for provider resilience.
    Tracks provider failures and automatically fails fast when threshold exceeded.
    """
    
    def __init__(self, config: Optional[CircuitBreakerConfig] = None):
        """
        Initialize circuit breaker.
        
        Args:
            config: Optional configuration, uses defaults if not provided
        """
        self.config = config or CircuitBreakerConfig()
        self.circuits: Dict[str, CircuitStats] = {}
    
    def get_circuit(self, provider: str) -> CircuitStats:
        """Get or create circuit for a provider"""
        if provider not in self.circuits:
            self.circuits[provider] = CircuitStats()
        return self.circuits[provider]
    
    def can_execute(self, provider: str) -> bool:
        """
        Check if a request can be executed for this provider.
        
        Args:
            provider: Provider name
            
        Returns:
            True if request can proceed, False if circuit is open
        """
        circuit = self.get_circuit(provider)
        now = time.time()
        
        if circuit.state == CircuitState.CLOSED:
            # Normal operation
            return True
        
        elif circuit.state == CircuitState.OPEN:
            # Check if timeout has elapsed
            if circuit.opened_at and (now - circuit.opened_at) >= self.config.timeout_seconds:
                # Try half-open
                circuit.state = CircuitState.HALF_OPEN
                circuit.success_count = 0
                logger.info(f"Circuit for {provider} moving to HALF_OPEN state")
                return True
            else:
                # Still open, fail fast
                logger.warning(f"Circuit for {provider} is OPEN, failing fast")
                return False
        
        elif circuit.state == CircuitState.HALF_OPEN:
            # Allow limited requests in half-open state
            if circuit.success_count + circuit.failure_count < self.config.half_open_max_calls:
                return True
            else:
                logger.warning(f"Circuit for {provider} HALF_OPEN limit reached")
                return False
        
        return False
    
    def record_success(self, provider: str) -> None:
        """
        Record a successful request.
        
        Args:
            provider: Provider name
        """
        circuit = self.get_circuit(provider)
        circuit.total_requests += 1
        circuit.total_successes += 1
        
        if circuit.state == CircuitState.CLOSED:
            # Reset failure count on success
            circuit.failure_count = 0
        
        elif circuit.state == CircuitState.HALF_OPEN:
            circuit.success_count += 1
            
            # Check if we can close the circuit
            if circuit.success_count >= self.config.success_threshold:
                circuit.state = CircuitState.CLOSED
                circuit.failure_count = 0
                circuit.success_count = 0
                logger.info(f"Circuit for {provider} CLOSED - service recovered")
        
        logger.debug(f"Success recorded for {provider} (state={circuit.state.value})")
    
    def record_failure(self, provider: str, error: Optional[Exception] = None) -> None:
        """
        Record a failed request.
        
        Args:
            provider: Provider name
            error: Optional exception that caused the failure
        """
        circuit = self.get_circuit(provider)
        circuit.total_requests += 1
        circuit.total_failures += 1
        circuit.last_failure_time = time.time()
        
        if circuit.state == CircuitState.CLOSED:
            circuit.failure_count += 1
            
            # Check if we should open the circuit
            if circuit.failure_count >= self.config.failure_threshold:
                circuit.state = CircuitState.OPEN
                circuit.opened_at = time.time()
                logger.warning(
                    f"Circuit for {provider} OPENED after {circuit.failure_count} failures"
                )
        
        elif circuit.state == CircuitState.HALF_OPEN:
            # Any failure in half-open reopens the circuit
            circuit.state = CircuitState.OPEN
            circuit.opened_at = time.time()
            circuit.failure_count = self.config.failure_threshold
            logger.warning(f"Circuit for {provider} reopened from HALF_OPEN")
        
        error_msg = f"{type(error).__name__}: {error}" if error else "unknown error"
        logger.debug(
            f"Failure recorded for {provider} (state={circuit.state.value}): {error_msg}"
        )
    
    def reset(self, provider: str) -> None:
        """
        Manually reset a circuit.
        
        Args:
            provider: Provider to reset
        """
        if provider in self.circuits:
            self.circuits[provider] = CircuitStats()
            logger.info(f"Circuit for {provider} manually reset")
    
    def reset_all(self) -> None:
        """Reset all circuits"""
        self.circuits = {}
        logger.info("All circuits reset")
    
    def get_state(self, provider: str) -> CircuitState:
        """Get current state of a circuit"""
        return self.get_circuit(provider).state
    
    def get_stats(self, provider: Optional[str] = None) -> Dict[str, Any]:
        """
        Get statistics for one or all circuits.
        
        Args:
            provider: Optional provider name, if None returns all stats
            
        Returns:
            Dict with statistics
        """
        if provider:
            circuit = self.get_circuit(provider)
            return {
                'provider': provider,
                'state': circuit.state.value,
                'failure_count': circuit.failure_count,
                'total_requests': circuit.total_requests,
                'total_failures': circuit.total_failures,
                'total_successes': circuit.total_successes,
                'success_rate': (
                    circuit.total_successes / circuit.total_requests * 100
                    if circuit.total_requests > 0 else 0.0
                )
            }
        else:
            return {
                provider: {
                    'state': circuit.state.value,
                    'failure_count': circuit.failure_count,
                    'total_requests': circuit.total_requests,
                    'success_rate': (
                        circuit.total_successes / circuit.total_requests * 100
                        if circuit.total_requests > 0 else 0.0
                    )
                }
                for provider, circuit in self.circuits.items()
            }
    
    def __repr__(self) -> str:
        open_circuits = sum(
            1 for c in self.circuits.values()
            if c.state == CircuitState.OPEN
        )
        return (
            f"CircuitBreaker(circuits={len(self.circuits)}, "
            f"open={open_circuits})"
        )
