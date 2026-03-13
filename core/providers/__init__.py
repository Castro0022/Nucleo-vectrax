"""LLM provider implementations"""
from .ollama_provider import OllamaProvider

# Optional cloud providers — import directly from submodules to avoid
# circular imports. Use:  from core.providers.openai_stub import OpenAIProvider
# These are NOT imported at package level on purpose.

__all__ = [
    "OllamaProvider",
]
