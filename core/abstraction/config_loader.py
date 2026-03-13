"""
Configuration Loader - Build ProviderRegistry from YAML config
"""
import yaml
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional

from .registry import ProviderRegistry, ProviderConfig
from .base import ProviderType
from ..providers.ollama_provider import OllamaProvider

logger = logging.getLogger(__name__)


class ConfigLoader:
    """Loads configuration and builds ProviderRegistry"""
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize config loader.
        
        Args:
            config_path: Path to config.yaml, or None to use default
        """
        if config_path is None:
            # Default to config/config.yaml relative to project root
            project_root = Path(__file__).parent.parent.parent
            config_path = project_root / "config" / "config.yaml"
        
        self.config_path = Path(config_path)
        self.config_data: Optional[Dict[str, Any]] = None
        
    def load_config(self) -> Dict[str, Any]:
        """
        Load configuration from YAML file.
        
        Returns:
            Configuration dictionary
        """
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        
        with open(self.config_path, 'r') as f:
            self.config_data = yaml.safe_load(f)
        
        logger.info(f"Loaded configuration from {self.config_path}")
        return self.config_data
    
    def build_registry(self) -> ProviderRegistry:
        """
        Build a ProviderRegistry from loaded configuration.
        
        Returns:
            Configured ProviderRegistry instance
        """
        if self.config_data is None:
            self.load_config()
        
        registry = ProviderRegistry()
        
        # Get providers config
        providers_config = self.config_data.get('providers', {})
        
        for provider_name, provider_data in providers_config.items():
            try:
                self._register_provider(registry, provider_name, provider_data)
            except Exception as e:
                logger.error(f"Failed to register provider {provider_name}: {e}")
        
        # Set default provider from routing config
        routing_config = self.config_data.get('routing', {})
        default_provider = routing_config.get('default_provider')
        if default_provider:
            try:
                registry.set_default_provider(default_provider)
            except Exception as e:
                logger.warning(f"Could not set default provider to {default_provider}: {e}")
        
        logger.info(f"Built registry: {registry}")
        return registry
    
    def _register_provider(
        self,
        registry: ProviderRegistry,
        name: str,
        config: Dict[str, Any]
    ) -> None:
        """
        Register a single provider with the registry.
        
        Args:
            registry: Registry to register with
            name: Provider name
            config: Provider configuration dict
        """
        provider_type = config.get('type')
        enabled = config.get('enabled', True)
        priority = config.get('priority', 99)
        endpoint = config.get('endpoint')
        models = config.get('models', {})
        timeout = config.get('timeout', 60)
        
        # Get API key from environment if not in config
        api_key = config.get('api_key')
        if api_key is None:
            # Try environment variable
            env_var = f"{name.upper()}_API_KEY"
            api_key = os.environ.get(env_var)
        
        # Create ProviderConfig
        provider_config = ProviderConfig(
            name=name,
            provider_type=provider_type,
            enabled=enabled,
            priority=priority,
            endpoint=endpoint,
            api_key=api_key,
            models=models,
            timeout=timeout,
            config=config
        )
        
        # Instantiate provider based on type
        provider = self._create_provider(provider_type, provider_config)
        
        # Register
        registry.register(name, provider, provider_config)
    
    def _create_provider(
        self,
        provider_type: str,
        config: ProviderConfig
    ) -> 'BaseLLMProvider':
        """
        Factory method to create provider instances.
        
        Args:
            provider_type: Type of provider (ollama, openai, etc.)
            config: Provider configuration
            
        Returns:
            Provider instance
        """
        if provider_type == "ollama":
            return OllamaProvider(
                endpoint=config.endpoint or "http://localhost:11434",
                timeout=config.timeout
            )
        
        elif provider_type == "openai":
            # Lazy import to avoid dependency if not needed
            try:
                from ..providers.openai_provider import OpenAIProvider
                return OpenAIProvider(
                    api_key=config.api_key,
                    timeout=config.timeout
                )
            except ImportError:
                logger.error("OpenAI provider requested but not available")
                raise
        
        elif provider_type == "anthropic":
            # Lazy import
            try:
                from ..providers.anthropic_provider import AnthropicProvider
                return AnthropicProvider(
                    api_key=config.api_key,
                    timeout=config.timeout
                )
            except ImportError:
                logger.error("Anthropic provider requested but not available")
                raise
        
        else:
            raise ValueError(f"Unknown provider type: {provider_type}")
    
    def get_system_config(self) -> Dict[str, Any]:
        """Get system-level configuration"""
        if self.config_data is None:
            self.load_config()
        return self.config_data.get('system', {})
    
    def get_routing_config(self) -> Dict[str, Any]:
        """Get routing configuration"""
        if self.config_data is None:
            self.load_config()
        return self.config_data.get('routing', {})
    
    def get_storage_config(self) -> Dict[str, Any]:
        """Get storage configuration"""
        if self.config_data is None:
            self.load_config()
        return self.config_data.get('storage', {})


# Convenience function
def load_registry_from_config(config_path: Optional[str] = None) -> ProviderRegistry:
    """
    Load and build a ProviderRegistry from configuration file.
    
    Args:
        config_path: Path to config.yaml, or None for default
        
    Returns:
        Configured ProviderRegistry
    """
    loader = ConfigLoader(config_path)
    return loader.build_registry()
