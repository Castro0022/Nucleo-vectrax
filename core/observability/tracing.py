"""
Distributed Tracing - Track requests across components
"""
import time
import uuid
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class Span:
    """
    Represents a single span in a trace.
    A span tracks an operation with start/end times.
    """
    span_id: str
    trace_id: str
    name: str
    start_time: float
    parent_id: Optional[str] = None
    end_time: Optional[float] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "OK"  # OK, ERROR
    
    def end(self, status: str = "OK"):
        """Mark span as completed"""
        self.end_time = time.time()
        self.status = status
    
    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        """Add an event to the span"""
        event = {
            'name': name,
            'timestamp': time.time(),
            'attributes': attributes or {}
        }
        self.events.append(event)
    
    def set_attribute(self, key: str, value: Any):
        """Set a span attribute"""
        self.attributes[key] = value
    
    def duration(self) -> Optional[float]:
        """Get span duration in seconds"""
        if self.end_time:
            return self.end_time - self.start_time
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert span to dictionary"""
        return {
            'span_id': self.span_id,
            'trace_id': self.trace_id,
            'name': self.name,
            'parent_id': self.parent_id,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'duration': self.duration(),
            'attributes': self.attributes,
            'events': self.events,
            'status': self.status
        }


@dataclass
class Trace:
    """
    Represents a complete trace with multiple spans.
    """
    trace_id: str
    spans: List[Span] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    
    def add_span(self, span: Span):
        """Add a span to the trace"""
        self.spans.append(span)
    
    def get_span(self, span_id: str) -> Optional[Span]:
        """Get span by ID"""
        for span in self.spans:
            if span.span_id == span_id:
                return span
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert trace to dictionary"""
        return {
            'trace_id': self.trace_id,
            'start_time': self.start_time,
            'span_count': len(self.spans),
            'spans': [span.to_dict() for span in self.spans]
        }


class Tracer:
    """
    Tracer for creating and managing spans.
    """
    
    def __init__(self):
        self._traces: Dict[str, Trace] = {}
        self._active_spans: Dict[str, Span] = {}
    
    def start_trace(self, trace_id: Optional[str] = None) -> str:
        """
        Start a new trace.
        
        Args:
            trace_id: Optional trace ID, generated if not provided
            
        Returns:
            Trace ID
        """
        if trace_id is None:
            trace_id = str(uuid.uuid4())
        
        self._traces[trace_id] = Trace(trace_id=trace_id)
        logger.debug(f"Started trace: {trace_id}")
        return trace_id
    
    def start_span(
        self,
        trace_id: str,
        name: str,
        parent_id: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None
    ) -> Span:
        """
        Start a new span within a trace.
        
        Args:
            trace_id: Trace ID
            name: Span name
            parent_id: Optional parent span ID
            attributes: Optional span attributes
            
        Returns:
            Span instance
        """
        span_id = str(uuid.uuid4())
        span = Span(
            span_id=span_id,
            trace_id=trace_id,
            name=name,
            parent_id=parent_id,
            start_time=time.time(),
            attributes=attributes or {}
        )
        
        # Add to trace
        if trace_id in self._traces:
            self._traces[trace_id].add_span(span)
        
        # Track as active
        self._active_spans[span_id] = span
        
        logger.debug(f"Started span: {name} ({span_id})")
        return span
    
    def end_span(self, span_id: str, status: str = "OK"):
        """
        End a span.
        
        Args:
            span_id: Span ID
            status: Span status (OK or ERROR)
        """
        if span_id in self._active_spans:
            span = self._active_spans[span_id]
            span.end(status)
            del self._active_spans[span_id]
            logger.debug(f"Ended span: {span.name} ({span_id}) - duration: {span.duration():.3f}s")
    
    def get_trace(self, trace_id: str) -> Optional[Trace]:
        """Get trace by ID"""
        return self._traces.get(trace_id)
    
    def get_active_span(self, span_id: str) -> Optional[Span]:
        """Get active span by ID"""
        return self._active_spans.get(span_id)
    
    def clear_trace(self, trace_id: str):
        """Remove a trace from memory"""
        if trace_id in self._traces:
            del self._traces[trace_id]
            logger.debug(f"Cleared trace: {trace_id}")


class SpanContext:
    """
    Context manager for automatic span lifecycle management.
    """
    
    def __init__(
        self,
        tracer: Tracer,
        trace_id: str,
        name: str,
        parent_id: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None
    ):
        self.tracer = tracer
        self.trace_id = trace_id
        self.name = name
        self.parent_id = parent_id
        self.attributes = attributes
        self.span: Optional[Span] = None
    
    def __enter__(self) -> Span:
        """Start span on entering context"""
        self.span = self.tracer.start_span(
            self.trace_id,
            self.name,
            self.parent_id,
            self.attributes
        )
        return self.span
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """End span on exiting context"""
        if self.span:
            status = "ERROR" if exc_type else "OK"
            self.tracer.end_span(self.span.span_id, status)


# Global tracer instance
_global_tracer: Optional[Tracer] = None


def get_tracer() -> Tracer:
    """Get global tracer instance"""
    global _global_tracer
    if _global_tracer is None:
        _global_tracer = Tracer()
    return _global_tracer


def trace_operation(
    trace_id: str,
    operation_name: str,
    parent_id: Optional[str] = None,
    attributes: Optional[Dict[str, Any]] = None
):
    """
    Decorator for automatic span creation.
    
    Usage:
        @trace_operation(trace_id, "my_operation")
        def my_function():
            pass
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            tracer = get_tracer()
            with SpanContext(tracer, trace_id, operation_name, parent_id, attributes) as span:
                return func(*args, **kwargs)
        return wrapper
    return decorator
