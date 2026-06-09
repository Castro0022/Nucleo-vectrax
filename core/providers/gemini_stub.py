"""
Gemini Provider Stub
=====================
Optional adapter for Google Gemini API. No hard dependency.
Reads GEMINI_API_KEY from environment.
"""

from __future__ import annotations

import json as _json
import os
import time
from typing import AsyncIterator, Dict, Optional

from core.abstraction.base import (
    BaseLLMProvider,
    GenerateRequest,
    GenerateResponse,
    ProviderType,
)
from core.resilience.errors import NotConfiguredError
from vectrax.core_identity import VECTRAX_SYSTEM_PROMPT, enrich_user_prompt


class GeminiProvider(BaseLLMProvider):
    """Google Gemini API provider stub."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout: int = 25,
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
                "gemini",
                "GEMINI_API_KEY not set. Export it or pass api_key=..."
            )
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=5, read=20),
            )

    def get_provider_name(self) -> str:
        return "gemini"

    # -- Payload helpers ----------------------------------------------------

    def _build_payload(self, request: GenerateRequest) -> Dict:
        """Build the Gemini API payload. Identity is always the sole systemInstruction."""
        # Strict identity: VECTRAX_SYSTEM_PROMPT is the ONLY system instruction
        user_content = enrich_user_prompt(request.prompt, request.system_prompt)
        payload: Dict = {
            "contents": [{"parts": [{"text": user_content}]}],
            "generationConfig": {"temperature": request.temperature},
            "systemInstruction": {
                "parts": [{"text": VECTRAX_SYSTEM_PROMPT}]
            },
        }
        if request.max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = request.max_tokens
        return payload

    @staticmethod
    def _extract_text(data: Dict) -> str:
        """Extract generated text from Gemini response JSON."""
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                return parts[0].get("text", "")
        return ""

    @staticmethod
    def _extract_error(resp) -> str:
        """Best-effort error message extraction from a failed response."""
        try:
            body = resp.json()
            err = body.get("error", {})
            return err.get("message", resp.text[:200])
        except Exception:
            return resp.text[:200] if hasattr(resp, "text") else "unknown error"

    # -- generate / stream --------------------------------------------------

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        self._ensure_client()

        # API Gate: skip call entirely if rate-limited (exponential backoff)
        try:
            from core.api_gate import check_gate, record_429, record_success
            if not check_gate("gemini"):
                raise RuntimeError("Gemini gate closed (429 backoff active)")
        except ImportError:
            pass

        start = time.time()

        url = (
            f"{self.endpoint}/models/{request.model}:generateContent"
            f"?key={self._api_key}"
        )
        payload = self._build_payload(request)

        resp = await self._client.post(url, json=payload)
        if resp.status_code == 429:
            try:
                record_429("gemini")
            except Exception:
                pass
            raise RuntimeError(f"Gemini 429: {self._extract_error(resp)}")
        if resp.status_code != 200:
            raise RuntimeError(
                f"Gemini API error ({resp.status_code}): {self._extract_error(resp)}"
            )
        try:
            record_success("gemini")
        except Exception:
            pass
        data = resp.json()

        text = self._extract_text(data)
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
        payload = self._build_payload(request)

        async with self._client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    chunk = _json.loads(line[6:])
                    text = self._extract_text(chunk)
                    if text:
                        yield text

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
