"""
Tests para la capa consolidada de proveedores IA.

Cubre:
  1. BaseLLMProvider + ProviderType
  2. NotConfiguredError (ubicación compartida)
  3. OpenAI provider — instanciación, NotConfiguredError
  4. Gemini provider — instanciación, system_prompt payload, error handling
  5. Anthropic provider — instanciación, NotConfiguredError
  6. Ollama provider — instanciación
  7. Config loader factory — crea los 4 tipos
  8. Providers __init__ exports
  9. Connector registry — auto-detect structure
"""

import os
import pytest
from unittest.mock import patch, MagicMock


# ═══════════════════════════════════════════════════════════════════════════
# 1. Base abstraction
# ═══════════════════════════════════════════════════════════════════════════

from core.abstraction.base import (
    BaseLLMProvider,
    GenerateRequest,
    GenerateResponse,
    ProviderType,
)


class TestBaseAbstraction:
    """Tests para la capa de abstracción base."""

    def test_provider_type_enum(self):
        assert ProviderType.OLLAMA.value == "ollama"
        assert ProviderType.OPENAI.value == "openai"
        assert ProviderType.GEMINI.value == "gemini"
        assert ProviderType.ANTHROPIC.value == "anthropic"

    def test_provider_type_has_all_cloud(self):
        names = {pt.value for pt in ProviderType}
        assert {"ollama", "openai", "gemini", "anthropic"} <= names

    def test_generate_request_defaults(self):
        req = GenerateRequest(prompt="hello", model="test")
        assert req.temperature == 0.7
        assert req.max_tokens is None
        assert req.system_prompt is None

    def test_generate_request_to_dict(self):
        req = GenerateRequest(prompt="test", model="m1", system_prompt="sys")
        d = req.to_dict()
        assert d["prompt"] == "test"
        assert d["model"] == "m1"
        assert d["system_prompt"] == "sys"

    def test_generate_response_to_dict(self):
        resp = GenerateResponse(content="hi", model="m1", provider="test")
        d = resp.to_dict()
        assert d["content"] == "hi"
        assert d["provider"] == "test"


# ═══════════════════════════════════════════════════════════════════════════
# 2. NotConfiguredError
# ═══════════════════════════════════════════════════════════════════════════

from core.resilience.errors import NotConfiguredError, ProviderError


class TestNotConfiguredError:
    """NotConfiguredError debe estar en resilience/errors.py."""

    def test_is_subclass_of_provider_error(self):
        assert issubclass(NotConfiguredError, ProviderError)

    def test_raise_with_provider_name(self):
        with pytest.raises(NotConfiguredError) as exc_info:
            raise NotConfiguredError("openai", "API key missing")
        assert "openai" in str(exc_info.value)

    def test_backward_compat_import_from_openai_stub(self):
        """Old import path should still work."""
        from core.providers.openai_stub import NotConfiguredError as NCE
        assert NCE is NotConfiguredError


# ═══════════════════════════════════════════════════════════════════════════
# 3. OpenAI Provider
# ═══════════════════════════════════════════════════════════════════════════

from core.providers.openai_stub import OpenAIProvider


class TestOpenAIProvider:

    def test_instantiation_no_key(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = OpenAIProvider(api_key=None)
            assert provider.provider_type == ProviderType.OPENAI

    def test_instantiation_with_key(self):
        provider = OpenAIProvider(api_key="test-key")
        assert provider._api_key == "test-key"
        assert provider.get_provider_name() == "openai"

    def test_ensure_client_raises_without_key(self):
        provider = OpenAIProvider(api_key=None)
        provider._api_key = None
        with pytest.raises(NotConfiguredError):
            provider._ensure_client()

    def test_endpoint_default(self):
        provider = OpenAIProvider(api_key="k")
        assert "openai.com" in provider.endpoint

    def test_repr(self):
        provider = OpenAIProvider(api_key="k")
        r = repr(provider)
        assert "OpenAIProvider" in r


# ═══════════════════════════════════════════════════════════════════════════
# 4. Gemini Provider
# ═══════════════════════════════════════════════════════════════════════════

from core.providers.gemini_stub import GeminiProvider


class TestGeminiProvider:

    def test_instantiation(self):
        provider = GeminiProvider(api_key="test-key")
        assert provider.provider_type == ProviderType.GEMINI
        assert provider.get_provider_name() == "gemini"

    def test_ensure_client_raises_without_key(self):
        provider = GeminiProvider(api_key=None)
        provider._api_key = None
        with pytest.raises(NotConfiguredError):
            provider._ensure_client()

    def test_build_payload_basic(self):
        provider = GeminiProvider(api_key="k")
        req = GenerateRequest(prompt="hello", model="gemini-2.0-flash")
        payload = provider._build_payload(req)
        assert payload["contents"][0]["parts"][0]["text"] == "hello"
        assert "systemInstruction" not in payload

    def test_build_payload_with_system_prompt(self):
        provider = GeminiProvider(api_key="k")
        req = GenerateRequest(
            prompt="hello", model="gemini-2.0-flash",
            system_prompt="You are a helpful assistant",
        )
        payload = provider._build_payload(req)
        assert "systemInstruction" in payload
        assert payload["systemInstruction"]["parts"][0]["text"] == "You are a helpful assistant"

    def test_build_payload_with_max_tokens(self):
        provider = GeminiProvider(api_key="k")
        req = GenerateRequest(
            prompt="hello", model="gemini-2.0-flash", max_tokens=512,
        )
        payload = provider._build_payload(req)
        assert payload["generationConfig"]["maxOutputTokens"] == 512

    def test_extract_text_valid(self):
        data = {
            "candidates": [{"content": {"parts": [{"text": "response text"}]}}]
        }
        assert GeminiProvider._extract_text(data) == "response text"

    def test_extract_text_empty(self):
        assert GeminiProvider._extract_text({}) == ""
        assert GeminiProvider._extract_text({"candidates": []}) == ""

    def test_extract_error_from_response(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": {"message": "bad request"}}
        assert GeminiProvider._extract_error(mock_resp) == "bad request"

    def test_endpoint_default(self):
        provider = GeminiProvider(api_key="k")
        assert "generativelanguage.googleapis.com" in provider.endpoint


# ═══════════════════════════════════════════════════════════════════════════
# 5. Anthropic Provider
# ═══════════════════════════════════════════════════════════════════════════

from core.providers.anthropic_stub import AnthropicProvider


class TestAnthropicProvider:

    def test_instantiation(self):
        provider = AnthropicProvider(api_key="test-key")
        assert provider.provider_type == ProviderType.ANTHROPIC
        assert provider.get_provider_name() == "anthropic"

    def test_ensure_client_raises_without_key(self):
        provider = AnthropicProvider(api_key=None)
        provider._api_key = None
        with pytest.raises(NotConfiguredError):
            provider._ensure_client()

    def test_endpoint_default(self):
        provider = AnthropicProvider(api_key="k")
        assert "anthropic.com" in provider.endpoint

    def test_list_models_static(self):
        """Anthropic list_models returns hardcoded list (no API endpoint)."""
        import asyncio
        provider = AnthropicProvider(api_key="k")
        models = asyncio.run(provider.list_models())
        assert len(models) > 0
        assert any("claude" in m for m in models)


# ═══════════════════════════════════════════════════════════════════════════
# 6. Ollama Provider
# ═══════════════════════════════════════════════════════════════════════════

from core.providers.ollama_provider import OllamaProvider


class TestOllamaProvider:

    def test_instantiation(self):
        provider = OllamaProvider()
        assert provider.provider_type == ProviderType.OLLAMA
        assert provider.get_provider_name() == "ollama"

    def test_endpoint_default(self):
        provider = OllamaProvider()
        assert "localhost:11434" in provider.endpoint

    def test_custom_endpoint(self):
        provider = OllamaProvider(endpoint="http://gpu-server:11434")
        assert "gpu-server" in provider.endpoint


# ═══════════════════════════════════════════════════════════════════════════
# 7. Config Loader Factory
# ═══════════════════════════════════════════════════════════════════════════

from core.abstraction.config_loader import ConfigLoader
from core.abstraction.registry import ProviderConfig


class TestConfigLoaderFactory:
    """Test that _create_provider handles all 4 types."""

    def _make_config(self, ptype: str) -> ProviderConfig:
        return ProviderConfig(
            name=ptype,
            provider_type=ptype,
            enabled=True,
            priority=1,
            api_key="test-key",
            timeout=30,
        )

    def test_create_ollama(self):
        loader = ConfigLoader.__new__(ConfigLoader)
        provider = loader._create_provider("ollama", self._make_config("ollama"))
        assert isinstance(provider, OllamaProvider)

    def test_create_openai(self):
        loader = ConfigLoader.__new__(ConfigLoader)
        provider = loader._create_provider("openai", self._make_config("openai"))
        assert isinstance(provider, OpenAIProvider)

    def test_create_gemini(self):
        loader = ConfigLoader.__new__(ConfigLoader)
        provider = loader._create_provider("gemini", self._make_config("gemini"))
        assert isinstance(provider, GeminiProvider)

    def test_create_anthropic(self):
        loader = ConfigLoader.__new__(ConfigLoader)
        provider = loader._create_provider("anthropic", self._make_config("anthropic"))
        assert isinstance(provider, AnthropicProvider)

    def test_create_unknown_raises(self):
        loader = ConfigLoader.__new__(ConfigLoader)
        with pytest.raises(ValueError, match="Unknown provider"):
            loader._create_provider("unknown", self._make_config("unknown"))


# ═══════════════════════════════════════════════════════════════════════════
# 8. Package __init__ exports
# ═══════════════════════════════════════════════════════════════════════════


class TestPackageExports:

    def test_all_providers_in_init(self):
        import core.providers as pkg
        assert pkg.OllamaProvider is not None
        assert pkg.OpenAIProvider is not None
        assert pkg.GeminiProvider is not None
        assert pkg.AnthropicProvider is not None

    def test_all_list(self):
        import core.providers as pkg
        assert "OllamaProvider" in pkg.__all__
        assert "OpenAIProvider" in pkg.__all__
        assert "GeminiProvider" in pkg.__all__
        assert "AnthropicProvider" in pkg.__all__


# ═══════════════════════════════════════════════════════════════════════════
# 9. Connector Registry Structure
# ═══════════════════════════════════════════════════════════════════════════

from core.intelligence.connector_registry import (
    IntelligenceConnectorRegistry,
    ProviderDescriptor,
    get_connector_registry,
)


class TestConnectorRegistry:

    def test_register_and_get(self):
        registry = IntelligenceConnectorRegistry()
        provider = OllamaProvider()
        desc = registry.register(
            "test_ollama", provider, ProviderType.OLLAMA, is_local=True,
        )
        assert isinstance(desc, ProviderDescriptor)
        assert registry.get("test_ollama") is provider

    def test_unregister(self):
        registry = IntelligenceConnectorRegistry()
        provider = OllamaProvider()
        registry.register("tmp", provider, ProviderType.OLLAMA)
        assert registry.unregister("tmp") is True
        assert registry.get("tmp") is None

    def test_list_providers(self):
        registry = IntelligenceConnectorRegistry()
        p1 = OllamaProvider()
        p2 = OpenAIProvider(api_key="k")
        registry.register("ollama", p1, ProviderType.OLLAMA, is_local=True)
        registry.register("openai", p2, ProviderType.OPENAI)
        names = registry.list_providers()
        assert "ollama" in names
        assert "openai" in names

    def test_get_by_type(self):
        registry = IntelligenceConnectorRegistry()
        p = GeminiProvider(api_key="k")
        registry.register("gemini", p, ProviderType.GEMINI)
        assert registry.get_by_type(ProviderType.GEMINI) is p

    def test_status(self):
        registry = IntelligenceConnectorRegistry()
        registry.register("ollama", OllamaProvider(), ProviderType.OLLAMA, is_local=True)
        status = registry.status()
        assert status["total"] == 1
        assert "ollama" in status["providers"]

    def test_singleton(self):
        r = get_connector_registry()
        assert isinstance(r, IntelligenceConnectorRegistry)


# ═══════════════════════════════════════════════════════════════════════════
# 10. Capabilities Registry
# ═══════════════════════════════════════════════════════════════════════════

from core.routing.capabilities import (
    CAPABILITY_REGISTRY,
    get_available_models,
    refresh_availability,
    Capability,
    ModelProfile,
)


class TestCapabilities:

    def test_ollama_models_exist(self):
        ollama_models = [
            k for k, v in CAPABILITY_REGISTRY.items() if v.provider == "ollama"
        ]
        assert len(ollama_models) > 0

    def test_cloud_profiles_exist(self):
        providers = {v.provider for v in CAPABILITY_REGISTRY.values()}
        assert "openai" in providers
        assert "gemini" in providers
        assert "anthropic" in providers

    def test_refresh_availability(self):
        """refresh_availability should not raise."""
        refresh_availability()

    def test_model_profile_key(self):
        profile = ModelProfile(
            provider="test", model="m1",
            capabilities={Capability.REASONING},
            cost_per_1k_tokens=0.0,
            avg_latency_ms=100, max_context_tokens=4096,
        )
        assert profile.key == "test:m1"
