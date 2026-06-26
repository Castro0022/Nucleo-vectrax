"""
Anthropic (Claude) Provider
============================
Adapter for the Anthropic Messages API over raw httpx — NO SDK dependency.
Consistent with the OpenAI/Gemini stubs: the only third-party HTTP client is
httpx (already a core dependency), imported lazily inside _ensure_client().

Targets the current Messages API:
  - default model claude-opus-4-7
  - adaptive thinking ({"type": "adaptive"}) — the model decides when to reason
  - prompt caching (cache_control) on the frozen Vectrax system prefix
  - sampling params (temperature/top_p/top_k) intentionally omitted — Opus 4.7
    rejects them (HTTP 400); adaptive thinking replaces them

Identity contract: VECTRAX_SYSTEM_PROMPT is the ONLY system message; any
caller-provided context is demoted into the user turn via enrich_user_prompt.

Reads ANTHROPIC_API_KEY from the environment. Mirrors the OpenAI provider's
resilience instrumentation (circuit breaker + API gate).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, AsyncIterator, Optional

from core.abstraction.base import (
    BaseLLMProvider,
    GenerateRequest,
    GenerateResponse,
    ProviderType,
)
from core.resilience.errors import NotConfiguredError
from vectrax.core_identity import VECTRAX_SYSTEM_PROMPT, enrich_user_prompt


# Anthropic API version header (stable; unrelated to any client/SDK version).
_ANTHROPIC_VERSION = "2023-06-01"

# Default model — Opus 4.7 (adaptive thinking, 1M context).
DEFAULT_MODEL = "claude-opus-4-7"

# Non-streaming default keeps responses under typical HTTP timeouts; streaming
# gets a larger ceiling because timeouts aren't a concern there.
_DEFAULT_MAX_TOKENS = 16000
_STREAM_MAX_TOKENS = 64000


class AnthropicProvider(BaseLLMProvider):
    """Anthropic (Claude) Messages API provider over raw httpx (no SDK)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: str = "https://api.anthropic.com/v1",
        timeout: int = 60,
        *,
        enable_thinking: bool = True,
        enable_prompt_caching: bool = True,
        **kwargs,
    ):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._enable_thinking = enable_thinking
        self._enable_prompt_caching = enable_prompt_caching
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
                "ANTHROPIC_API_KEY not set. Export it or pass api_key=...",
            )
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=5, read=self.timeout),
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": _ANTHROPIC_VERSION,
                    "Content-Type": "application/json",
                },
            )

    # ------------------------------------------------------------------
    # Payload construction — single source of identity truth
    # ------------------------------------------------------------------

    def _build_system(self) -> Any:
        """Frozen system prefix.

        VECTRAX_SYSTEM_PROMPT is immutable and identical across requests, so it
        is the natural cache prefix. The cache_control marker is a no-op until
        the prefix exceeds the model minimum (4096 tokens on Opus 4.7); it is
        harmless below that and starts paying off as soon as the prefix grows.
        """
        if self._enable_prompt_caching:
            return [
                {
                    "type": "text",
                    "text": VECTRAX_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        return VECTRAX_SYSTEM_PROMPT

    def _build_payload(
        self, request: GenerateRequest, *, max_tokens: int, stream: bool
    ) -> dict[str, Any]:
        # Strict identity: VECTRAX_SYSTEM_PROMPT is the ONLY system message;
        # any caller-provided context is demoted into the user turn.
        user_content = enrich_user_prompt(request.prompt, request.system_prompt)
        payload: dict[str, Any] = {
            "model": request.model or DEFAULT_MODEL,
            "max_tokens": max_tokens,
            "system": self._build_system(),
            "messages": [{"role": "user", "content": user_content}],
        }
        # NOTE: temperature/top_p/top_k are intentionally omitted — Opus 4.7
        # rejects sampling params (HTTP 400). Adaptive thinking replaces them.
        if request.stop_sequences:
            payload["stop_sequences"] = request.stop_sequences
        if self._enable_thinking:
            # Adaptive thinking: the model decides when/how much to reason.
            # Valid on Opus 4.7/4.6 and Sonnet 4.6 (default model is 4.7).
            payload["thinking"] = {"type": "adaptive"}
        if stream:
            payload["stream"] = True
        return payload

    # ------------------------------------------------------------------
    # Generate / stream
    # ------------------------------------------------------------------

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        self._ensure_client()

        # Resilience: circuit breaker + API gate (mirror the OpenAI provider).
        try:
            from core.external_call_guard import _check_circuit
            if not _check_circuit("anthropic"):
                raise RuntimeError("Anthropic circuit OPEN — failing fast")
        except ImportError:
            pass
        try:
            from core.api_gate import check_gate
            if not check_gate("anthropic"):
                raise RuntimeError("Anthropic gate closed (429 backoff active)")
        except ImportError:
            pass

        payload = self._build_payload(
            request, max_tokens=request.max_tokens or _DEFAULT_MAX_TOKENS, stream=False
        )

        start = time.time()
        try:
            resp = await self._client.post(f"{self.endpoint}/messages", json=payload)
        except Exception as exc:
            self._record_failure(str(exc), is_timeout="Timeout" in type(exc).__name__)
            raise

        latency_ms = (time.time() - start) * 1000

        if resp.status_code == 429:
            self._record_429()
            self._record_failure("HTTP 429")
            resp.raise_for_status()

        if resp.status_code >= 400:
            self._record_failure(f"HTTP {resp.status_code}")
            resp.raise_for_status()

        self._record_success(latency_ms)
        data = resp.json()

        # Surface only assistant text — thinking blocks stay internal.
        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        usage = data.get("usage", {})
        prompt_tokens = usage.get("input_tokens")
        completion_tokens = usage.get("output_tokens")

        return GenerateResponse(
            content=text,
            model=payload["model"],
            provider="anthropic",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=((prompt_tokens or 0) + (completion_tokens or 0)),
            latency_ms=latency_ms,
            metadata={
                "stop_reason": data.get("stop_reason"),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
            },
        )

    async def stream(self, request: GenerateRequest) -> AsyncIterator[str]:
        self._ensure_client()

        payload = self._build_payload(
            request, max_tokens=request.max_tokens or _STREAM_MAX_TOKENS, stream=True
        )

        async with self._client.stream(
            "POST", f"{self.endpoint}/messages", json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta", {})
                    # Yield only assistant text — thinking_delta stays internal.
                    if delta.get("type") == "text_delta":
                        yield delta.get("text", "")

    # ------------------------------------------------------------------
    # Health / discovery
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        try:
            self._ensure_client()
            # Cheap probe against the Models API (GET /v1/models).
            resp = await self._client.get(
                f"{self.endpoint}/models", params={"limit": 1}
            )
            return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        # Curated current models — kept static so this stays hermetic (no network).
        return ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"]

    async def close(self):
        if self._client is not None:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Resilience helpers (defensive — never fatal if guards are absent)
    # ------------------------------------------------------------------

    @staticmethod
    def _record_success(latency_ms: float) -> None:
        try:
            from core.api_gate import record_success
            record_success("anthropic")
        except Exception:
            pass
        try:
            from core.external_call_guard import _record_success
            _record_success("anthropic", latency_ms)
        except Exception:
            pass

    @staticmethod
    def _record_429() -> None:
        try:
            from core.api_gate import record_429
            record_429("anthropic")
        except Exception:
            pass

    @staticmethod
    def _record_failure(detail: str, *, is_timeout: bool = False) -> None:
        try:
            from core.external_call_guard import _record_failure
            _record_failure("anthropic", detail, is_timeout=is_timeout)
        except Exception:
            pass
