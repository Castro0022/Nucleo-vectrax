"""
Vectrax Intelligence Connector Registry
=========================================
Registers LLM provider adapters as external modules.
Auto-detects available providers from environment variables and health checks.

The core routing logic is immutable — this module only manages the
provider instances that the IntelligenceRouter dispatches to.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.abstraction.base import BaseLLMProvider, ProviderType

logger = logging.getLogger("vectrax.intelligence.connector_registry")


# ---------------------------------------------------------------------------
# Provider descriptor
# ---------------------------------------------------------------------------

@dataclass
class ProviderDescriptor:
    """Metadata about a registered provider adapter."""
    name: str
    provider_type: ProviderType
    instance: BaseLLMProvider
    env_var: Optional[str] = None       # env var that activates this provider
    is_local: bool = False
    healthy: bool = False
    auto_detected: bool = False
    models: List[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class IntelligenceConnectorRegistry:
    """
    Manages the lifecycle of LLM provider adapters.

    - Supports manual registration via ``register()``
    - Supports auto-detection via ``auto_detect()``
    - Provides lookup by name or provider type
    - Health-checks all registered providers

    Usage::

        registry = IntelligenceConnectorRegistry()
        await registry.auto_detect()
        provider = registry.get("ollama")
    """

    def __init__(self) -> None:
        self._providers: Dict[str, ProviderDescriptor] = {}

    # -- registration -------------------------------------------------------

    def register(
        self,
        name: str,
        instance: BaseLLMProvider,
        provider_type: ProviderType,
        *,
        env_var: Optional[str] = None,
        is_local: bool = False,
    ) -> ProviderDescriptor:
        """Register a provider adapter manually."""
        desc = ProviderDescriptor(
            name=name,
            provider_type=provider_type,
            instance=instance,
            env_var=env_var,
            is_local=is_local,
        )
        self._providers[name] = desc
        logger.info("Registered provider: %s (type=%s)", name, provider_type.value)
        return desc

    def unregister(self, name: str) -> bool:
        if name in self._providers:
            del self._providers[name]
            logger.info("Unregistered provider: %s", name)
            return True
        return False

    # -- auto-detection -----------------------------------------------------

    async def auto_detect(self) -> List[str]:
        """
        Auto-detect available providers from env vars and health checks.

        Returns list of provider names that were successfully detected.
        """
        detected: List[str] = []

        # Ollama (local-first)
        if "ollama" not in self._providers:
            try:
                from core.providers.ollama_provider import OllamaProvider
                provider = OllamaProvider()
                healthy = await provider.health_check()
                if healthy:
                    desc = self.register(
                        "ollama", provider, ProviderType.OLLAMA, is_local=True,
                    )
                    desc.healthy = True
                    desc.auto_detected = True
                    try:
                        desc.models = await provider.list_models()
                    except Exception:
                        desc.models = []
                    detected.append("ollama")
                    logger.info("Auto-detected Ollama with %d models", len(desc.models))
                else:
                    await provider.close()
            except Exception as exc:
                logger.debug("Ollama auto-detect failed: %s", exc)

        # OpenAI
        if "openai" not in self._providers and os.environ.get("OPENAI_API_KEY"):
            try:
                from core.providers.openai_stub import OpenAIProvider
                provider = OpenAIProvider()
                desc = self.register(
                    "openai", provider, ProviderType.OPENAI,
                    env_var="OPENAI_API_KEY",
                )
                desc.healthy = True  # assume healthy if key present
                desc.auto_detected = True
                detected.append("openai")
                logger.info("Auto-detected OpenAI (API key present)")
            except Exception as exc:
                logger.debug("OpenAI auto-detect failed: %s", exc)

        # Gemini
        if "gemini" not in self._providers and os.environ.get("GEMINI_API_KEY"):
            try:
                from core.providers.gemini_stub import GeminiProvider
                provider = GeminiProvider()
                desc = self.register(
                    "gemini", provider, ProviderType.GEMINI,
                    env_var="GEMINI_API_KEY",
                )
                desc.healthy = True
                desc.auto_detected = True
                detected.append("gemini")
                logger.info("Auto-detected Gemini (API key present)")
            except Exception as exc:
                logger.debug("Gemini auto-detect failed: %s", exc)

        # Anthropic
        if "anthropic" not in self._providers and os.environ.get("ANTHROPIC_API_KEY"):
            try:
                from core.providers.anthropic_stub import AnthropicProvider
                provider = AnthropicProvider()
                desc = self.register(
                    "anthropic", provider, ProviderType.ANTHROPIC,
                    env_var="ANTHROPIC_API_KEY",
                )
                desc.healthy = True
                desc.auto_detected = True
                detected.append("anthropic")
                logger.info("Auto-detected Anthropic (API key present)")
            except Exception as exc:
                logger.debug("Anthropic auto-detect failed: %s", exc)

        return detected

    # -- lookup -------------------------------------------------------------

    def get(self, name: str) -> Optional[BaseLLMProvider]:
        """Get a provider instance by name."""
        desc = self._providers.get(name)
        return desc.instance if desc else None

    def get_descriptor(self, name: str) -> Optional[ProviderDescriptor]:
        return self._providers.get(name)

    def get_by_type(self, ptype: ProviderType) -> Optional[BaseLLMProvider]:
        """Get first provider matching the given type."""
        for desc in self._providers.values():
            if desc.provider_type == ptype:
                return desc.instance
        return None

    def list_providers(self, healthy_only: bool = False) -> List[str]:
        if healthy_only:
            return [n for n, d in self._providers.items() if d.healthy]
        return list(self._providers.keys())

    def list_descriptors(self) -> List[ProviderDescriptor]:
        return list(self._providers.values())

    # -- health check -------------------------------------------------------

    async def health_check_all(self) -> Dict[str, bool]:
        """Run health checks on all registered providers."""
        results: Dict[str, bool] = {}
        for name, desc in self._providers.items():
            try:
                desc.healthy = await desc.instance.health_check()
            except Exception:
                desc.healthy = False
            results[name] = desc.healthy
        return results

    # -- cleanup ------------------------------------------------------------

    async def close_all(self) -> None:
        """Close all provider clients."""
        for desc in self._providers.values():
            try:
                await desc.instance.close()
            except Exception:
                pass
        self._providers.clear()

    # -- status -------------------------------------------------------------

    def status(self) -> Dict:
        return {
            "total": len(self._providers),
            "healthy": sum(1 for d in self._providers.values() if d.healthy),
            "local": sum(1 for d in self._providers.values() if d.is_local),
            "providers": {
                n: {
                    "type": d.provider_type.value,
                    "healthy": d.healthy,
                    "is_local": d.is_local,
                    "auto_detected": d.auto_detected,
                    "models_count": len(d.models),
                }
                for n, d in self._providers.items()
            },
        }

    def __repr__(self) -> str:
        healthy = sum(1 for d in self._providers.values() if d.healthy)
        return f"IntelligenceConnectorRegistry(providers={len(self._providers)}, healthy={healthy})"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: Optional[IntelligenceConnectorRegistry] = None


def get_connector_registry() -> IntelligenceConnectorRegistry:
    global _registry
    if _registry is None:
        _registry = IntelligenceConnectorRegistry()
    return _registry
