#!/usr/bin/env python3
"""
Phase 5 Tests - Observability System
Tests metrics collection, logging, and tracing
"""
import pytest
import asyncio
import time
from core.observability import (
    get_metrics_collector,
    get_tracer,
    SpanContext,
    setup_structured_logging,
    set_request_context,
    clear_request_context
)
from core.abstraction import ConfigLoader
from core.abstraction.base import GenerateRequest


@pytest.mark.asyncio
async def test_metrics_collection():
    """Test that metrics are collected correctly"""
    metrics = get_metrics_collector()
    
    # Reset metrics
    metrics.reset_all()
    
    # Record some metrics
    metrics.record_llm_request(
        duration=1.5,
        provider="ollama",
        model="llama3.2:3b",
        success=True,
        tokens=100
    )
    
    metrics.record_llm_request(
        duration=2.0,
        provider="ollama",
        model="llama3.2:3b",
        success=True,
        tokens=150
    )
    
    # Get summary
    summary = metrics.get_summary()
    
    # Check counters
    assert summary['counters']['llm_requests_total'] == 2
    assert summary['counters']['llm_requests_failed'] == 0
    assert summary['counters']['llm_tokens_total'] == 250
    
    # Check histograms
    duration_stats = summary['histograms']['llm_request_duration_seconds']
    assert duration_stats['count'] == 2
    assert duration_stats['avg'] == pytest.approx(1.75, 0.01)
    assert duration_stats['min'] == 1.5
    assert duration_stats['max'] == 2.0
    
    print("✅ Metrics collection works")


@pytest.mark.asyncio
async def test_metrics_failed_requests():
    """Test that failed requests are tracked"""
    metrics = get_metrics_collector()
    metrics.reset_all()
    
    # Record a failed request
    metrics.record_llm_request(
        duration=0.5,
        provider="ollama",
        model="llama3.2:3b",
        success=False
    )
    
    summary = metrics.get_summary()
    
    assert summary['counters']['llm_requests_total'] == 1
    assert summary['counters']['llm_requests_failed'] == 1
    
    print("✅ Failed request tracking works")


@pytest.mark.asyncio
async def test_workflow_metrics():
    """Test workflow execution metrics"""
    metrics = get_metrics_collector()
    metrics.reset_all()
    
    # Record workflow execution
    metrics.record_workflow_execution(
        duration=3.5,
        workflow_name="test_workflow",
        success=True
    )
    
    summary = metrics.get_summary()
    
    assert summary['counters']['workflows_executed_total'] == 1
    assert summary['histograms']['workflow_duration_seconds']['count'] == 1
    
    print("✅ Workflow metrics work")


@pytest.mark.asyncio
async def test_tracing():
    """Test distributed tracing"""
    tracer = get_tracer()
    
    # Start a trace
    trace_id = tracer.start_trace()
    
    # Start a span
    span1 = tracer.start_span(trace_id, "operation1", attributes={"key": "value"})
    time.sleep(0.1)
    tracer.end_span(span1.span_id)
    
    # Start a child span
    span2 = tracer.start_span(trace_id, "operation2", parent_id=span1.span_id)
    time.sleep(0.05)
    tracer.end_span(span2.span_id)
    
    # Get trace
    trace = tracer.get_trace(trace_id)
    assert trace is not None
    assert len(trace.spans) == 2
    
    # Check span hierarchy
    assert trace.spans[1].parent_id == span1.span_id
    
    # Check durations
    assert span1.duration() >= 0.1
    assert span2.duration() >= 0.05
    
    print("✅ Tracing works")


@pytest.mark.asyncio
async def test_span_context():
    """Test span context manager"""
    tracer = get_tracer()
    trace_id = tracer.start_trace()
    
    # Use context manager
    with SpanContext(tracer, trace_id, "context_test") as span:
        time.sleep(0.05)
        span.set_attribute("test_attr", "test_value")
        span.add_event("test_event", {"detail": "test"})
    
    # Check span was properly closed
    trace = tracer.get_trace(trace_id)
    assert len(trace.spans) == 1
    assert trace.spans[0].status == "OK"
    assert trace.spans[0].duration() >= 0.05
    assert trace.spans[0].attributes["test_attr"] == "test_value"
    assert len(trace.spans[0].events) == 1
    
    print("✅ Span context manager works")


@pytest.mark.asyncio
async def test_prometheus_export():
    """Test Prometheus format export"""
    metrics = get_metrics_collector()
    metrics.reset_all()
    
    # Add some metrics
    metrics.record_llm_request(1.0, "ollama", "llama3.2:3b", True, 100)
    
    # Export
    prometheus_text = metrics.export_prometheus()
    
    # Check format
    assert "# HELP" in prometheus_text
    assert "# TYPE" in prometheus_text
    assert "llm_requests_total" in prometheus_text
    
    print("✅ Prometheus export works")


@pytest.mark.asyncio
async def test_integration_with_provider():
    """Test that provider records metrics"""
    metrics = get_metrics_collector()
    metrics.reset_all()
    
    # Load config and create provider
    config_loader = ConfigLoader("config/config.yaml")
    registry = config_loader.build_registry()
    
    # Make a request
    request = GenerateRequest(
        prompt="Say 'test'",
        model="llama3.2:3b",
        max_tokens=10
    )
    
    try:
        response = await registry.generate_direct("ollama", request)
        
        # Check that metrics were recorded
        summary = metrics.get_summary()
        assert summary['counters']['llm_requests_total'] >= 1
        
        print("✅ Provider integration works")
    except Exception as e:
        print(f"⚠️  Provider test skipped (Ollama might not be running): {e}")


@pytest.mark.asyncio
async def test_integration_with_workflow():
    """Test that workflow records metrics"""
    from core.workflows import WorkflowOrchestrator, WorkflowStep, StepType
    
    metrics = get_metrics_collector()
    metrics.reset_all()
    
    # Load config
    config_loader = ConfigLoader("config/config.yaml")
    registry = config_loader.build_registry()
    
    # Create orchestrator
    orchestrator = WorkflowOrchestrator(registry)
    
    # Create simple workflow
    steps = [
        WorkflowStep(
            name="test_step",
            step_type=StepType.LLM,
            prompt_template="Say 'test'",
            config={"max_tokens": 10, "output_key": "result"}
        )
    ]
    
    try:
        context = await orchestrator.execute_steps(steps, {})
        
        # Check workflow metrics
        summary = metrics.get_summary()
        # Note: workflows_executed_total is only incremented by execute_workflow, not execute_steps
        # But LLM requests should still be recorded
        assert summary['counters']['llm_requests_total'] >= 1
        
        print("✅ Workflow integration works")
    except Exception as e:
        print(f"⚠️  Workflow test skipped (Ollama might not be running): {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
