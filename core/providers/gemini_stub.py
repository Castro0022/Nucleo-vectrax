"""
Gemini Provider Stub
=====================
Optional adapter for Google Gemini API. No hard dependency.
Reads GEMINI_API_KEY from environment.
"""

from __future__ import annotations

import os
import time
from typing import AsyncIterator, Optional

from core.abstraction.base import (
    BaseLLMProvider,
    GenerateRequest,
    GenerateResponse,
    ProviderType,
)
from core.providers.openai_stub import NotConfiguredError


class GeminiProvider(BaseLLMProvider):
    """Google Gemini API provider stub."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout: int = 60,
        **kwargs,
    ):
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        super().__init__(
            provider_type=ProviderType.GEMINI,
            endpoint=endpoint,
            api_key=self._api_key,
            timeout=timeout,
            **kwargs,
        )
        self._client = None

    def _ensure_client(self):
        if self._api_key is None:
            raise NotConfiguredError(
                "GEMINI_API_KEY not set. Export it or pass api_key=..."
            )
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=self.timeout)

    def get_provider_name(self) -> str:
        return "gemini"

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        self._ensure_client()
        start = time.time()

        url = (
            f"{self.endpoint}/models/{request.model}:generateContent"
            f"?key={self._api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": request.prompt}]}],
            "generationConfig": {"temperature": request.temperature},
        }
        if request.max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = request.max_tokens

        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

        text = ""
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                text = parts[0].get("text", "")

        usage = data.get("usageMetadata", {})

        return GenerateResponse(
            content=text,
            model=request.model,
            provider="gemini",
            prompt_tokens=usage.get("promptTokenCount"),
            completion_tokens=usage.get("candidatesTokenCount"),
            total_tokens=usage.get("totalTokenCount"),
            latency_ms=(time.time() - start) * 1000,
        )

    async def stream(self, request: GenerateRequest) -> AsyncIterator[str]:
        self._ensure_client()

        url = (
            f"{self.endpoint}/models/{request.model}:streamGenerateContent"
            f"?key={self._api_key}&alt=sse"
        )
        payload = {
            "contents": [{"parts": [{"text": request.prompt}]}],
            "generationConfig": {"temperature": request.temperature},
        }

        async with self._client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    import json
                    chunk = json.loads(line[6:])
                    candidates = chunk.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            yield parts[0].get("text", "")

    async def health_check(self) -> bool:
        try:
            self._ensure_client()
            url = f"{self.endpoint}/models?key={self._api_key}"
            resp = await self._client.get(url)
            return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        try:
            self._ensure_client()
            url = f"{self.endpoint}/models?key={self._api_key}"
            resp = await self._client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    async def close(self):
        if self._client:
            await self._client.aclose()
