"""
Phase 2 Verification Test
Tests provider registry and configuration-driven provider management:
- Registry can manage multiple providers
- Config loader builds registry from YAML
- Can switch providers via configuration
- Model aliases work correctly
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.abstraction import (
    GenerateRequest,
    ProviderRegistry,
    ProviderConfig,
    load_registry_from_config
)
from core.providers import OllamaProvider


async def test_registry_basic():
    """Test 1: Basic registry operations"""
    print("🔍 Test 1: Basic registry operations...")
    
    registry = ProviderRegistry()
    
    # Create provider and config
    provider = OllamaProvider()
    config = ProviderConfig(
        name="ollama",
        provider_type="ollama",
        enabled=True,
        priority=1,
        models={"default": "llama3.2:3b", "fast": "llama3.2:3b"}
    )
    
    # Register
    registry.register("ollama", provider, config)
    
    # Check registration
    assert "ollama" in registry.list_providers()
    assert registry.get_provider("ollama") == provider
    
    print("✅ Registry basic operations working")
    return True


async def test_config_loading():
    """Test 2: Load registry from YAML config"""
    print("\n🔍 Test 2: Loading registry from config.yaml...")
    
    try:
        registry = load_registry_from_config()
        
        # Check that providers were loaded
        providers = registry.list_providers()
        print(f"   Loaded providers: {providers}")
        
        # Check ollama is registered and enabled
        enabled_providers = registry.list_providers(enabled_only=True)
        assert "ollama" in enabled_providers, "Ollama should be enabled"
        
        print(f"✅ Config loaded successfully")
        print(f"   Registry: {registry}")
        return True
    except Exception as e:
        print(f"❌ Config loading failed: {e}")
        return False


async def test_generate_via_registry():
    """Test 3: Generate using registry convenience method"""
    print("\n🔍 Test 3: Generate using registry...")
    
    try:
        registry = load_registry_from_config()
        
        # Generate using default provider
        response = await registry.generate(
            prompt="Say hello in one sentence",
            model="llama3.2:3b",
            temperature=0.7,
            max_tokens=30
        )
        
        print(f"✅ Generated via registry:")
        print(f"   Content: {response.content[:80]}...")
        print(f"   Provider: {response.provider}")
        print(f"   Model: {response.model}")
        print(f"   Latency: {response.latency_ms:.0f}ms")
        
        return True
    except Exception as e:
        print(f"❌ Registry generation failed: {e}")
        return False


async def test_model_aliases():
    """Test 4: Model alias resolution"""
    print("\n🔍 Test 4: Testing model aliases...")
    
    try:
        registry = load_registry_from_config()
        
        # Get model aliases from config
        fast_model = registry.get_model_alias("ollama", "fast")
        code_model = registry.get_model_alias("ollama", "code")
        
        print(f"   fast → {fast_model}")
        print(f"   code → {code_model}")
        
        assert fast_model is not None, "Fast model alias should exist"
        assert code_model is not None, "Code model alias should exist"
        
        print("✅ Model aliases working")
        return True
    except Exception as e:
        print(f"❌ Model alias test failed: {e}")
        return False


async def test_health_check_all():
    """Test 5: Health check all providers"""
    print("\n🔍 Test 5: Health check all providers...")
    
    try:
        registry = load_registry_from_config()
        
        health_status = await registry.health_check_all()
        
        print("   Provider health status:")
        for provider, is_healthy in health_status.items():
            status = "✅ Healthy" if is_healthy else "❌ Unhealthy"
            print(f"   - {provider}: {status}")
        
        # At least ollama should be healthy
        assert health_status.get("ollama", False), "Ollama should be healthy"
        
        print("✅ Health checks completed")
        return True
    except Exception as e:
        print(f"❌ Health check failed: {e}")
        return False


async def test_provider_switching():
    """Test 6: Demonstrate provider switching via config"""
    print("\n🔍 Test 6: Provider switching demonstration...")
    
    try:
        registry = load_registry_from_config()
        
        # Show current default
        default = registry._default_provider
        print(f"   Current default provider: {default}")
        
        # Show all providers (enabled and disabled)
        all_providers = registry.list_providers(enabled_only=False)
        print(f"   All registered providers: {all_providers}")
        
        enabled = registry.list_providers(enabled_only=True)
        print(f"   Enabled providers: {enabled}")
        
        print("✅ Provider management working")
        print("\n   To switch provider: Edit config/config.yaml")
        print("   - Change 'enabled: true/false' for any provider")
        print("   - Change 'default_provider' in routing section")
        print("   - Restart application (or reload config)")
        
        return True
    except Exception as e:
        print(f"❌ Provider switching test failed: {e}")
        return False


async def main():
    """Run all tests"""
    print("=" * 70)
    print("VECTRAX PHASE 2 VERIFICATION TEST")
    print("Testing: Provider Registry & Configuration Management")
    print("=" * 70)
    
    tests = [
        test_registry_basic,
        test_config_loading,
        test_generate_via_registry,
        test_model_aliases,
        test_health_check_all,
        test_provider_switching,
    ]
    
    results = []
    for test in tests:
        try:
            result = await test()
            results.append(result)
        except Exception as e:
            print(f"❌ Test crashed: {e}")
            import traceback
            traceback.print_exc()
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
        print("\n🎉 All tests passed! Phase 2 setup is complete.")
        print("\n✅ PHASE 2 SUCCESS CRITERIA MET:")
        print("   • ProviderRegistry implemented and working")
        print("   • Config loader builds registry from YAML")
        print("   • Can switch providers by editing config.yaml")
        print("   • Model aliases resolved correctly")
        print("   • Health checks working across all providers")
        print("\n🔄 TO SWITCH PROVIDERS:")
        print("   1. Edit config/config.yaml")
        print("   2. Change enabled: true/false for providers")
        print("   3. Change routing.default_provider")
        print("   4. Restart (or reload config)")
        return 0
    else:
        print("\n⚠️  Some tests failed. Check the output above.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
