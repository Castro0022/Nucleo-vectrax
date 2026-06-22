"""
OpenAI Provider Stub
=====================
Optional adapter for OpenAI API. No hard dependency.
Reads OPENAI_API_KEY from environment. Raises NotConfiguredError if absent.
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
from vectrax.core_identity import effective_system_prompt


# Backward-compatible re-export
from core.resilience.errors import NotConfiguredError  # noqa: F401


class OpenAIProvider(BaseLLMProvider):
    """OpenAI API provider stub."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: str = "https://api.openai.com/v1",
        timeout: int = 25,
        **kwargs,
    ):
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        super().__init__(
            provider_type=ProviderType.OPENAI,
            endpoint=endpoint,
            api_key=self._api_key,
            timeout=timeout,
            **kwargs,
        )
        self._client = None

    def _ensure_client(self):
        if self._api_key is None:
            raise NotConfiguredError(
                "openai",
                "OPENAI_API_KEY not set. Export it or pass api_key=..."
            )
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=5, read=20),
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        self._ensure_client()

        # ExternalCallGuard: circuit breaker check
        try:
            from core.external_call_guard import _check_circuit
            if not _check_circuit("openai"):
                raise RuntimeError("OpenAI circuit OPEN — failing fast")
        except ImportError:
            pass

        # API Gate: skip call entirely if rate-limited (exponential backoff)
        try:
            from core.api_gate import check_gate, record_429, record_success
            if not check_gate("openai"):
                raise RuntimeError("OpenAI gate closed (429 backoff active)")
        except ImportError:
            pass

        start = time.time()

        # Identity sovereignty by default: when the caller didn't set a
        # system_prompt, effective_system_prompt() supplies VECTRAX_SYSTEM_PROMPT
        # (single source of truth). An explicit system_prompt overrides it.
        payload = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": effective_system_prompt(request.system_prompt)},
                {"role": "user", "content": request.prompt},
            ],
            "temperature": request.temperature,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens

        try:
            resp = await self._client.post(
                f"{self.endpoint}/chat/completions", json=payload
            )
        except Exception as exc:
            try:
                from core.external_call_guard import _record_failure
                _record_failure("openai", str(exc), is_timeout="Timeout" in type(exc).__name__)
            except Exception:
                pass
            raise

        latency_ms = (time.time() - start) * 1000

        if resp.status_code == 429:
            try:
                record_429("openai")
            except Exception:
                pass
            try:
                from core.external_call_guard import _record_failure
                _record_failure("openai", "HTTP 429")
            except Exception:
                pass
            resp.raise_for_status()

        try:
            record_success("openai")
        except Exception:
            pass
        try:
            from core.external_call_guard import _record_success
            _record_success("openai", latency_ms)
        except Exception:
            pass

        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})

        return GenerateResponse(
            content=choice,
            model=request.model,
            provider="openai",
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            latency_ms=latency_ms,
        )

    async def stream(self, request: GenerateRequest) -> AsyncIterator[str]:
        self._ensure_client()

        # Identity sovereignty by default (see generate()).
        payload = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": effective_system_prompt(request.system_prompt)},
                {"role": "user", "content": request.prompt},
            ],
            "temperature": request.temperature,
            "stream": True,
        }

        async with self._client.stream(
            "POST", f"{self.endpoint}/chat/completions", json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    import json
                    chunk = json.loads(line[6:])
                    delta = chunk["choices"][0].get("delta", {})
                    if "content" in delta:
                        yield delta["content"]

    async def health_check(self) -> bool:
        try:
            self._ensure_client()
            resp = await self._client.get(f"{self.endpoint}/models")
            return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        try:
            self._ensure_client()
            resp = await self._client.get(f"{self.endpoint}/models")
            resp.raise_for_status()
            data = resp.json()
            return [m["id"] for m in data.get("data", [])]
        except Exception:
            return []

    async def close(self):
        if self._client:
            await self._client.aclose()
