"""
Vectrax Core — API Models
===========================
Pydantic request/response schemas for the Core API.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_in: int = 86400
    role: str = "viewer"
    channel: str = "user"
    username: str = ""


class RegisterRequest(BaseModel):
    username: str
    password: Optional[str] = None  # None = auto-generate secure temp password
    role: str = "viewer"  # viewer | operator (owner only via seed)
    domain: str = "otro"  # personal | financiero | tecnológico | empresarial | científico | otro


class RegisterResponse(BaseModel):
    id: str
    username: str
    role: str
    channel: str
    message: str = "User created"
    # Independence package
    domain: str = "otro"
    domain_context: str = ""
    domain_rules: List[str] = Field(default_factory=list)
    domain_routes: List[str] = Field(default_factory=list)
    constellation_id: str = ""
    seed_stars_created: int = 0
    portal_url: str = ""
    must_change_password: bool = False
    token: str = ""
    token_expires_in: int = 0
    nucleus_intact: bool = True
    validation_passed: bool = True
    validation_errors: List[str] = Field(default_factory=list)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class UserInfo(BaseModel):
    id: str
    username: str
    role: str
    channel: str
    created_at: float
    is_active: bool


class UserListResponse(BaseModel):
    users: List[UserInfo] = Field(default_factory=list)
    total: int = 0


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    AGENT_REGISTER = "agent.register"
    AGENT_HEARTBEAT = "agent.heartbeat"
    AGENT_STATUS = "agent.status"
    COMMAND_EXECUTED = "command.executed"
    PROPOSAL_CREATED = "proposal.created"
    PROPOSAL_APPLIED = "proposal.applied"
    PROPOSAL_REJECTED = "proposal.rejected"
    ERROR = "error"
    CUSTOM = "custom"


class EventPayload(BaseModel):
    event_type: EventType
    agent_id: str = "local"
    timestamp: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


class EventResponse(BaseModel):
    accepted: bool = True
    event_id: str = ""
    message: str = "Event received"


# ---------------------------------------------------------------------------
# Proposals / Actions
# ---------------------------------------------------------------------------

class ProposeRequest(BaseModel):
    description: str
    model: str = "qwen2.5-coder:7b"
    agent_id: str = "local"


class ProposeResponse(BaseModel):
    proposal_id: str = ""
    description: str = ""
    files_affected: int = 0
    risk_score: Optional[float] = None
    risk_level: Optional[str] = None
    allowed: bool = False
    rationale: str = ""
    diff: str = ""
    requires_confirmation: bool = True


# ---------------------------------------------------------------------------
# Connectors
# ---------------------------------------------------------------------------

class ConnectorInfo(BaseModel):
    name: str
    scopes: List[str] = Field(default_factory=list)
    permissions: List[str] = Field(default_factory=list)
    healthy: bool = False
    version: str = "0.1.0"


class ConnectorListResponse(BaseModel):
    connectors: List[ConnectorInfo] = Field(default_factory=list)
    total: int = 0


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
    env: str = "dev"
    uptime_seconds: float = 0.0
    components: Dict[str, str] = Field(default_factory=dict)
    governor_mode: Optional[str] = None
    governor_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    text: str
    success: bool = False


class SourceItem(BaseModel):
    title: str = ""
    url: str = ""
    snippet: str = ""


class ChatResponse(BaseModel):
    star_id: str = ""
    content: str = ""
    layer: str = "outer"
    gravity_score: float = 0.0
    repetition_count: int = 1
    channel: str = "user"
    owner: str = ""
    is_duplicate: bool = False
    message: str = "Ingested"
    # --- Sovereign response (user-facing) ---
    sovereign_answer: str = ""          # clean answer shown to user
    # --- Internal/debug fields (hidden from user, available for debug) ---
    resolve_mode: str = "memory"        # memory | local | online
    answer: str = ""                    # debug answer text (may list sources)
    sources: List[SourceItem] = Field(default_factory=list)
    context_stars: int = 0              # stars consulted for local resolve
    search_query: str = ""              # query sent to online search
    fallback_from: str = ""             # non-empty if escalated (e.g. "local")


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

class StarResponse(BaseModel):
    id: str
    content: str
    timestamp: float
    layer: str
    gravity_score: float
    repetition_count: int
    success_count: int
    total_count: int
    channel: str
    owner: str


class StarListResponse(BaseModel):
    stars: List[StarResponse] = Field(default_factory=list)
    total: int = 0


class ConstellationResponse(BaseModel):
    id: str
    member_count: int
    coherence_score: float
    repetition_count: int
    success_rate: float
    gravity_score: float
    channel: str
    owner: str


class ConstellationListResponse(BaseModel):
    constellations: List[ConstellationResponse] = Field(default_factory=list)
    total: int = 0


class MemoryStatsResponse(BaseModel):
    stars: int = 0
    constellations: int = 0
    pending_proposals: int = 0
    layers: Dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Proposals (extended)
# ---------------------------------------------------------------------------

class ProposalDetail(BaseModel):
    id: str
    constellation_id: str
    description: str
    evidence: Dict[str, Any] = Field(default_factory=dict)
    status: str
    created_at: float


class ProposalListResponse(BaseModel):
    proposals: List[ProposalDetail] = Field(default_factory=list)
    total: int = 0


class ProposalActionRequest(BaseModel):
    reason: str = ""


class ProposalActionResponse(BaseModel):
    id: str
    status: str
    message: str = ""


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class StatusResponse(BaseModel):
    governor_mode: str = "unknown"
    governor_reason: str = ""
    autopatch_allowed: bool = False
    clean_streak: int = 0
    graph_nodes: int = 0
    graph_edges: int = 0
    graph_components: int = 0
    stars_total: int = 0
    constellations_total: int = 0
    pending_proposals: int = 0


class LayerInfo(BaseModel):
    id: int
    name: str
    status: str
    modules: List[str] = Field(default_factory=list)


class OperatorStatusResponse(BaseModel):
    operator_name: str = "Vectrax"
    mode: str = "guided"
    layers: List[LayerInfo] = Field(default_factory=list)
    total_layers: int = 0
    active_layers: int = 0


class AuditEntryResponse(BaseModel):
    id: int
    timestamp: str
    actor: str
    role: str
    action: str
    decision: str
    reason: str = ""


class AuditListResponse(BaseModel):
    entries: List[AuditEntryResponse] = Field(default_factory=list)
    total: int = 0
