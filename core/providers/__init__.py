"""LLM provider implementations."""
from .ollama_provider import OllamaProvider

# Cloud providers — lazy imports to avoid hard httpx dependency at package level.
try:
    from .openai_stub import OpenAIProvider
except ImportError:
    OpenAIProvider = None  # type: ignore[assignment,misc]

try:
    from .gemini_stub import GeminiProvider
except ImportError:
    GeminiProvider = None  # type: ignore[assignment,misc]

try:
    from .anthropic_stub import AnthropicProvider
except ImportError:
    AnthropicProvider = None  # type: ignore[assignment,misc]

__all__ = [
    "OllamaProvider",
    "OpenAIProvider",
    "GeminiProvider",
    "AnthropicProvider",
]
