"""
core/opportunities — primera capa económica de Vectrax.

API pública (estable):
    * `OpportunityService.process_message(...)`        async
    * `OpportunityService.observe_message_sync(...)`   sync bridge
    * `OpportunityService.observe_message_fire_and_forget(...)` no-blocking
    * `build_default_service()` / `get_default_service_sync()`

Backend SQLite-async hoy. Migración a Postgres = cambiar
`OPPORTUNITIES_DB_URL`. El dominio NO depende del backend.
"""
from .domain import (
    EventType,
    IntentType,
    JobStatus,
    OpportunityEvent,
    OpportunityState,
    OpportunityStatus,
    ReactivationJob,
    SourceType,
    Urgency,
    new_uuid,
    slugify_contact,
)
from .detector import (
    CLOSED_SIGNALS,
    DetectionResult,
    OPEN_SIGNALS,
    OpportunityDetector,
)
from .embeddings import (
    CLOSED_EXEMPLARS,
    Encoder,
    EmbeddingScore,
    EmbeddingSignalScorer,
    OPEN_EXEMPLARS,
    SentenceTransformerEncoder,
)
from .llm_scorer import (
    LLMClient,
    LLMScore,
    LLMSignalScorer,
    OpenAILLMClient,
    is_llm_scoring_enabled,
)
from .events import (
    OpportunityEventBus,
    get_event_bus,
    reset_event_bus_for_tests,
)
from .formatter import humanize, humanize_summary_count
from .repository import (
    EventRepository,
    OpportunityRepository,
    ReactivationJobRepository,
    UnitOfWork,
)
from .scheduler import (
    ReactivationScheduler,
    SchedulingPolicy,
    URGENT_DAYS,
    WARM_DAYS,
)
from .service import (
    OpportunityService,
    ProcessResult,
    build_default_service,
    get_default_service_sync,
    reset_singleton_for_tests,
)
from .sqlite_repository import SqliteUnitOfWork
from .state_machine import (
    InvalidTransition,
    assert_transition,
    can_transition,
)

__all__ = [
    # domain
    "EventType",
    "IntentType",
    "JobStatus",
    "OpportunityEvent",
    "OpportunityState",
    "OpportunityStatus",
    "ReactivationJob",
    "SourceType",
    "Urgency",
    "new_uuid",
    "slugify_contact",
    # detector
    "CLOSED_SIGNALS",
    "DetectionResult",
    "OPEN_SIGNALS",
    "OpportunityDetector",
    # embeddings (Fase 2)
    "CLOSED_EXEMPLARS",
    "Encoder",
    "EmbeddingScore",
    "EmbeddingSignalScorer",
    "OPEN_EXEMPLARS",
    "SentenceTransformerEncoder",
    # llm scorer (Fase 3)
    "LLMClient",
    "LLMScore",
    "LLMSignalScorer",
    "OpenAILLMClient",
    "is_llm_scoring_enabled",
    # events
    "OpportunityEventBus",
    "get_event_bus",
    "reset_event_bus_for_tests",
    # formatter
    "humanize",
    "humanize_summary_count",
    # repository protocols
    "EventRepository",
    "OpportunityRepository",
    "ReactivationJobRepository",
    "UnitOfWork",
    # scheduler
    "ReactivationScheduler",
    "SchedulingPolicy",
    "URGENT_DAYS",
    "WARM_DAYS",
    # service
    "OpportunityService",
    "ProcessResult",
    "build_default_service",
    "get_default_service_sync",
    "reset_singleton_for_tests",
    # backend
    "SqliteUnitOfWork",
    # state machine
    "InvalidTransition",
    "assert_transition",
    "can_transition",
]
