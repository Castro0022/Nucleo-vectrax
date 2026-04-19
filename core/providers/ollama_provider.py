"""
Ollama provider implementation.
Connects to local Ollama server.
"""
import time
import httpx
from typing import AsyncIterator, Optional
from core.abstraction.base import (
    BaseLLMProvider, 
    ProviderType, 
    GenerateRequest, 
    GenerateResponse
)
from core.observability import get_metrics_collector
from vectrax.core_identity import VECTRAX_SYSTEM_PROMPT, enrich_user_prompt


class OllamaProvider(BaseLLMProvider):
    """Ollama local LLM provider"""
    
    def __init__(
        self,
        endpoint: str = "http://localhost:11434",
        timeout: int = 60,
        **kwargs
    ):
        super().__init__(
            provider_type=ProviderType.OLLAMA,
            endpoint=endpoint,
            timeout=timeout,
            **kwargs
        )
        self.client = httpx.AsyncClient(timeout=timeout)
        
    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        """Generate response from Ollama"""
        start_time = time.time()
        metrics = get_metrics_collector()
        success = False
        
        try:
            # Build Ollama-specific request
            # Strict identity: VECTRAX_SYSTEM_PROMPT is the ONLY system message
            user_content = enrich_user_prompt(request.prompt, request.system_prompt)
            ollama_request = {
                "model": request.model,
                "prompt": user_content,
                "system": VECTRAX_SYSTEM_PROMPT,
                "stream": False,
                "options": {
                    "temperature": request.temperature,
                }
            }
                
            if request.max_tokens:
                ollama_request["options"]["num_predict"] = request.max_tokens
                
            if request.stop_sequences:
                ollama_request["options"]["stop"] = request.stop_sequences
            
            # Make request
            response = await self.client.post(
                f"{self.endpoint}/api/generate",
                json=ollama_request
            )
            response.raise_for_status()
            
            data = response.json()
            latency_ms = (time.time() - start_time) * 1000
            
            # Mark as successful
            success = True
            
            # Convert to standardized response
            result = GenerateResponse(
                content=data.get("response", ""),
                model=request.model,
                provider=self.get_provider_name(),
                prompt_tokens=data.get("prompt_eval_count"),
                completion_tokens=data.get("eval_count"),
                total_tokens=(
                    (data.get("prompt_eval_count") or 0) + 
                    (data.get("eval_count") or 0)
                ),
                latency_ms=latency_ms,
                metadata={
                    "total_duration": data.get("total_duration"),
                    "load_duration": data.get("load_duration"),
                    "eval_duration": data.get("eval_duration"),
                }
            )
            
            # Record metrics
            metrics.record_llm_request(
                duration=time.time() - start_time,
                provider=self.get_provider_name(),
                model=request.model,
                success=True,
                tokens=result.total_tokens
            )
            
            return result
            
        except Exception as e:
            # Record failed request
            metrics.record_llm_request(
                duration=time.time() - start_time,
                provider=self.get_provider_name(),
                model=request.model,
                success=False
            )
            raise
    
    async def stream(self, request: GenerateRequest) -> AsyncIterator[str]:
        """Stream response from Ollama"""
        # Strict identity: single system message only
        user_content = enrich_user_prompt(request.prompt, request.system_prompt)
        ollama_request = {
            "model": request.model,
            "prompt": user_content,
            "system": VECTRAX_SYSTEM_PROMPT,
            "stream": True,
            "options": {
                "temperature": request.temperature,
            }
        }
            
        if request.max_tokens:
            ollama_request["options"]["num_predict"] = request.max_tokens
        
        async with self.client.stream(
            "POST",
            f"{self.endpoint}/api/generate",
            json=ollama_request
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line:
                    import json
                    data = json.loads(line)
                    if "response" in data:
                        yield data["response"]
    
    async def health_check(self) -> bool:
        """Check if Ollama is accessible"""
        try:
            response = await self.client.get(f"{self.endpoint}/api/tags")
            return response.status_code == 200
        except Exception:
            return False
    
    async def list_models(self) -> list[str]:
        """List available Ollama models"""
        try:
            response = await self.client.get(f"{self.endpoint}/api/tags")
            response.raise_for_status()
            data = response.json()
            return [model["name"] for model in data.get("models", [])]
        except Exception:
            return []
    
    async def close(self):
        """Close HTTP client"""
        await self.client.aclose()
    
    def __del__(self):
        """Cleanup on deletion"""
        try:
            import asyncio
            asyncio.create_task(self.close())
        except Exception:
            pass
