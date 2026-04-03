#!/usr/bin/env python3
"""
Demo: Observability in Action
Shows metrics collection, tracing, and logging working together
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.abstraction import ConfigLoader
from core.abstraction.base import GenerateRequest
from core.observability import (
    get_metrics_collector,
    get_tracer,
    SpanContext,
    setup_structured_logging
)


async def main():
    """Demo observability features"""
    
    # Setup structured logging
    setup_structured_logging(log_level="INFO", use_json=False)
    
    print("=" * 70)
    print("🔭 VECTRAX OBSERVABILITY DEMO")
    print("=" * 70)
    
    # Get observability components
    metrics = get_metrics_collector()
    tracer = get_tracer()
    
    # Start trace
    trace_id = tracer.start_trace()
    print(f"\n📍 Started trace: {trace_id}\n")
    
    # Load config
    with SpanContext(tracer, trace_id, "load_config") as span:
        config_loader = ConfigLoader("config/config.yaml")
        registry = config_loader.build_registry()
        span.set_attribute("providers_loaded", len(registry.list_providers()))
    
    # Make LLM requests
    print("📝 Making LLM requests...\n")
    
    with SpanContext(tracer, trace_id, "llm_requests") as parent_span:
        for i in range(3):
            request = GenerateRequest(
                prompt=f"Say hello in language #{i+1}",
                model="llama3.2:3b",
                max_tokens=20
            )
            
            with SpanContext(tracer, trace_id, f"request_{i+1}", 
                           parent_id=parent_span.span_id) as span:
                try:
                    response = await registry.generate(prompt=request.prompt, 
                                                      model=request.model,
                                                      max_tokens=request.max_tokens)
                    
                    print(f"  {i+1}. {response.content[:50]}...")
                    span.set_attribute("tokens", response.total_tokens)
                    span.set_attribute("latency_ms", response.latency_ms)
                    
                except Exception as e:
                    print(f"  {i+1}. Error: {e}")
                    span.set_attribute("error", str(e))
    
    # Show metrics
    print("\n" + "=" * 70)
    print("📊 METRICS COLLECTED")
    print("=" * 70)
    
    summary = metrics.get_summary()
    
    # Counters
    print("\n🔢 Counters:")
    for name, value in summary['counters'].items():
        if value > 0:
            print(f"  {name:40s} {int(value):>10,}")
    
    # Histograms
    print("\n📊 Histograms:")
    for name, stats in summary['histograms'].items():
        if stats['count'] > 0:
            print(f"\n  {name}:")
            print(f"    Count:    {stats['count']:>10}")
            print(f"    Average:  {stats['avg']:>10.3f}s")
            print(f"    P50:      {stats['p50']:>10.3f}s")
            print(f"    P95:      {stats['p95']:>10.3f}s")
    
    # Show trace
    print("\n" + "=" * 70)
    print("🔍 TRACE SUMMARY")
    print("=" * 70)
    
    trace = tracer.get_trace(trace_id)
    print(f"\nTrace ID: {trace_id}")
    print(f"Total spans: {len(trace.spans)}\n")
    
    for span in trace.spans:
        indent = "  " * (1 if span.parent_id else 0)
        duration = span.duration()
        duration_str = f"{duration:.3f}s" if duration else "in progress"
        status_icon = "✅" if span.status == "OK" else "❌"
        
        print(f"{indent}{status_icon} {span.name} - {duration_str}")
        
        # Show attributes if present
        if span.attributes:
            for key, value in span.attributes.items():
                print(f"{indent}    {key}: {value}")
    
    print("\n" + "=" * 70)
    print("✅ Demo complete!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
