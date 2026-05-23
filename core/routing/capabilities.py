"""
Vectrax Capability Registry
=============================
Maps provider/model pairs to capability profiles including:
capabilities, cost, latency, context limits, and modality support.

Ollama models are fully populated as local-first.
Cloud stubs (OpenAI, Gemini, Anthropic) activate when env vars are set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Set


# ---------------------------------------------------------------------------
# Capability enum
# ---------------------------------------------------------------------------

class Capability(str, Enum):
    REASONING = "reasoning"
    CODING = "coding"
    SUMMARIZATION = "summarization"
    TRANSLATION = "translation"
    VISION = "vision"
    STRUCTURED_EXTRACTION = "structured_extraction"
    LONG_CONTEXT = "long_context"
    TOOL_USE = "tool_use"
    AGENTIC = "agentic"               # autonomous agent workflows
    SAFETY = "safety"                  # safety-critical reasoning
    PLANNING = "planning"              # step-by-step planning


# ---------------------------------------------------------------------------
# Model profile
# ---------------------------------------------------------------------------

@dataclass
class ModelProfile:
    """Describes a provider/model's capabilities and limits."""
    provider: str
    model: str
    capabilities: Set[Capability]
    cost_per_1k_tokens: float          # USD; 0.0 for local
    avg_latency_ms: float              # typical latency
    max_context_tokens: int            # context window size
    modalities: Set[str] = field(default_factory=lambda: {"text"})
    priority: int = 50                 # lower = higher priority
    is_local: bool = False
    available: bool = True             # flipped at runtime based on env/health

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"


# ---------------------------------------------------------------------------
# Built-in registry
# ---------------------------------------------------------------------------

def _build_default_registry() -> Dict[str, ModelProfile]:
    """Build the default capability registry with known models."""
    registry: Dict[str, ModelProfile] = {}

    # -- Ollama local models ------------------------------------------------

    _ollama_models = [
        ModelProfile(
            provider="ollama",
            model="llama3.2:3b",
            capabilities={
                Capability.REASONING,
                Capability.SUMMARIZATION,
                Capability.TRANSLATION,
                Capability.CODING,
            },
            cost_per_1k_tokens=0.0,
            avg_latency_ms=800,
            max_context_tokens=8192,
            modalities={"text"},
            priority=10,
            is_local=True,
        ),
        ModelProfile(
            provider="ollama",
            model="qwen2.5-coder:7b",
            capabilities={
                Capability.CODING,
                Capability.REASONING,
                Capability.STRUCTURED_EXTRACTION,
                Capability.TOOL_USE,
            },
            cost_per_1k_tokens=0.0,
            avg_latency_ms=1200,
            max_context_tokens=32768,
            modalities={"text"},
            priority=10,
            is_local=True,
        ),
        ModelProfile(
            provider="ollama",
            model="llama3.1:8b",
            capabilities={
                Capability.REASONING,
                Capability.CODING,
                Capability.SUMMARIZATION,
                Capability.TRANSLATION,
                Capability.LONG_CONTEXT,
            },
            cost_per_1k_tokens=0.0,
            avg_latency_ms=1500,
            max_context_tokens=131072,
            modalities={"text"},
            priority=15,
            is_local=True,
        ),
        ModelProfile(
            provider="ollama",
            model="llava:7b",
            capabilities={
                Capability.VISION,
                Capability.REASONING,
                Capability.SUMMARIZATION,
            },
            cost_per_1k_tokens=0.0,
            avg_latency_ms=2000,
            max_context_tokens=4096,
            modalities={"text", "image"},
            priority=20,
            is_local=True,
        ),
    ]

    for m in _ollama_models:
        registry[m.key] = m

    # -- Cloud stubs (activate when env var present) -------------------------

    openai_available = bool(os.environ.get("OPENAI_API_KEY"))
    gemini_available = bool(os.environ.get("GEMINI_API_KEY"))
    anthropic_available = bool(os.environ.get("ANTHROPIC_API_KEY"))

    _cloud_models = [
        # OpenAI — fortaleza: código, herramientas, agentes
        ModelProfile(
            provider="openai",
            model="gpt-4o",
            capabilities={
                Capability.CODING, Capability.TOOL_USE, Capability.AGENTIC,
                Capability.REASONING, Capability.SUMMARIZATION,
                Capability.TRANSLATION, Capability.VISION,
                Capability.STRUCTURED_EXTRACTION, Capability.LONG_CONTEXT,
            },
            cost_per_1k_tokens=0.005,
            avg_latency_ms=2000,
            max_context_tokens=128000,
            modalities={"text", "image"},
            priority=30,
            available=openai_available,
        ),
        ModelProfile(
            provider="openai",
            model="gpt-4o-mini",
            capabilities={
                Capability.CODING, Capability.TOOL_USE, Capability.AGENTIC,
                Capability.REASONING, Capability.SUMMARIZATION,
                Capability.TRANSLATION, Capability.STRUCTURED_EXTRACTION,
            },
            cost_per_1k_tokens=0.00015,
            avg_latency_ms=1000,
            max_context_tokens=128000,
            modalities={"text"},
            priority=25,
            available=openai_available,
        ),
        # Gemini — fortaleza: análisis largo, contexto grande, multimodal
        ModelProfile(
            provider="gemini",
            model="gemini-2.0-flash",
            capabilities={
                Capability.LONG_CONTEXT, Capability.VISION,
                Capability.SUMMARIZATION, Capability.REASONING,
                Capability.CODING, Capability.TRANSLATION,
                Capability.TOOL_USE,
            },
            cost_per_1k_tokens=0.0001,
            avg_latency_ms=1500,
            max_context_tokens=1048576,
            modalities={"text", "image"},
            priority=25,
            available=gemini_available,
        ),
        # Anthropic — fortaleza: razonamiento, seguridad, planificación
        ModelProfile(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            capabilities={
                Capability.REASONING, Capability.SAFETY, Capability.PLANNING,
                Capability.CODING, Capability.SUMMARIZATION,
                Capability.TRANSLATION, Capability.STRUCTURED_EXTRACTION,
                Capability.LONG_CONTEXT, Capability.TOOL_USE,
            },
            cost_per_1k_tokens=0.003,
            avg_latency_ms=2500,
            max_context_tokens=200000,
            modalities={"text"},
            priority=30,
            available=anthropic_available,
        ),
    ]

    for m in _cloud_models:
        registry[m.key] = m

    return registry


# Module-level singleton — rebuilt on import
CAPABILITY_REGISTRY: Dict[str, ModelProfile] = _build_default_registry()


def get_registry() -> Dict[str, ModelProfile]:
    """Return the global capability registry."""
    return CAPABILITY_REGISTRY


def get_available_models(local_only: bool = False) -> Dict[str, ModelProfile]:
    """Return only models marked as available."""
    return {
        k: v for k, v in CAPABILITY_REGISTRY.items()
        if v.available and (not local_only or v.is_local)
    }


def models_with_capability(cap: Capability, local_only: bool = False) -> Dict[str, ModelProfile]:
    """Return available models that have a given capability."""
    return {
        k: v for k, v in get_available_models(local_only=local_only).items()
        if cap in v.capabilities
    }


def refresh_availability() -> None:
    """Re-check env vars and connector registry to update availability.

    Cloud models: available if their API key env var is set.
    Local models (e.g. Ollama): available only if the provider passed
    the health check and is registered in the connector registry.
    This prevents the ModelRouter from selecting Ollama on servers
    where it isn't installed.
    """
    env_map = {
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }

    # Check which local providers are actually registered (passed health check)
    _registered_local: set = set()
    try:
        from core.intelligence.connector_registry import get_connector_registry
        reg = get_connector_registry()
        for desc in reg.list_descriptors():
            if desc.is_local and desc.healthy:
                _registered_local.add(desc.name)
    except Exception:
        pass

    for profile in CAPABILITY_REGISTRY.values():
        if profile.is_local:
            # Local models: only available if provider passed health check
            profile.available = profile.provider in _registered_local
        else:
            env_var = env_map.get(profile.provider)
            if env_var:
                profile.available = bool(os.environ.get(env_var))
