"""
Vectrax Intelligence — Multi-Model Cognitive Routing
=====================================================

Public API:
    IntelligenceRouter       — main orchestrator (route / query_parallel / query_synthesized)
    get_intelligence_router  — module-level singleton accessor

    IntelligenceConnectorRegistry  — provider adapter management
    get_connector_registry         — singleton accessor

    ParallelQueryEngine      — concurrent multi-model querying
    ResponseSynthesizer      — response comparison and synthesis
    PerformanceFeedback      — learning feedback to KnowledgeGraph

Data classes:
    SingleQueryResult        — result of route()
    SynthesizedQueryResult   — result of query_synthesized()
    ParallelQueryResult      — result of query_parallel()
    ProviderResult           — per-provider result in parallel queries
    SynthesizedResult        — synthesis analysis output
    ProviderScore            — per-provider scoring breakdown
"""

from core.intelligence.router import (
    IntelligenceRouter,
    SingleQueryResult,
    SynthesizedQueryResult,
    get_intelligence_router,
)
from core.intelligence.connector_registry import (
    IntelligenceConnectorRegistry,
    ProviderDescriptor,
    get_connector_registry,
)
from core.intelligence.parallel import (
    ParallelQueryEngine,
    ParallelQueryResult,
    ProviderResult,
)
from core.intelligence.synthesizer import (
    ResponseSynthesizer,
    SynthesizedResult,
    ProviderScore,
    DivergenceEntry,
)
from core.intelligence.feedback import PerformanceFeedback

__all__ = [
    # Router
    "IntelligenceRouter",
    "SingleQueryResult",
    "SynthesizedQueryResult",
    "get_intelligence_router",
    # Connector registry
    "IntelligenceConnectorRegistry",
    "ProviderDescriptor",
    "get_connector_registry",
    # Parallel
    "ParallelQueryEngine",
    "ParallelQueryResult",
    "ProviderResult",
    # Synthesizer
    "ResponseSynthesizer",
    "SynthesizedResult",
    "ProviderScore",
    "DivergenceEntry",
    # Feedback
    "PerformanceFeedback",
]
