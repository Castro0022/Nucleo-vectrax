"""Intelligent routing and resilience for provider management"""
from .smart_router import SmartRouter
from .circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
)
from .capabilities import (
    Capability,
    ModelProfile,
    get_registry,
    get_available_models,
    models_with_capability,
    refresh_availability,
)
from .task_classifier import (
    TaskType,
    TaskClassification,
    classify_task,
)
from .model_router import (
    ModelRouter,
    RoutingDecision,
    get_model_router,
    route,
)

__all__ = [
    # Legacy
    "SmartRouter",
    # Circuit breaker
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitState",
    # Capability registry
    "Capability",
    "ModelProfile",
    "get_registry",
    "get_available_models",
    "models_with_capability",
    "refresh_availability",
    # Task classifier
    "TaskType",
    "TaskClassification",
    "classify_task",
    # Model router
    "ModelRouter",
    "RoutingDecision",
    "get_model_router",
    "route",
]
