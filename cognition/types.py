"""
Vectrax Cognition — Shared types.

Dataclasses and enums used by all four motors.  Kept in a single file
to avoid circular imports across sub-packages.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SignalCategory(str, Enum):
    """Classification category for a perceived signal."""
    CHANGE = "CHANGE"
    ANOMALY = "ANOMALY"
    PATTERN = "PATTERN"
    NOISE = "NOISE"
    CRITICAL = "CRITICAL"


class SignalSource(str, Enum):
    """Origin family of a signal."""
    FILESYSTEM = "filesystem"
    SHELL = "shell"
    DATABASE = "database"
    API = "api"
    WEBHOOK = "webhook"
    CLOUD = "cloud"
    INTERNAL = "internal"


class CognitiveMode(str, Enum):
    """Operational mode of the orchestrator."""
    SILENT_OBSERVATION = "silent_observation"
    SUPERVISED_PROPOSAL = "supervised_proposal"
    # No autonomous mode — by design.


class ProposalStatus(str, Enum):
    """Lifecycle state of a cognitive proposal."""
    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


class ImpactHorizon(str, Enum):
    """Time horizon for impact analysis."""
    SHORT = "short"    # immediate
    MEDIUM = "medium"  # ~7 days
    LONG = "long"      # 30+ days


class MemoryTier(str, Enum):
    """Abstraction level of stored memories."""
    RAW = "raw"              # unprocessed signals
    STRUCTURAL = "structural"  # patterns and relationships
    OPERATIVE = "operative"    # active decisions and principles


class KnowledgeNodeType(str, Enum):
    """Type of node in the knowledge graph."""
    DECISION = "decision"
    PATTERN = "pattern"
    SIGNAL = "signal"
    ERROR = "error"
    PRINCIPLE = "principle"
    CONNECTOR = "connector"
    MODEL_PERFORMANCE = "model_performance"


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CognitiveSignal:
    """A single perceived event, already normalised."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    source: str = ""
    source_type: SignalSource = SignalSource.INTERNAL
    category: SignalCategory = SignalCategory.NOISE
    priority: float = 0.0         # 0-1
    summary: str = ""             # abstract, no raw content
    intent: str = ""              # inferred intent label
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["source_type"] = self.source_type.value
        d["category"] = self.category.value
        return d


@dataclass
class CognitiveContext:
    """Snapshot of the world built by the perception engine."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)
    signals: List[CognitiveSignal] = field(default_factory=list)
    signal_count: int = 0
    dominant_category: Optional[SignalCategory] = None
    avg_priority: float = 0.0
    governor_mode: str = "observe"
    system_health: str = "unknown"
    changes_detected: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "signal_count": self.signal_count,
            "dominant_category": self.dominant_category.value if self.dominant_category else None,
            "avg_priority": round(self.avg_priority, 4),
            "governor_mode": self.governor_mode,
            "system_health": self.system_health,
            "changes_detected": self.changes_detected,
            "metadata": self.metadata,
        }


@dataclass
class ImpactAssessment:
    """Impact evaluation across time horizons."""
    short_term: float = 0.0   # 0-1
    medium_term: float = 0.0
    long_term: float = 0.0
    details: str = ""

    @property
    def overall(self) -> float:
        return round(
            0.5 * self.short_term + 0.3 * self.medium_term + 0.2 * self.long_term,
            4,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "short_term": round(self.short_term, 4),
            "medium_term": round(self.medium_term, 4),
            "long_term": round(self.long_term, 4),
            "overall": self.overall,
            "details": self.details,
        }


@dataclass
class Scenario:
    """One possible outcome of a proposed action."""
    description: str = ""
    probability: float = 0.0   # 0-1
    impact: float = 0.0        # 0-1
    risk: float = 0.0          # 0-1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReasoningResult:
    """Output of the strategic reasoning engine."""
    risk_score: float = 0.0
    risk_level: str = "LOW"
    impact: Optional[ImpactAssessment] = None
    scenarios: List[Scenario] = field(default_factory=list)
    contradictions: List[str] = field(default_factory=list)
    constitutional_pass: bool = True
    constitutional_reasons: List[str] = field(default_factory=list)
    recommendation: str = ""  # "proceed", "review", "block"
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "risk_score": round(self.risk_score, 4),
            "risk_level": self.risk_level,
            "impact": self.impact.to_dict() if self.impact else None,
            "scenarios": [s.to_dict() for s in self.scenarios],
            "contradictions": self.contradictions,
            "constitutional_pass": self.constitutional_pass,
            "constitutional_reasons": self.constitutional_reasons,
            "recommendation": self.recommendation,
            "details": self.details,
        }


@dataclass
class CognitiveProposal:
    """A proposal generated by the cognitive layer for creator review."""
    id: str = field(default_factory=lambda: f"COG-{uuid.uuid4().hex[:8].upper()}")
    timestamp: float = field(default_factory=time.time)
    status: ProposalStatus = ProposalStatus.DRAFT
    description: str = ""
    context_id: str = ""
    reasoning: Optional[ReasoningResult] = None
    actions: List[Dict[str, Any]] = field(default_factory=list)
    decided_at: Optional[float] = None
    execution_result: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "status": self.status.value,
            "description": self.description,
            "context_id": self.context_id,
            "reasoning": self.reasoning.to_dict() if self.reasoning else None,
            "actions": self.actions,
            "decided_at": self.decided_at,
            "execution_result": self.execution_result,
        }


@dataclass
class MemoryContext:
    """Results of a memory query — what the system remembers."""
    related_decisions: List[Dict[str, Any]] = field(default_factory=list)
    related_patterns: List[Dict[str, Any]] = field(default_factory=list)
    related_errors: List[Dict[str, Any]] = field(default_factory=list)
    active_principles: List[Dict[str, Any]] = field(default_factory=list)
    graph_connections: List[Dict[str, Any]] = field(default_factory=list)
    tier: MemoryTier = MemoryTier.STRUCTURAL

    def to_dict(self) -> Dict[str, Any]:
        return {
            "related_decisions": self.related_decisions,
            "related_patterns": self.related_patterns,
            "related_errors": self.related_errors,
            "active_principles": self.active_principles,
            "graph_connections": self.graph_connections,
            "tier": self.tier.value,
        }


@dataclass
class TraceEntry:
    """One step in the cognitive loop trace."""
    step: str = ""
    timestamp: float = field(default_factory=time.time)
    duration_ms: float = 0.0
    input_summary: str = ""
    output_summary: str = ""
    success: bool = True
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
