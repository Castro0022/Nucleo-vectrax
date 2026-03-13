"""
Vectrax Core — Operador Universal
====================================
Capa central que unifica la arquitectura de 12 capas del operador.

Uso rápido::

    from core.operator import get_nucleus, get_identity, get_memory_manifest
    from core.operator import ledger

    # Identidad
    identity = get_identity()
    print(identity.name)        # "Vectrax Core"
    print(identity.creator)     # "Mario Bravo Castro"

    # Núcleo
    nucleus = get_nucleus()
    print(nucleus.status())     # Estado completo del operador
    print(nucleus.check_integrity())

    # Memoria
    manifest = get_memory_manifest()
    print(manifest.cardinal_rule)  # "Nada se borra..."

    # Ledger
    ledger.record_event("test", reason="verificación")

Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from core.operator.identity import (
    IDENTITY,
    OperatorIdentity,
    OperatorMode,
    OperatorPrinciple,
    get_identity,
    verify_identity_integrity,
)
from core.operator.nucleus import (
    LayerDefinition,
    LayerStatus,
    Nucleus,
    get_nucleus,
    reset_nucleus,
)
from core.operator.memory_manifest import (
    MEMORY_MANIFEST,
    MemoryManifest,
    MemoryTier,
    MemoryType,
    get_memory_manifest,
)
from core.operator import ledger_bridge as ledger
from core.operator.perception_bridge import PerceptionBridge, PerceptionResult
from core.operator.relevance_engine import (
    RelevanceEngine,
    RelevanceLevel,
    RelevanceScore,
    get_relevance_engine,
)
from core.operator.risk_bridge import (
    OperatorDecision,
    RiskBridge,
    RiskEvaluation,
    get_risk_bridge,
)
from core.operator.approval_gate import (
    ApprovalGate,
    ApprovalRequest,
    ApprovalStatus,
    get_approval_gate,
)
from core.operator.contracts_reader import (
    ContractAnalysis,
    ContractRiskLevel,
    ContractsReader,
    ContractsSummary,
    get_contracts_reader,
)
from core.operator.universal_bus import (
    BusEvent,
    Channels,
    EventPriority,
    UniversalBus,
    get_universal_bus,
    reset_bus,
)
from core.operator.hypothesis_engine import (
    Evidence,
    EvidenceType,
    Hypothesis,
    HypothesisEngine,
    HypothesisStatus,
    get_hypothesis_engine,
    reset_hypothesis_engine,
)
from core.operator.activation import (
    OperatorCycleResult,
    OperatorDomain,
    OperatorRuntime,
    RestrictedDomain,
    RuntimeState,
    activate_operator,
    get_runtime,
    get_operator_status,
    run_operator_forever,
    reset_runtime,
)
from core.operator.builder import (
    BuilderBridge,
    CodeReader,
    ModuleGenerator,
    ModuleIntegrator,
    PatternExtractor,
    SandboxValidator,
)

__all__ = [
    # Identidad
    "IDENTITY",
    "OperatorIdentity",
    "OperatorMode",
    "OperatorPrinciple",
    "get_identity",
    "verify_identity_integrity",
    # Núcleo
    "LayerDefinition",
    "LayerStatus",
    "Nucleus",
    "get_nucleus",
    "reset_nucleus",
    # Memoria
    "MEMORY_MANIFEST",
    "MemoryManifest",
    "MemoryTier",
    "MemoryType",
    "get_memory_manifest",
    # Ledger
    "ledger",
    # Percepción (FASE 2)
    "PerceptionBridge",
    "PerceptionResult",
    # Relevancia (FASE 2)
    "RelevanceEngine",
    "RelevanceLevel",
    "RelevanceScore",
    "get_relevance_engine",
    # Gobernanza (FASE 3)
    "OperatorDecision",
    "RiskBridge",
    "RiskEvaluation",
    "get_risk_bridge",
    "ApprovalGate",
    "ApprovalRequest",
    "ApprovalStatus",
    "get_approval_gate",
    # Contratos (FASE 5)
    "ContractAnalysis",
    "ContractRiskLevel",
    "ContractsReader",
    "ContractsSummary",
    "get_contracts_reader",
    # Bus Universal (FASE 5)
    "BusEvent",
    "Channels",
    "EventPriority",
    "UniversalBus",
    "get_universal_bus",
    "reset_bus",
    # Hipótesis (FASE 5)
    "Evidence",
    "EvidenceType",
    "Hypothesis",
    "HypothesisEngine",
    "HypothesisStatus",
    "get_hypothesis_engine",
    "reset_hypothesis_engine",
    # Activación (Runtime)
    "OperatorCycleResult",
    "OperatorDomain",
    "OperatorRuntime",
    "RestrictedDomain",
    "RuntimeState",
    "activate_operator",
    "get_runtime",
    "get_operator_status",
    "run_operator_forever",
    "reset_runtime",
    # Builder (Capa 8)
    "BuilderBridge",
    "CodeReader",
    "ModuleGenerator",
    "ModuleIntegrator",
    "PatternExtractor",
    "SandboxValidator",
]
