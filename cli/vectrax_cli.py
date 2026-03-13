#!/usr/bin/env python3
"""
Vectrax CLI - Quick interface to local LLM
Usage: python cli/vectrax_cli.py "your prompt here"
"""
import sys
import asyncio
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.abstraction import GenerateRequest
from core.providers import OllamaProvider


async def generate(prompt: str, model: str = "llama3.2:3b", stream: bool = True):
    """Generate response using local LLM"""
    provider = OllamaProvider()
    
    # Check health first
    if not await provider.health_check():
        print("❌ Error: Ollama is not running")
        print("Start it with: brew services start ollama")
        return
    
    request = GenerateRequest(
        prompt=prompt,
        model=model,
        temperature=0.7,
    )
    
    if stream:
        # Streaming response
        async for chunk in provider.stream(request):
            print(chunk, end="", flush=True)
        print()  # newline at end
    else:
        # Non-streaming response
        response = await provider.generate(request)
        print(response.content)
        print(f"\n[{response.total_tokens} tokens | {response.latency_ms:.0f}ms | {response.provider}]")
    
    await provider.close()


async def list_models():
    """List available models"""
    provider = OllamaProvider()
    models = await provider.list_models()
    
    print("Available models:")
    for model in models:
        print(f"  • {model}")
    
    await provider.close()


def main():
    if len(sys.argv) < 2:
        print("Vectrax CLI - Local-first AI")
        print("\nUsage:")
        print("  python cli/vectrax_cli.py 'your prompt'")
        print("  python cli/vectrax_cli.py --models")
        print("\nExamples:")
        print("  python cli/vectrax_cli.py 'explain quantum computing'")
        print("  python cli/vectrax_cli.py 'write a python function' --model qwen2.5-coder:7b")
        sys.exit(1)
    
    if sys.argv[1] == "--models":
        asyncio.run(list_models())
    else:
        prompt = sys.argv[1]
        model = "llama3.2:3b"
        
        # Check for --model flag
        if "--model" in sys.argv:
            idx = sys.argv.index("--model")
            if idx + 1 < len(sys.argv):
                model = sys.argv[idx + 1]
        
        asyncio.run(generate(prompt, model=model))


if __name__ == "__main__":
    main()
