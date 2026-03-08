"""
Base interface for all LLM providers.
Provides provider-agnostic abstraction layer.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, AsyncIterator
from dataclasses import dataclass
from enum import Enum


class ProviderType(Enum):
    """Supported provider types"""
    OLLAMA = "ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    LOCALAI = "localai"


@dataclass
class GenerateRequest:
    """Standardized request format"""
    prompt: str
    model: str
    system_prompt: Optional[str] = None
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    stop_sequences: Optional[list[str]] = None
    stream: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding None values"""
        return {
            k: v for k, v in self.__dict__.items() 
            if v is not None
        }


@dataclass
class GenerateResponse:
    """Standardized response format"""
    content: str
    model: str
    provider: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    latency_ms: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            k: v for k, v in self.__dict__.items()
            if v is not None
        }


class BaseLLMProvider(ABC):
    """
    Abstract base class for all LLM providers.
    All providers must implement this interface.
    """
    
    def __init__(
        self, 
        provider_type: ProviderType,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: int = 60,
        **kwargs
    ):
        self.provider_type = provider_type
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout = timeout
        self.config = kwargs
        
    @abstractmethod
    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        """
        Generate a response from the LLM.
        
        Args:
            request: Standardized generate request
            
        Returns:
            Standardized generate response
        """
        pass
    
    @abstractmethod
    async def stream(self, request: GenerateRequest) -> AsyncIterator[str]:
        """
        Stream a response from the LLM.
        
        Args:
            request: Standardized generate request
            
        Yields:
            Response tokens as they are generated
        """
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """
        Check if the provider is healthy and accessible.
        
        Returns:
            True if provider is healthy, False otherwise
        """
        pass
    
    @abstractmethod
    async def list_models(self) -> list[str]:
        """
        List available models from this provider.
        
        Returns:
            List of model names
        """
        pass
    
    def get_provider_name(self) -> str:
        """Get the provider type name"""
        return self.provider_type.value
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(type={self.provider_type.value}, endpoint={self.endpoint})"
