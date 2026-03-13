#!/usr/bin/env python3
"""
Observability CLI - View metrics, traces, and system health
"""
import asyncio
import json
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.observability import get_metrics_collector, get_tracer
from core.abstraction import ConfigLoader


def display_metrics():
    """Display current metrics"""
    metrics = get_metrics_collector()
    summary = metrics.get_summary()
    
    print("\n" + "="*60)
    print("📊 VECTRAX METRICS")
    print("="*60)
    
    # Counters
    print("\n🔢 COUNTERS:")
    for name, value in summary['counters'].items():
        print(f"  {name:40s} {int(value):>10,}")
    
    # Gauges
    print("\n📈 GAUGES:")
    for name, value in summary['gauges'].items():
        print(f"  {name:40s} {value:>10.2f}")
    
    # Histograms
    print("\n📊 HISTOGRAMS:")
    for name, stats in summary['histograms'].items():
        print(f"\n  {name}:")
        print(f"    Count:    {stats['count']:>10}")
        print(f"    Sum:      {stats['sum']:>10.3f}s")
        print(f"    Average:  {stats['avg']:>10.3f}s")
        print(f"    Min:      {stats['min']:>10.3f}s")
        print(f"    Max:      {stats['max']:>10.3f}s")
        print(f"    P50:      {stats['p50']:>10.3f}s")
        print(f"    P95:      {stats['p95']:>10.3f}s")
        print(f"    P99:      {stats['p99']:>10.3f}s")
    
    print("\n" + "="*60)


def display_prometheus():
    """Display metrics in Prometheus format"""
    metrics = get_metrics_collector()
    print(metrics.export_prometheus())


def display_traces():
    """Display active traces"""
    tracer = get_tracer()
    
    print("\n" + "="*60)
    print("🔍 VECTRAX TRACES")
    print("="*60)
    
    if not tracer._traces:
        print("\nNo traces recorded yet.")
    else:
        for trace_id, trace in tracer._traces.items():
            print(f"\nTrace ID: {trace_id}")
            print(f"  Spans: {len(trace.spans)}")
            for span in trace.spans:
                duration = span.duration()
                duration_str = f"{duration:.3f}s" if duration else "in progress"
                print(f"    - {span.name} ({span.status}) - {duration_str}")
    
    print("\n" + "="*60)


async def display_system_health():
    """Display system health status"""
    print("\n" + "="*60)
    print("🏥 SYSTEM HEALTH")
    print("="*60)
    
    try:
        # Load config and check providers
        config_loader = ConfigLoader("config/config.yaml")
        registry = config_loader.build_registry()
        
        print("\n📡 PROVIDERS:")
        for provider_name in registry.list_providers():
            provider = registry.get_provider(provider_name)
            is_healthy = await provider.health_check()
            status = "✅ HEALTHY" if is_healthy else "❌ DOWN"
            print(f"  {provider_name:20s} {status}")
            
            # List models if available
            if is_healthy:
                models = await provider.list_models()
                if models:
                    print(f"    Models: {', '.join(models[:3])}")
                    if len(models) > 3:
                        print(f"            ... and {len(models) - 3} more")
        
        print("\n" + "="*60)
        
    except Exception as e:
        print(f"\n❌ Error checking system health: {e}")
        print("="*60)


def display_summary():
    """Display complete observability summary"""
    display_metrics()
    display_traces()
    asyncio.run(display_system_health())


def main():
    """Main CLI entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Vectrax Observability CLI"
    )
    parser.add_argument(
        'command',
        choices=['metrics', 'traces', 'health', 'prometheus', 'summary'],
        help='What to display'
    )
    
    args = parser.parse_args()
    
    if args.command == 'metrics':
        display_metrics()
    elif args.command == 'traces':
        display_traces()
    elif args.command == 'health':
        asyncio.run(display_system_health())
    elif args.command == 'prometheus':
        display_prometheus()
    elif args.command == 'summary':
        display_summary()


if __name__ == "__main__":
    main()
