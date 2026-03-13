"""
Provider Registry - Centralized management of LLM providers
Enables hot-swapping between providers without code changes
"""
import logging
from typing import Dict, Optional, List
from dataclasses import dataclass

from .base import BaseLLMProvider, GenerateRequest, GenerateResponse

logger = logging.getLogger(__name__)


@dataclass
class ProviderConfig:
    """Configuration for a provider"""
    name: str
    provider_type: str
    enabled: bool
    priority: int
    endpoint: Optional[str] = None
    api_key: Optional[str] = None
    models: Optional[Dict[str, str]] = None
    timeout: int = 60
    config: Optional[Dict] = None


class ProviderRegistry:
    """
    Central registry for managing multiple LLM providers.
    Supports hot-swapping, fallbacks, and health-based routing.
    """
    
    def __init__(self):
        self._providers: Dict[str, BaseLLMProvider] = {}
        self._configs: Dict[str, ProviderConfig] = {}
        self._default_provider: Optional[str] = None
        
    def register(
        self, 
        name: str, 
        provider: BaseLLMProvider,
        config: ProviderConfig
    ) -> None:
        """
        Register a provider with the registry.
        
        Args:
            name: Unique name for this provider
            provider: Provider instance
            config: Provider configuration
        """
        if name in self._providers:
            logger.warning(f"Provider '{name}' already registered, replacing...")
        
        self._providers[name] = provider
        self._configs[name] = config
        
        logger.info(f"Registered provider: {name} (type={config.provider_type}, enabled={config.enabled})")
        
        # Set as default if it's the first enabled provider or has priority 1
        if self._default_provider is None and config.enabled:
            self._default_provider = name
            logger.info(f"Set default provider: {name}")
    
    def unregister(self, name: str) -> None:
        """Remove a provider from the registry"""
        if name in self._providers:
            del self._providers[name]
            del self._configs[name]
            logger.info(f"Unregistered provider: {name}")
            
            # Update default if needed
            if self._default_provider == name:
                self._default_provider = self._get_highest_priority_provider()
    
    def set_default_provider(self, name: str) -> None:
        """Set the default provider"""
        if name not in self._providers:
            raise ValueError(f"Provider '{name}' not registered")
        if not self._configs[name].enabled:
            raise ValueError(f"Provider '{name}' is disabled")
        
        self._default_provider = name
        logger.info(f"Default provider set to: {name}")
    
    def get_provider(self, name: Optional[str] = None) -> BaseLLMProvider:
        """
        Get a provider by name, or the default provider.
        
        Args:
            name: Provider name, or None for default
            
        Returns:
            Provider instance
            
        Raises:
            ValueError: If provider not found or disabled
        """
        if name is None:
            if self._default_provider is None:
                raise ValueError("No default provider set and no provider name given")
            name = self._default_provider
        
        if name not in self._providers:
            raise ValueError(f"Provider '{name}' not registered")
        
        config = self._configs[name]
        if not config.enabled:
            raise ValueError(f"Provider '{name}' is disabled")
        
        return self._providers[name]
    
    def get_config(self, name: str) -> ProviderConfig:
        """Get provider configuration"""
        if name not in self._configs:
            raise ValueError(f"Provider '{name}' not registered")
        return self._configs[name]
    
    def list_providers(self, enabled_only: bool = False) -> List[str]:
        """
        List all registered providers.
        
        Args:
            enabled_only: If True, only return enabled providers
            
        Returns:
            List of provider names
        """
        if enabled_only:
            return [
                name for name, config in self._configs.items()
                if config.enabled
            ]
        return list(self._providers.keys())
    
    def _get_highest_priority_provider(self) -> Optional[str]:
        """Get the enabled provider with highest priority (lowest number)"""
        enabled_providers = [
            (name, config) for name, config in self._configs.items()
            if config.enabled
        ]
        
        if not enabled_providers:
            return None
        
        # Sort by priority (ascending)
        enabled_providers.sort(key=lambda x: x[1].priority)
        return enabled_providers[0][0]
    
    async def generate(
        self,
        prompt: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> GenerateResponse:
        """
        Generate using the registry (convenience method).
        
        Args:
            prompt: Text prompt
            provider: Provider name (optional, uses default)
            model: Model name (optional, uses config default)
            **kwargs: Additional generation parameters
            
        Returns:
            Standardized response
        """
        # Get provider
        provider_instance = self.get_provider(provider)
        provider_name = provider or self._default_provider
        
        # Resolve model name
        if model is None:
            config = self._configs[provider_name]
            if config.models and "default" in config.models:
                model = config.models["default"]
            else:
                raise ValueError(f"No model specified and no default model in config")
        
        # Create request
        request = GenerateRequest(
            prompt=prompt,
            model=model,
            **kwargs
        )
        
        # Generate
        return await provider_instance.generate(request)
    
    async def health_check_all(self) -> Dict[str, bool]:
        """
        Check health of all enabled providers.
        
        Returns:
            Dict mapping provider name to health status
        """
        results = {}
        
        for name in self.list_providers(enabled_only=True):
            try:
                provider = self._providers[name]
                is_healthy = await provider.health_check()
                results[name] = is_healthy
            except Exception as e:
                logger.error(f"Health check failed for {name}: {e}")
                results[name] = False
        
        return results
    
    def get_model_alias(self, provider_name: str, alias: str) -> Optional[str]:
        """
        Resolve a model alias to actual model name.
        
        Args:
            provider_name: Provider to look up
            alias: Model alias (e.g., "fast", "code")
            
        Returns:
            Actual model name, or None if not found
        """
        config = self._configs.get(provider_name)
        if not config or not config.models:
            return None
        
        return config.models.get(alias)
    
    def __repr__(self) -> str:
        enabled = len([c for c in self._configs.values() if c.enabled])
        total = len(self._providers)
        return f"ProviderRegistry(providers={total}, enabled={enabled}, default={self._default_provider})"
