"""
Metrics Collection - Track system performance and usage
Prometheus-style metrics for monitoring
"""
import time
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from collections import defaultdict
from threading import Lock

logger = logging.getLogger(__name__)


@dataclass
class MetricValue:
    """Single metric value with timestamp"""
    value: float
    timestamp: float = field(default_factory=time.time)
    labels: Dict[str, str] = field(default_factory=dict)


class Counter:
    """Counter metric - monotonically increasing value"""
    
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self._value = 0
        self._lock = Lock()
    
    def inc(self, amount: float = 1.0) -> None:
        """Increment counter"""
        with self._lock:
            self._value += amount
    
    def get(self) -> float:
        """Get current value"""
        with self._lock:
            return self._value
    
    def reset(self) -> None:
        """Reset counter to zero"""
        with self._lock:
            self._value = 0


class Gauge:
    """Gauge metric - can go up or down"""
    
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self._value = 0.0
        self._lock = Lock()
    
    def set(self, value: float) -> None:
        """Set gauge value"""
        with self._lock:
            self._value = value
    
    def inc(self, amount: float = 1.0) -> None:
        """Increment gauge"""
        with self._lock:
            self._value += amount
    
    def dec(self, amount: float = 1.0) -> None:
        """Decrement gauge"""
        with self._lock:
            self._value -= amount
    
    def get(self) -> float:
        """Get current value"""
        with self._lock:
            return self._value


class Histogram:
    """Histogram metric - track distribution of values"""
    
    def __init__(self, name: str, description: str = "", buckets: Optional[List[float]] = None):
        self.name = name
        self.description = description
        self.buckets = buckets or [0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0]
        self._observations: List[float] = []
        self._sum = 0.0
        self._count = 0
        self._lock = Lock()
    
    def observe(self, value: float) -> None:
        """Add observation"""
        with self._lock:
            self._observations.append(value)
            self._sum += value
            self._count += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Get histogram statistics"""
        with self._lock:
            if not self._observations:
                return {
                    'count': 0,
                    'sum': 0.0,
                    'avg': 0.0,
                    'min': 0.0,
                    'max': 0.0,
                    'p50': 0.0,
                    'p95': 0.0,
                    'p99': 0.0
                }
            
            sorted_obs = sorted(self._observations)
            count = len(sorted_obs)
            
            return {
                'count': count,
                'sum': self._sum,
                'avg': self._sum / count if count > 0 else 0.0,
                'min': sorted_obs[0],
                'max': sorted_obs[-1],
                'p50': sorted_obs[int(count * 0.50)],
                'p95': sorted_obs[int(count * 0.95)] if count > 1 else sorted_obs[0],
                'p99': sorted_obs[int(count * 0.99)] if count > 1 else sorted_obs[0],
            }


class MetricsCollector:
    """
    Central metrics collector for the system.
    Tracks counters, gauges, and histograms.
    """
    
    def __init__(self):
        self._counters: Dict[str, Counter] = {}
        self._gauges: Dict[str, Gauge] = {}
        self._histograms: Dict[str, Histogram] = {}
        self._lock = Lock()
        
        # Initialize core metrics
        self._init_core_metrics()
    
    def _init_core_metrics(self):
        """Initialize standard metrics"""
        # Request metrics
        self.register_counter(
            "llm_requests_total",
            "Total number of LLM requests"
        )
        self.register_counter(
            "llm_requests_failed",
            "Total number of failed LLM requests"
        )
        self.register_histogram(
            "llm_request_duration_seconds",
            "Duration of LLM requests in seconds"
        )
        self.register_counter(
            "llm_tokens_total",
            "Total number of tokens processed"
        )
        
        # Provider metrics
        self.register_gauge(
            "providers_available",
            "Number of available providers"
        )
        self.register_counter(
            "provider_switches_total",
            "Number of provider switches"
        )
        
        # Workflow metrics
        self.register_counter(
            "workflows_executed_total",
            "Total workflows executed"
        )
        self.register_histogram(
            "workflow_duration_seconds",
            "Duration of workflow execution"
        )
        
        # Circuit breaker metrics
        self.register_gauge(
            "circuit_breaker_open",
            "Number of open circuit breakers"
        )
        
        # Risk engine metrics
        self.register_histogram(
            "risk_score_distribution",
            "Distribution of risk scores per operation"
        )
        self.register_counter(
            "risk_assessments_total",
            "Total number of risk assessments performed"
        )
        
        # Shadow mode metrics
        self.register_histogram(
            "confidence_score_distribution",
            "Distribution of confidence scores in shadow mode"
        )
    
    def register_counter(self, name: str, description: str = "") -> Counter:
        """Register a counter metric"""
        with self._lock:
            if name not in self._counters:
                self._counters[name] = Counter(name, description)
            return self._counters[name]
    
    def register_gauge(self, name: str, description: str = "") -> Gauge:
        """Register a gauge metric"""
        with self._lock:
            if name not in self._gauges:
                self._gauges[name] = Gauge(name, description)
            return self._gauges[name]
    
    def register_histogram(self, name: str, description: str = "", buckets: Optional[List[float]] = None) -> Histogram:
        """Register a histogram metric"""
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = Histogram(name, description, buckets)
            return self._histograms[name]
    
    def get_counter(self, name: str) -> Optional[Counter]:
        """Get counter by name"""
        return self._counters.get(name)
    
    def get_gauge(self, name: str) -> Optional[Gauge]:
        """Get gauge by name"""
        return self._gauges.get(name)
    
    def get_histogram(self, name: str) -> Optional[Histogram]:
        """Get histogram by name"""
        return self._histograms.get(name)
    
    def record_llm_request(
        self,
        duration: float,
        provider: str,
        model: str,
        success: bool = True,
        tokens: Optional[int] = None
    ):
        """
        Record an LLM request.
        
        Args:
            duration: Request duration in seconds
            provider: Provider used
            model: Model used
            success: Whether request succeeded
            tokens: Number of tokens (if known)
        """
        # Increment request counter
        self.get_counter("llm_requests_total").inc()
        
        if not success:
            self.get_counter("llm_requests_failed").inc()
        
        # Record duration
        self.get_histogram("llm_request_duration_seconds").observe(duration)
        
        # Record tokens if provided
        if tokens:
            self.get_counter("llm_tokens_total").inc(tokens)
        
        logger.debug(
            f"Recorded LLM request: provider={provider}, model={model}, "
            f"duration={duration:.3f}s, success={success}"
        )
    
    def record_workflow_execution(self, duration: float, workflow_name: str, success: bool = True):
        """Record workflow execution"""
        self.get_counter("workflows_executed_total").inc()
        self.get_histogram("workflow_duration_seconds").observe(duration)
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all metrics"""
        summary = {
            'counters': {},
            'gauges': {},
            'histograms': {}
        }
        
        # Collect counters
        for name, counter in self._counters.items():
            summary['counters'][name] = counter.get()
        
        # Collect gauges
        for name, gauge in self._gauges.items():
            summary['gauges'][name] = gauge.get()
        
        # Collect histograms
        for name, histogram in self._histograms.items():
            summary['histograms'][name] = histogram.get_stats()
        
        return summary
    
    def reset_all(self):
        """Reset all metrics"""
        for counter in self._counters.values():
            counter.reset()
        
        self._histograms.clear()
        self._init_core_metrics()
        
        logger.info("All metrics reset")
    
    def export_prometheus(self) -> str:
        """Export metrics in Prometheus text format"""
        lines = []
        
        # Export counters
        for name, counter in self._counters.items():
            lines.append(f"# HELP {name} {counter.description}")
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {counter.get()}")
        
        # Export gauges
        for name, gauge in self._gauges.items():
            lines.append(f"# HELP {name} {gauge.description}")
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {gauge.get()}")
        
        # Export histograms
        for name, histogram in self._histograms.items():
            stats = histogram.get_stats()
            lines.append(f"# HELP {name} {histogram.description}")
            lines.append(f"# TYPE {name} histogram")
            lines.append(f"{name}_count {stats['count']}")
            lines.append(f"{name}_sum {stats['sum']}")
        
        return "\n".join(lines)


# Global metrics collector instance
_global_metrics = None

def get_metrics_collector() -> MetricsCollector:
    """Get global metrics collector instance"""
    global _global_metrics
    if _global_metrics is None:
        _global_metrics = MetricsCollector()
    return _global_metrics
