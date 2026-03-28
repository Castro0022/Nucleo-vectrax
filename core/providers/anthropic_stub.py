"""
Anthropic Provider Stub
========================
Optional adapter for Anthropic API. No hard dependency.
Reads ANTHROPIC_API_KEY from environment.
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
from core.resilience.errors import NotConfiguredError


class AnthropicProvider(BaseLLMProvider):
    """Anthropic (Claude) API provider stub."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: str = "https://api.anthropic.com/v1",
        timeout: int = 60,
        **kwargs,
    ):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        super().__init__(
            provider_type=ProviderType.ANTHROPIC,
            endpoint=endpoint,
            api_key=self._api_key,
            timeout=timeout,
            **kwargs,
        )
        self._client = None

    def _ensure_client(self):
        if self._api_key is None:
            raise NotConfiguredError(
                "anthropic",
                "ANTHROPIC_API_KEY not set. Export it or pass api_key=..."
            )
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
            )

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        self._ensure_client()
        start = time.time()

        payload = {
            "model": request.model,
            "max_tokens": request.max_tokens or 4096,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.system_prompt:
            payload["system"] = request.system_prompt

        resp = await self._client.post(
            f"{self.endpoint}/messages", json=payload
        )
        resp.raise_for_status()
        data = resp.json()

        content_blocks = data.get("content", [])
        text = ""
        for block in content_blocks:
            if block.get("type") == "text":
                text += block.get("text", "")

        usage = data.get("usage", {})

        return GenerateResponse(
            content=text,
            model=request.model,
            provider="anthropic",
            prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"),
            total_tokens=(
                (usage.get("input_tokens") or 0)
                + (usage.get("output_tokens") or 0)
            ),
            latency_ms=(time.time() - start) * 1000,
        )

    async def stream(self, request: GenerateRequest) -> AsyncIterator[str]:
        self._ensure_client()

        payload = {
            "model": request.model,
            "max_tokens": request.max_tokens or 4096,
            "messages": [{"role": "user", "content": request.prompt}],
            "stream": True,
        }
        if request.system_prompt:
            payload["system"] = request.system_prompt

        async with self._client.stream(
            "POST", f"{self.endpoint}/messages", json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    import json
                    event = json.loads(line[6:])
                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            yield delta.get("text", "")

    async def health_check(self) -> bool:
        try:
            self._ensure_client()
            # Anthropic doesn't have a /models endpoint — test with a minimal call
            resp = await self._client.post(
                f"{self.endpoint}/messages",
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "ping"}],
                },
            )
            return resp.status_code in (200, 400)  # 400 = auth ok but bad request
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        # Anthropic doesn't have a list models endpoint
        return ["claude-sonnet-4-20250514", "claude-3-5-haiku-20241022"]

    async def close(self):
        if self._client:
            await self._client.aclose()
