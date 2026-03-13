"""
Phase 1 Verification Test
Tests that the basic local setup is working:
- Ollama is running
- Models are available
- Abstraction layer works
- Can generate responses locally
"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from core.abstraction import GenerateRequest
from core.providers import OllamaProvider


async def test_health():
    """Test 1: Ollama health check"""
    print("🔍 Test 1: Checking Ollama health...")
    provider = OllamaProvider()
    
    is_healthy = await provider.health_check()
    if is_healthy:
        print("✅ Ollama is running and accessible")
        return True
    else:
        print("❌ Ollama is not accessible")
        return False


async def test_list_models():
    """Test 2: List available models"""
    print("\n🔍 Test 2: Listing available models...")
    provider = OllamaProvider()
    
    models = await provider.list_models()
    if models:
        print(f"✅ Found {len(models)} models:")
        for model in models:
            print(f"   - {model}")
        return True
    else:
        print("❌ No models found")
        return False


async def test_generate_fast():
    """Test 3: Generate with fast model (llama3.2:3b)"""
    print("\n🔍 Test 3: Testing fast model (llama3.2:3b)...")
    provider = OllamaProvider()
    
    request = GenerateRequest(
        prompt="Say hello and introduce yourself in one sentence.",
        model="llama3.2:3b",
        temperature=0.7,
        max_tokens=50
    )
    
    try:
        response = await provider.generate(request)
        print(f"✅ Fast model response:")
        print(f"   Content: {response.content[:100]}...")
        print(f"   Tokens: {response.total_tokens}")
        print(f"   Latency: {response.latency_ms:.0f}ms")
        print(f"   Provider: {response.provider}")
        return True
    except Exception as e:
        print(f"❌ Fast model failed: {e}")
        return False


async def test_generate_code():
    """Test 4: Generate with code model (qwen2.5-coder:7b)"""
    print("\n🔍 Test 4: Testing code model (qwen2.5-coder:7b)...")
    provider = OllamaProvider()
    
    request = GenerateRequest(
        prompt="Write a Python function to calculate fibonacci numbers.",
        model="qwen2.5-coder:7b",
        temperature=0.3,
        max_tokens=150
    )
    
    try:
        response = await provider.generate(request)
        print(f"✅ Code model response:")
        print(f"   Content: {response.content[:200]}...")
        print(f"   Tokens: {response.total_tokens}")
        print(f"   Latency: {response.latency_ms:.0f}ms")
        return True
    except Exception as e:
        print(f"❌ Code model failed: {e}")
        return False


async def test_streaming():
    """Test 5: Test streaming response"""
    print("\n🔍 Test 5: Testing streaming...")
    provider = OllamaProvider()
    
    request = GenerateRequest(
        prompt="Count from 1 to 5 slowly.",
        model="llama3.2:3b",
        temperature=0.7,
        stream=True
    )
    
    try:
        print("✅ Streaming response: ", end="", flush=True)
        chunks = []
        async for chunk in provider.stream(request):
            print(chunk, end="", flush=True)
            chunks.append(chunk)
        print()  # newline
        print(f"   Received {len(chunks)} chunks")
        return True
    except Exception as e:
        print(f"\n❌ Streaming failed: {e}")
        return False


async def main():
    """Run all tests"""
    print("=" * 70)
    print("VECTRAX PHASE 1 VERIFICATION TEST")
    print("Testing: Local-first setup with Ollama")
    print("=" * 70)
    
    tests = [
        test_health,
        test_list_models,
        test_generate_fast,
        test_generate_code,
        test_streaming,
    ]
    
    results = []
    for test in tests:
        try:
            result = await test()
            results.append(result)
        except Exception as e:
            print(f"❌ Test crashed: {e}")
            results.append(False)
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    passed = sum(results)
    total = len(results)
    print(f"✅ Passed: {passed}/{total}")
    print(f"❌ Failed: {total - passed}/{total}")
    
    if passed == total:
        print("\n🎉 All tests passed! Phase 1 setup is complete.")
        print("\n✅ PHASE 1 SUCCESS CRITERIA MET:")
        print("   • Ollama running locally")
        print("   • Models downloaded and accessible")
        print("   • Abstraction layer working")
        print("   • Can generate text 100% locally without internet")
        return 0
    else:
        print("\n⚠️  Some tests failed. Check the output above.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
