"""Vectrax - Modelos de datos del sistema."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SignalCategory(Enum):
    ECONOMICA = "economica"
    SOCIAL = "social"
    LEGAL = "legal"
    TECNICA = "tecnica"
    AMBIENTAL = "ambiental"


class RiskLevel(Enum):
    BAJO = "bajo"
    MODERADO = "moderado"
    ALTO = "alto"
    CRITICO = "critico"


class ProposalStatus(Enum):
    APROBADA = "aprobada"
    RECHAZADA = "rechazada"
    REQUIERE_REVISION = "requiere_revision"


# ---------------------------------------------------------------------------
# Paso 1: Señales
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    """Señal generada por un usuario."""
    user_id: str
    text: str
    category: SignalCategory
    weight: float = 1.0
    timestamp: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        if not 0.0 <= self.weight <= 10.0:
            raise ValueError("El peso debe estar entre 0.0 y 10.0")


# ---------------------------------------------------------------------------
# Paso 2: Convergencia
# ---------------------------------------------------------------------------

@dataclass
class ConvergenceResult:
    """Resultado de detección de convergencia entre señales."""
    converged: bool
    score: float  # 0.0 - 1.0
    cluster_count: int
    dominant_category: Optional[SignalCategory]
    signal_count: int
    details: str = ""


# ---------------------------------------------------------------------------
# Paso 3: Análisis Estructural
# ---------------------------------------------------------------------------

@dataclass
class DimensionScore:
    """Puntuación de una dimensión del análisis estructural."""
    name: str
    score: float  # 0.0 - 1.0
    notes: str = ""


@dataclass
class StructuralAnalysis:
    """Análisis estructural completo por dimensiones."""
    dimensions: list[DimensionScore] = field(default_factory=list)
    overall_score: float = 0.0
    summary: str = ""


# ---------------------------------------------------------------------------
# Paso 4: Validación Constitucional
# ---------------------------------------------------------------------------

@dataclass
class ValidationViolation:
    """Violación detectada contra la Constitución Inmutable."""
    principle: str
    description: str
    severity: RiskLevel


@dataclass
class ValidationResult:
    """Resultado de validación contra la Constitución Inmutable."""
    is_valid: bool
    violations: list[ValidationViolation] = field(default_factory=list)
    checked_principles: int = 0
    details: str = ""


# ---------------------------------------------------------------------------
# Paso 5: Simulación de Impacto
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    """Resultado de un escenario de simulación."""
    name: str  # optimista, base, pesimista
    probability: float
    impact_score: float  # -1.0 a 1.0
    description: str = ""


@dataclass
class SimulationResult:
    """Resultado de simulación de impacto."""
    scenarios: list[ScenarioResult] = field(default_factory=list)
    expected_impact: float = 0.0  # impacto ponderado
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Paso 6: Riesgo Sistémico
# ---------------------------------------------------------------------------

@dataclass
class RiskAssessment:
    """Evaluación de riesgo sistémico."""
    risk_level: RiskLevel
    probability: float  # 0.0 - 1.0
    impact: float  # 0.0 - 1.0
    risk_score: float = 0.0  # probability * impact
    mitigations: list[str] = field(default_factory=list)
    details: str = ""


# ---------------------------------------------------------------------------
# Paso 7: Sostenibilidad
# ---------------------------------------------------------------------------

@dataclass
class SustainabilityScore:
    """Cálculo de sostenibilidad a largo plazo."""
    short_term: float = 0.0  # 0 - 12 meses
    medium_term: float = 0.0  # 1 - 3 años
    long_term: float = 0.0  # 3+ años
    overall: float = 0.0
    is_sustainable: bool = False
    details: str = ""


# ---------------------------------------------------------------------------
# Paso 8: Propuesta Ejecutiva
# ---------------------------------------------------------------------------

@dataclass
class ExecutiveProposal:
    """Propuesta ejecutiva consolidada."""
    id: str
    title: str
    status: ProposalStatus
    convergence: ConvergenceResult
    structural_analysis: StructuralAnalysis
    validation: ValidationResult
    simulation: SimulationResult
    risk: RiskAssessment
    sustainability: SustainabilityScore
    recommendation: str = ""
    generated_at: datetime = field(default_factory=datetime.now)
